# src/write_api/main.py
#
# ActivityStreams Write API – HTTP-rajapinta aktiviteettien vastaanottamiseen
#
# Vastaa:
#   - Like, Dislike, Create (Note), Delete, Update -aktiviteettien vastaanottamisesta
#   - Google OIDC JWT -autentikoinnista (loppukäyttäjä + service-to-service)
#   - Aktiviteettien tallentamisesta kahteen BigQuery-datasetiin:
#       {BQ_DATASET}.objects         → julkinen artikkelidata
#       {BQ_SOCIAL_DATASET}.activities → sosiaalinen lokitaulu (append-only)
#       {BQ_SOCIAL_DATASET}.likes    → tykkayslookup (duplikaattisuojaus, Like + Dislike)
#
# Arkkitehtuuriraja:
#   Kaikki kirjoitukset kahdelle BQ-datasetille tapahtuvat tässä tiedostossa.
#   Query-API (query_api/main.py) on read-only — se ei kirjoita koskaan.
#   og-scraper (og_scraper/) kirjoittaa vain {BQ_DATASET}.objects-tauluun.
#
# Ympäristömuuttujat (pakolliset):
#   GCP_PROJECT, BQ_DATASET, GOOGLE_CLIENT_ID
# Ympäristömuuttujat (valinnaiset):
#   BQ_SOCIAL_DATASET (oletus: activitystreams_social)
#   CLOUD_RUN_SERVICE_URL (service-to-service audience)
#   ALLOW_MOCK_AUTH     (vain yksikkötesteissä — EI SAA KOSKAAN olla true tuotannossa
#                        eikä staging-ympäristössä. Puuttuva muuttuja == "false".
#                        Tarkista terraform/cloud_run.tf: muuttujaa ei pidä olla siellä.)
#
# Muutoshistoria:
#   #33/#48 – Lisätty Dislike-käsittelijä ja toggle-logiikka (Like ↔ Dislike)
import datetime
import json
import logging
import os
from typing import Any, Dict, Optional

import ulid
from fastapi import FastAPI, Header, HTTPException, Response
from google.auth.transport import requests as google_requests
from google.cloud import bigquery
from google.oauth2 import id_token


# Lokitus
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "time": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)

handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
logger = logging.getLogger("write-api")

app = FastAPI(title="ActivityStreams Write API", version="1.0.0")

# Ympäristömuuttujat
PROJECT = os.getenv("GCP_PROJECT")
DATASET = os.getenv("BQ_DATASET")
SOCIAL_DATASET = os.getenv("BQ_SOCIAL_DATASET", "activitystreams_social")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLOUD_RUN_SERVICE_URL = os.getenv("CLOUD_RUN_SERVICE_URL", "")
# ALLOW_MOCK_AUTH luetaan os.getenv:llä suoraan verify_auth_token-funktiossa
# (ei moduulitason vakiota) jotta patch.dict(os.environ) toimii testeissä.
# Python evaluoi moduulitason muuttujat kerran importin yhteydessä, minkä
# jälkeen patch.dict ei vaikuta niihin. os.getenv() funktiossa kutsutaan
# jokaisen HTTP-pyynnön yhteydessä uudelleen, joten ympäristö voidaan
# vaihtaa testissä dynaamisesti.

ALLOWED_AUDIENCES = [a for a in [GOOGLE_CLIENT_ID, CLOUD_RUN_SERVICE_URL] if a]

if not PROJECT or not DATASET:
    logger.critical("Virhe: GCP_PROJECT ja BQ_DATASET ovat pakollisia ympäristömuuttujia.")

bq_client = bigquery.Client(project=PROJECT)


def verify_google_token(token: str, audience: str) -> Optional[Dict[str, Any]]:
    """Validoi Google OIDC-tokenin annetulle audiencelle."""
    try:
        return id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            audience=audience
        )
    except Exception:
        return None


def verify_auth_token(auth_header: Optional[str]) -> str:
    """Validoi Google OIDC JWT-tokenin ja palauttaa käyttäjän sub-tunnisteen.

    Turvallisuushuomiot:
    - exp-tarkistus: google.oauth2.id_token.verify_oauth2_token hylkää
      vanhentuneet tokenit automaattisesti (ValueError 'Token expired').
    - aud-tarkistus: Google tarkistaa että token on tarkoitettu tälle
      palvelulle. Estää token reuse -hyökkäykset.
    - iss-tarkistus: Google hyväksyy vain 'accounts.google.com' ja
      'https://accounts.google.com' — muut issuerit hylätään.
    - sub-kenttä: palautetaan 401 jos puuttuu. Puuttuva sub aiheuttaisi
      muuten KeyError:n actorUrl-rakentamisessa (500-virhe).

    ALLOW_MOCK_AUTH-haara:
    - Ohittaa Google-tokeniverifioinnin kokonaan — sallii "mock-test"-
      tokenin yksikkötesteissä ilman oikeaa GCP-yhteyttä.
    - ALLOW_MOCK_AUTH=true EI SAA olla päällä Cloud Runissa. Jos se on,
      kuka tahansa pääsee kirjoittamaan aktiviteetteja lähettämällä
      Authorization: Bearer mock-test -otsakkeen.
    - Tuotantoympäristössä muuttuja puuttuu kokonaan (default "false").
    """
    if not auth_header or not auth_header.startswith("Bearer "):
        logger.warning("Autentikaatio hylätty: Authorization-otsake puuttuu tai virheellinen")
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header.")

    token = auth_header.split(" ")[1]

    # Tyhjä token Bearer-otsakkeen jälkeen
    if not token:
        logger.warning("Autentikaatio hylätty: Bearer-token on tyhjä")
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header.")

    if os.getenv("ALLOW_MOCK_AUTH", "false").lower() == "true" and token == "mock-test":
        logger.warning("Mock-autentikaatio käytössä — vain kehitysympäristöön")
        return "test-user-sub-12345"

    last_error = None
    for audience in ALLOWED_AUDIENCES:
        try:
            id_info = verify_google_token(token, audience)
            if not id_info:
                raise ValueError("Token verification failed")

            if not id_info.get("email_verified"):
                logger.warning(f"Autentikaatio hylätty: email_verified=false, sub={id_info.get('sub')}")
                raise HTTPException(status_code=403, detail="Email domain must be verified.")

            # sub-kentän puuttuminen aiheuttaisi KeyError:n actorUrl-rakentamisessa.
            # Palautetaan 401 selkeällä viestillä sen sijaan että päästettäisiin
            # läpi ja kaaduttaisiin 500:een myöhemmin.
            sub = id_info.get("sub")
            if not sub:
                logger.warning(
                    f"Autentikaatio hylätty: sub-kenttä puuttuu tokenista, "
                    f"aud={audience}, email={id_info.get('email')}"
                )
                raise HTTPException(status_code=401, detail="Token missing subject claim.")

            logger.info(f"Autentikaatio onnistui: sub={sub}, aud={audience}")
            return sub
        except HTTPException:
            raise
        except Exception as e:
            last_error = e
            continue

    logger.warning(f"Autentikaatio hylätty: tokeni ei kelpaa millekään audience-arvolle. Virhe: {last_error}")
    raise HTTPException(status_code=401, detail="Authentication failed.")


def get_object_by_id(obj_id: str) -> Optional[Dict[str, Any]]:
    """Hakee objektin julkisesta objects-taulusta id:n perusteella."""
    query = f"""
        SELECT id, source, object_json, deleted, like_count, dislike_count
        FROM `{PROJECT}.{DATASET}.objects`
        WHERE id = @id LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", obj_id)]
    )
    try:
        rows = list(bq_client.query(query, job_config=job_config).result())
        if rows:
            row = rows[0]
            obj = json.loads(row["object_json"]) if isinstance(row["object_json"], str) else row["object_json"]
            return {
                "id": row["id"],
                "source": row["source"],
                "deleted": row["deleted"],
                "like_count": row["like_count"],
                "dislike_count": row["dislike_count"],
                "object_json": obj
            }
    except Exception as e:
        logger.error(f"Virhe haettaessa objektia {obj_id}: {e}")
    return None


def get_comments_count_by_actor(actor: str, object_id: str) -> int:
    """Laskee kuinka monta aktiviteettia tietty actor on tehnyt kyseiseen kohteeseen."""
    query = f"""
        SELECT COUNT(*) AS c
        FROM `{PROJECT}.{SOCIAL_DATASET}.activities`
        WHERE actor = @actor AND object_id = @object_id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("actor", "STRING", actor),
            bigquery.ScalarQueryParameter("object_id", "STRING", object_id)
        ]
    )
    try:
        rows = list(bq_client.query(query, job_config=job_config).result())
        return rows[0]["c"] if rows else 0
    except Exception as e:
        logger.error(f"Virhe lasketaessa kommenttien määrää: {e}")
    return 0


def get_existing_reaction(actor: str, object_id: str) -> Optional[str]:
    """Palauttaa käyttäjän olemassa olevan reaktiotyypin ('Like' tai 'Dislike') tai None.

    Toggle-logiikka (#33): käyttäjä voi äänestää vain kerran kerrallaan.
    Jos hän äänestää uudelleen samalla tyypillä, palautetaan sama tyyppi
    (duplikaattitarkistus). Jos hän vaihtaa tyyppiä, vanha poistetaan ja
    uusi tallennetaan.
    """
    query = f"""
        SELECT reaction_type
        FROM `{PROJECT}.{SOCIAL_DATASET}.likes`
        WHERE actor = @actor AND object_id = @object_id
        LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("actor", "STRING", actor),
            bigquery.ScalarQueryParameter("object_id", "STRING", object_id)
        ]
    )
    try:
        rows = list(bq_client.query(query, job_config=job_config).result())
        if rows:
            return rows[0]["reaction_type"]
    except Exception as e:
        logger.error(f"Virhe reaktiotarkistuksessa: {e}")
    return None


def remove_reaction(actor: str, object_id: str) -> None:
    """Poistaa käyttäjän olemassa olevan reaktion likes-taulusta.

    Toggle-logiikka: kutsutaan ennen uuden reaktion tallentamista
    kun käyttäjä vaihtaa Like → Dislike tai päinvastoin.
    BigQuery ei tue DELETE-lausetta stream-insertoiduille riveille
    alle 30 minuutin sisällä — tämä on DML DELETE joka toimii
    normaalisti BigQuery Storage Write API:n kautta.
    """
    query = f"""
        DELETE FROM `{PROJECT}.{SOCIAL_DATASET}.likes`
        WHERE actor = @actor AND object_id = @object_id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("actor", "STRING", actor),
            bigquery.ScalarQueryParameter("object_id", "STRING", object_id)
        ]
    )
    try:
        bq_client.query(query, job_config=job_config).result()
        logger.info(f"Reaktio poistettu: actor={actor}, object_id={object_id}")
    except Exception as e:
        logger.error(f"Virhe reaktion poistamisessa: {e}")
        raise


def handle_reaction(act_type: str, actor_id: str, obj_id: str,
                    target_obj: Dict[str, Any], published_str: str) -> Response:
    """Yhteinen käsittelijä Like- ja Dislike-aktiviteeteille.

    Toggle-logiikka (#33, hallittu AS2-poikkeama kirjattu AS2_CONTRACT.md:hen #54):
    - Sama reaktiotyyppi uudelleen → 200 already_reacted (ei muutosta)
    - Eri reaktiotyyppi → poistetaan vanha, tallennetaan uusi
    - Ei edellistä reaktiota → tallennetaan suoraan

    BigQuery ei tue UNIQUE-rajoitteita — esto toteutetaan sovelluslogiikassa.
    """
    existing = get_existing_reaction(actor_id, obj_id)

    if existing == act_type:
        logger.info(f"Käyttäjä {actor_id} on jo äänestänyt '{act_type}' kohteessa {obj_id}. Ohitetaan.")
        return Response(
            status_code=200,
            content=json.dumps({"status": "already_reacted", "type": act_type}),
            media_type="application/json"
        )

    if existing is not None:
        # Vaihdetaan reaktiotyyppiä — poistetaan vanha ensin
        logger.info(f"Käyttäjä {actor_id} vaihtaa reaktion {existing} → {act_type} kohteessa {obj_id}")
        remove_reaction(actor_id, obj_id)

    act_id = f"https://activitystreams.uutisseuranta.net/ap/activities/{act_type.lower()}s/{ulid.new().str}"
    object_type = target_obj["object_json"].get("type", "Article")

    activities_row = {
        "id": act_id,
        "type": act_type,
        "actor": actor_id,
        "object_id": obj_id,
        "object_type": object_type,
        "object_json": json.dumps({"type": act_type, "id": act_id, "actor": actor_id,
                                    "object": obj_id, "published": published_str}),
        "target_id": None,
        "in_reply_to": None,
        "thread_root": None,
        "published": published_str,
        "received_at": published_str
    }

    likes_row = {
        "activity_id": act_id,
        "actor": actor_id,
        "object_id": obj_id,
        "reaction_type": act_type,
        "published": published_str
    }

    try:
        bq_client.insert_rows_json(f"{PROJECT}.{SOCIAL_DATASET}.activities", [activities_row])
        bq_client.insert_rows_json(f"{PROJECT}.{SOCIAL_DATASET}.likes", [likes_row])
    except Exception as e:
        logger.error(f"Virhe tallennettaessa {act_type}-reaktiota BigQueryyn: {e}")
        raise HTTPException(status_code=500, detail="Database write failed.")

    logger.info(f"{act_type} tallennettu: actor={actor_id}, object={obj_id}, id={act_id}")
    return Response(
        status_code=201,
        content=json.dumps({"id": act_id}),
        media_type="application/json"
    )


@app.post("/ap/activities")
def post_activity(activity: Dict[str, Any], authorization: Optional[str] = Header(None)):
    sub = verify_auth_token(authorization)
    actor_id = f"https://activitystreams.uutisseuranta.net/ap/users/{sub}"
    activity["actor"] = actor_id

    act_type = activity.get("type")
    act_object = activity.get("object")

    if not act_type or not act_object:
        raise HTTPException(status_code=400, detail="Missing 'type' or 'object' in activity.")

    published_str = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

    if act_type in ("Like", "Dislike"):
        obj_id = act_object if isinstance(act_object, str) else act_object.get("id")
        if not obj_id:
            raise HTTPException(status_code=400, detail="Missing object ID for reaction.")

        target_obj = get_object_by_id(obj_id)
        if not target_obj or target_obj["deleted"]:
            raise HTTPException(status_code=404, detail="Target object not found or deleted.")

        return handle_reaction(act_type, actor_id, obj_id, target_obj, published_str)

    elif act_type == "Create":
        if not isinstance(act_object, dict):
            raise HTTPException(status_code=400, detail="Object must be a valid JSON object.")

        obj_type = act_object.get("type")
        in_reply_to = act_object.get("inReplyTo")

        if obj_type != "Note":
            raise HTTPException(status_code=400, detail="Only 'Note' type objects are supported for Create.")

        if not in_reply_to:
            raise HTTPException(status_code=400, detail="'inReplyTo' is required for comment creation.")

        parent = get_object_by_id(in_reply_to)
        if not parent or parent["deleted"]:
            raise HTTPException(status_code=404, detail="Parent object not found or deleted.")

        parent_type = parent["object_json"].get("type")
        thread_root = None

        if parent_type == "Article":
            thread_root = parent["id"]
        elif parent_type == "Note":
            parent_in_reply_to = parent["object_json"].get("inReplyTo")
            grandparent = get_object_by_id(parent_in_reply_to) if parent_in_reply_to else None
            if grandparent and grandparent["object_json"].get("type") == "Note":
                raise HTTPException(status_code=400, detail="Reply thread depth limit exceeded (max 2 levels).")
            thread_root = parent["object_json"].get("thread_root") or parent_in_reply_to
        else:
            raise HTTPException(status_code=400, detail="Unsupported parent object type.")

        obj_id = f"https://activitystreams.uutisseuranta.net/ap/objects/comments/{ulid.new().str}"
        act_object["id"] = obj_id
        act_object["published"] = published_str
        act_object["attributedTo"] = actor_id
        act_object["thread_root"] = thread_root

        act_id = f"https://activitystreams.uutisseuranta.net/ap/activities/creates/{ulid.new().str}"
        activity["id"] = act_id
        activity["object"] = act_object
        activity["published"] = published_str

        activities_row = {
            "id": act_id,
            "type": "Create",
            "actor": actor_id,
            "object_id": obj_id,
            "object_type": "Note",
            "object_json": json.dumps(activity),
            "target_id": None,
            "in_reply_to": in_reply_to,
            "thread_root": thread_root,
            "published": published_str,
            "received_at": published_str
        }

        merge_query = f"""
            MERGE `{PROJECT}.{DATASET}.objects` T
            USING (SELECT @id AS id, @object_json AS object_json, @published AS published) S
            ON T.id = S.id
            WHEN MATCHED THEN UPDATE SET
              T.object_json = PARSE_JSON(S.object_json),
              T.updated = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT
              (id, source, published, updated, tags, tags_enriched, like_count, dislike_count, deleted, object_json)
              VALUES (S.id, 'user', S.published, CURRENT_TIMESTAMP(), [], FALSE, 0, 0, FALSE, PARSE_JSON(S.object_json));
        """
        merge_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("id", "STRING", obj_id),
                bigquery.ScalarQueryParameter("object_json", "STRING", json.dumps(act_object)),
                bigquery.ScalarQueryParameter("published", "TIMESTAMP", datetime.datetime.now(datetime.timezone.utc))
            ]
        )

        try:
            bq_client.insert_rows_json(f"{PROJECT}.{SOCIAL_DATASET}.activities", [activities_row])
            bq_client.query(merge_query, job_config=merge_config).result()
        except Exception as e:
            logger.error(f"Virhe kommentin tallennuksessa: {e}")
            raise HTTPException(status_code=500, detail="Database write failed.")

        return Response(status_code=201, content=json.dumps({"id": act_id, "object_id": obj_id}), media_type="application/json")

    elif act_type == "Delete":
        obj_id = act_object if isinstance(act_object, str) else act_object.get("id")
        if not obj_id:
            raise HTTPException(status_code=400, detail="Missing object ID for Delete.")

        target_obj = get_object_by_id(obj_id)
        if not target_obj:
            raise HTTPException(status_code=404, detail="Object not found.")

        if target_obj["deleted"]:
            return {"status": "already_deleted", "id": obj_id}

        owner_id = target_obj["object_json"].get("attributedTo")
        if owner_id != actor_id:
            raise HTTPException(status_code=403, detail="You do not have permission to delete this object.")

        act_id = f"https://activitystreams.uutisseuranta.net/ap/activities/deletes/{ulid.new().str}"
        activity["id"] = act_id
        activity["published"] = published_str

        activities_row = {
            "id": act_id,
            "type": "Delete",
            "actor": actor_id,
            "object_id": obj_id,
            "object_type": target_obj["object_json"].get("type", "Note"),
            "object_json": json.dumps(activity),
            "target_id": None,
            "in_reply_to": None,
            "thread_root": None,
            "published": published_str,
            "received_at": published_str
        }

        update_query = f"""
            UPDATE `{PROJECT}.{DATASET}.objects`
            SET deleted = TRUE, updated = CURRENT_TIMESTAMP()
            WHERE id = @id
        """
        update_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", obj_id)]
        )

        try:
            bq_client.insert_rows_json(f"{PROJECT}.{SOCIAL_DATASET}.activities", [activities_row])
            bq_client.query(update_query, job_config=update_config).result()
        except Exception as e:
            logger.error(f"Virhe objektin poistamisessa BigQuerystä: {e}")
            raise HTTPException(status_code=500, detail="Database write failed.")

        return {"status": "deleted", "id": act_id}

    elif act_type == "Update":
        if not isinstance(act_object, dict):
            raise HTTPException(status_code=400, detail="Object must be a valid JSON object.")

        obj_id = act_object.get("id")
        if not obj_id:
            raise HTTPException(status_code=400, detail="Missing object ID for Update.")

        target_obj = get_object_by_id(obj_id)
        if not target_obj or target_obj["deleted"]:
            raise HTTPException(status_code=404, detail="Object not found or deleted.")

        owner_id = target_obj["object_json"].get("attributedTo")
        if owner_id != actor_id:
            raise HTTPException(status_code=403, detail="You do not have permission to update this object.")

        act_id = f"https://activitystreams.uutisseuranta.net/ap/activities/updates/{ulid.new().str}"
        activity["id"] = act_id
        activity["published"] = published_str

        updated_object = target_obj["object_json"].copy()
        updated_object["name"] = act_object.get("name", updated_object.get("name"))
        updated_object["summary"] = act_object.get("summary", updated_object.get("summary"))
        updated_object["content"] = act_object.get("content", updated_object.get("content"))
        updated_object["updated"] = published_str

        activities_row = {
            "id": act_id,
            "type": "Update",
            "actor": actor_id,
            "object_id": obj_id,
            "object_type": "Note",
            "object_json": json.dumps(activity),
            "target_id": None,
            "in_reply_to": None,
            "thread_root": None,
            "published": published_str,
            "received_at": published_str
        }

        merge_query = f"""
            MERGE `{PROJECT}.{DATASET}.objects` T
            USING (SELECT @id AS id, @object_json AS object_json) S
            ON T.id = S.id
            WHEN MATCHED THEN UPDATE SET
              T.object_json = PARSE_JSON(S.object_json),
              T.updated = CURRENT_TIMESTAMP()
        """
        merge_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("id", "STRING", obj_id),
                bigquery.ScalarQueryParameter("object_json", "STRING", json.dumps(updated_object))
            ]
        )

        try:
            bq_client.insert_rows_json(f"{PROJECT}.{SOCIAL_DATASET}.activities", [activities_row])
            bq_client.query(merge_query, job_config=merge_config).result()
        except Exception as e:
            logger.error(f"Virhe päivitettäessä kommenttia: {e}")
            raise HTTPException(status_code=500, detail="Database write failed.")

        return {"status": "updated", "id": act_id}

    else:
        raise HTTPException(status_code=400, detail=f"Activity type '{act_type}' is not supported.")


@app.get("/healthz")
def liveness():
    return {"status": "ok"}


@app.get("/readyz")
def readiness():
    try:
        bq_client.list_datasets(max_results=1)
        return {"status": "ready"}
    except Exception as e:
        logger.error(f"Readiness-tarkistus epäonnistui: {e}")
        raise HTTPException(status_code=503, detail="Database connection failed")

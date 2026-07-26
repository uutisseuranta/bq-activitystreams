import datetime
import json
import logging
import os
import time
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Query, Response
from google.cloud import bigquery


# JsonFormatter toistuu identtisenä og_enrichment_job-, og_scraper- ja query_api-palveluissa.
# Tämä on tietoinen arkkitehtuuripäätös: Cloud Run -palvelut ovat toisistaan riippumattomia
# deployable-yksikköjä. Jakaminen shared/-moduuliin lisäisi build-riippuvuuden ilman selvää hyötyä,
# koska formatter on yksinkertainen (~10 riviä) eikä muutu usein.
# Päätös kirjattu: TECHNICAL_DESIGN.md §4 "Suunnittelu- ja kehityskäytännöt".
class JsonFormatter(logging.Formatter):
    def format(self, record):
        # Cloud Logging tunnistaa 'severity'-kentän automaattisesti logtasoksi
        log_entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            # ISO 8601 UTC — Cloud Logging edellyttää Z-päätettä (ei +00:00)
            "time": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)

handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
logger = logging.getLogger("query-api")

app = FastAPI(title="ActivityStreams Query API", version="1.0.0")

# Globaalit ympäristömuuttujat — luetaan kerran käynnistyksen yhteydessä
PROJECT = os.getenv("GCP_PROJECT")
DATASET = os.getenv("BQ_DATASET")
LOCATION = os.getenv("BQ_LOCATION", "europe-north1")

if not PROJECT or not DATASET:
    logger.critical("Virhe: GCP_PROJECT ja BQ_DATASET ympäristömuuttujat ovat pakollisia.")

bq_client = bigquery.Client(project=PROJECT)

# In-memory cache totalItems-laskurille
# Rakenne: { "tag1,tag2": { "value": int, "expires": float } }
# Syy: COUNT(*)-kysely on kallis (full table scan), mutta arvo ei muutu sekunnin välein.
# TTL=300s on kompromissi tuoreuden ja BQ-kustannusten välillä.
# HUOM: cache on prosessikohtainen — Cloud Run -instanssien välillä ei jaeta cachea.
_count_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL = 300  # sekuntia (5 minuuttia)


def get_total_items_cached(tags: List[str]) -> int:
    """Laskee objektien kokonaismäärän välimuistia hyödyntäen."""
    now = time.time()
    # Lajitellaan tagit — cache-avain on järjestyksestä riippumaton
    cache_key = ",".join(sorted(tags))

    cached = _count_cache.get(cache_key)
    if cached and cached["expires"] > now:
        return cached["value"]

    query = f"""
        SELECT COUNT(*) AS c
        FROM `{PROJECT}.{DATASET}.objects`
        WHERE deleted = FALSE
          AND EXISTS (
            SELECT 1 FROM UNNEST(tags) t WHERE t IN UNNEST(@search_tags)
          )
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("search_tags", "STRING", tags)]
    )

    try:
        results = list(bq_client.query(query, job_config=job_config).result())
        count = results[0]["c"] if results else 0
    except Exception as e:
        logger.error(f"Virhe laskettaessa totalItems-arvoa: {e}")
        # Palautetaan vanha cache-arvo jos käytettävissä, muuten 0
        return cached["value"] if cached else 0

    _count_cache[cache_key] = {
        "value": count,
        "expires": now + CACHE_TTL
    }
    return count


@app.get("/ap/outbox")
def get_outbox(
    tag: List[str] = Query(default=None, description="Haettavat tagit (toistuva parametri)"),
    n: int = Query(default=50, description="Palautettavien kohteiden määrä (1-500)")
):
    # 1. Validoidaan tagit
    if not tag:
        raise HTTPException(
            status_code=400,
            detail="At least one 'tag' query parameter is required."
        )

    # 2. Validoidaan n-parametri (1-500)
    if n <= 0 or n > 500:
        raise HTTPException(
            status_code=400,
            detail="Parameter 'n' must be between 1 and 500."
        )

    # Normalisoidaan tagit: pieniksi kirjaimiksi, välilyönnit siivotaan
    search_tags = [t.strip().lower() for t in tag if t.strip()]
    if not search_tags:
        raise HTTPException(
            status_code=400,
            detail="Valid tags must be provided."
        )

    logger.info(f"Haku tageilla: {search_tags}, koko n: {n}")

    # 3. BigQuery-haku relevanssipisteytyksen mukaan
    # Relevanssi = osuvien hakutagien lukumäärä artikkelin tagien joukossa.
    # Esimerkki: haku ["politiikka", "EU"], artikkeli jolla molemmat tagit saa relevance=2.
    # Tasatilanne ratkaistaan: like_count DESC → updated DESC → published DESC → id ASC.
    query = f"""
        SELECT
          id,
          source,
          published,
          updated,
          like_count,
          dislike_count,
          object_json,
          (
            SELECT COUNT(*)
            FROM UNNEST(tags) t
            WHERE t IN UNNEST(@search_tags)
          ) AS relevance
        FROM `{PROJECT}.{DATASET}.objects`
        WHERE deleted = FALSE
          AND EXISTS (
            SELECT 1 FROM UNNEST(tags) t WHERE t IN UNNEST(@search_tags)
          )
        ORDER BY relevance DESC, like_count DESC, updated DESC, published DESC NULLS LAST, id ASC
        LIMIT @limit_n
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("search_tags", "STRING", search_tags),
            bigquery.ScalarQueryParameter("limit_n", "INT64", n)
        ]
    )

    try:
        query_job = bq_client.query(query, job_config=job_config)
        rows = list(query_job.result())
    except Exception as e:
        logger.error(f"BigQuery-haku epäonnistui: {e}")
        raise HTTPException(status_code=500, detail="Database query failed.")

    # 4. Injektoidaan dynaamiset kentät (like_count, dislike_count, updated) AS2-dokumentteihin
    # Nämä kentät elävät BQ-riveillä erillään object_json:sta jotta ne ovat helposti
    # päivitettävissä ilman koko JSON-dokumentin uudelleenkirjoitusta.
    ordered_items = []
    for row in rows:
        obj_json_raw = row["object_json"]
        if not obj_json_raw:
            continue

        try:
            # BigQuery JSON -kenttä voi tulla dictinä tai merkkijonona — käsitellään molemmat
            obj = json.loads(obj_json_raw) if isinstance(obj_json_raw, str) else obj_json_raw

            # @context laajennetaan kaksialkioiseksi listaksi:
            # 1. AS2 ydinontologia (pakollinen, W3C AS2 §2.1)
            # 2. uutisseuranta-nimiavaruus — projektikohtaiset laajennukset (dislikes, agreeCount)
            #    Namespace on IRI-pohjainen AS2-spesifikaation mukaisesti; välttää konfliktit
            #    tulevien AS2-laajennusten kanssa. Ks. AS2_CONTRACT.md (tuleva: #54) ja issue #33.
            obj["@context"] = [
                "https://www.w3.org/ns/activitystreams",
                {"_uutisseuranta": "https://uutisseuranta.net/ns#"}
            ]

            # likes: AS2 Core §5.7 -kenttä, palautetaan Collection-muodossa (ei kokonaisluku).
            # totalItems riittää — koko aktiviteettilistan palauttaminen olisi liian raskas.
            obj["likes"] = {
                "type": "Collection",
                "totalItems": row["like_count"]
            }

            # dislikes: projektikohtainen laajennus — ei AS2 Core -kenttä (toisin kuin 'likes').
            # Kirjattu hallittuna laajennuksena AS2_CONTRACT.md:hen (#54).
            # Collection-rakenne on yhdenmukainen AS2 Core §5.7 'likes'-käytännön kanssa.
            obj["dislikes"] = {
                "type": "Collection",
                "totalItems": row["dislike_count"]
            }

            # agreeCount = likes + dislikes (kaikki reaktiot yhteensä).
            # Tarkoitus: frontend näyttää yhteenlasketun reaktiomäärän ilman asiakaspuolen laskentaa.
            # Invariantti: arvo on oikein vain jos write-api estää duplikaattiäänet per käyttäjä.
            # Toggle-logiikka ja duplikaattiesto on ratkaistu write-apissa (poistetaan vanha ja lisätään uusi).
            # Hallittu AS2-poikkeama: toggle ei kirjaa 'Undo Like' -aktiviteettia — ks. AS2_CONTRACT.md (#54).
            obj["_uutisseuranta:agreeCount"] = row["like_count"] + row["dislike_count"]

            if row["updated"]:
                updated_dt = row["updated"]
                if isinstance(updated_dt, datetime.datetime):
                    # Muutetaan ISO UTC Z-muotoon AS2-standardin mukaisesti
                    obj["updated"] = updated_dt.isoformat().replace("+00:00", "Z")
                else:
                    obj["updated"] = str(updated_dt)

            ordered_items.append(obj)
        except Exception as e:
            logger.error(f"Virhe objektin {row['id']} parsimisessa: {e}")

    # 5. totalItems — cachetettu COUNT-kysely (ks. get_total_items_cached)
    total_items = get_total_items_cached(search_tags)

    # 6. Rakennetaan self-URL AS2 OrderedCollection id-kenttään
    base_url = "https://activitystreams.uutisseuranta.net/ap/outbox"
    tag_params = "&".join(f"tag={t}" for t in search_tags)
    self_url = f"{base_url}?{tag_params}&n={n}"

    response_json = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "type": "OrderedCollection",
        "id": self_url,
        "totalItems": total_items,
        "orderedItems": ordered_items
    }

    # application/activity+json on ActivityPub-yhteensopiva Content-Type
    # (application/ld+json; profile="..." olisi tiukempi AS2, mutta activity+json on laajemmin tuettu)
    return Response(
        content=json.dumps(response_json, ensure_ascii=False),
        media_type="application/activity+json; charset=utf-8"
    )


@app.get("/healthz")
def liveness():
    # Cloud Run liveness-probe — vastaa aina 200 OK jos prosessi on elossa
    return {"status": "ok"}


@app.get("/readyz")
def readiness():
    try:
        # Aktiivinnen BQ-yhteystarkistus: list_datasets on kevyt API-kutsu
        # joka vahvistaa sekä autentikaation että verkkoyhteyden toimivuuden
        bq_client.list_datasets(max_results=1)
        return {"status": "ready"}
    except Exception as e:
        logger.error(f"Readiness-tarkistus epäonnistui: {e}")
        raise HTTPException(status_code=503, detail="Database connection failed")

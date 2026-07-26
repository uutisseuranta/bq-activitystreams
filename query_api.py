# Käyttötapaus UC-5: Uutisvirran lukeminen (Outbox) — ks. TECHNICAL_DESIGN.md
import base64
import contextvars
import datetime
import json
import os
import time
import urllib.parse
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, BackgroundTasks
from gcp_logging import get_logger
from google.auth.transport import requests as google_requests
from google.cloud import bigquery
from google.oauth2 import id_token
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

logger = get_logger("query-api")

current_request = contextvars.ContextVar("current_request")

def get_query_user_or_ip(request: Request) -> str:
    current_request.set(request)
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        try:
            token = auth_header.split(" ")[1]
            if token == "mock-test":
                return "uid:test-user-sub-12345"
            payload_part = token.split(".")[1]
            payload_part += "=" * ((4 - len(payload_part) % 4) % 4)
            payload = json.loads(base64.b64decode(payload_part).decode("utf-8"))
            sub = payload.get("sub")
            if sub:
                return f"uid:{sub}"
        except Exception:
            pass
    return get_remote_address(request)

def get_outbox_limit() -> str:
    req = current_request.get(None)
    if req:
        key = get_query_user_or_ip(req)
        if key.startswith("uid:"):
            return "120/minute"
    return "60/minute"

limiter = Limiter(key_func=get_query_user_or_ip)
app = FastAPI(title="ActivityStreams Query API", version="1.0.0")
app.state.limiter = limiter

@app.middleware("http")
async def add_request_context_middleware(request: Request, call_next):
    current_request.set(request)
    response = await call_next(request)
    return response

@app.exception_handler(RateLimitExceeded)
async def custom_rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    response = Response(
        status_code=429,
        content=json.dumps({"error": f"Rate limit exceeded: {exc.detail}"}),
        media_type="application/json"
    )
    response.headers["Retry-After"] = "60"
    return response

# Google OIDC tokenin vahvistusfunktiot
def verify_google_token(token: str, audience: str) -> Optional[Dict[str, Any]]:
    try:
        return id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            audience=audience
        )
    except Exception:
        return None

def verify_auth_token_optional(auth_header: Optional[str]) -> Optional[str]:
    if not auth_header:
        return None
    if not auth_header.startswith("Bearer "):
        logger.warning("Autentikaatio hylätty: virheellinen Authorization-formaatti")
        raise HTTPException(status_code=401, detail="Invalid Authorization header format.")

    token = auth_header.split(" ")[1]
    allow_mock = os.getenv("ALLOW_MOCK_AUTH", "false").lower() == "true"
    if allow_mock and token == "mock-test":
        # ALLOW_MOCK_AUTH=true sallitaan vain kehitys- ja testiympäristöissä
        logger.warning("Mock-autentikaatio käytössä — vain kehitysympäristöön")
        return "test-user-sub-12345"

    project_id = os.getenv("GCP_PROJECT", "uutisseuranta-activitystreams")
    svc_url = os.getenv("CLOUD_RUN_SERVICE_URL", "")
    allowed_audiences = [a for a in [project_id, svc_url] if a]

    for aud in allowed_audiences:
        payload = verify_google_token(token, aud)
        if payload:
            sub = payload.get("sub")
            if not sub:
                raise HTTPException(status_code=401, detail="Token lacks 'sub' claim.")
            return sub

    logger.warning("Autentikaatio hylätty: OIDC-tokenia ei voitu vahvistaa")
    raise HTTPException(status_code=401, detail="Invalid OIDC token.")

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
    """Laskee kokonaismäärän välimuistia hyödyntäen (COUNT(*) per tag-yhdistelmä)."""
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
@limiter.limit(get_outbox_limit)
def get_outbox(
    request: Request,
    tag: List[str] = Query(default=None, description="Haettavat tagit (toistuva parametri)"),
    n: int = Query(default=50, description="Palautettavien kohteiden määrä (1-500)"),
    authorization: Optional[str] = Header(None)
):
    # Verifioidaan token jos sellainen on toimitettu
    verify_auth_token_optional(authorization)
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

    # Normalisoidaan tagit: pieniksi kirjaimiksi ja varmistetaan #-etuliite (päätös L-011)
    # Sallitaan sekä "politiikka" että "#politiikka" — molemmat normalisoidaan muotoon "#politiikka"
    search_tags = []
    for t in tag:
        val = t.strip().lower()
        if val:
            if not val.startswith("#"):
                val = f"#{val}"
            search_tags.append(val)
    if not search_tags:
        raise HTTPException(
            status_code=400,
            detail="Valid tags must be provided."
        )

    logger.info(f"Haku tageilla: {search_tags}, koko n: {n}")

    # 3. BigQuery-haku relevanssipisteytyksen mukaan
    # Relevanssi = osuvien hakutagien lukumäärä artikkelin tagien joukossa.
    # Esimerkki: haku ["#politiikka", "#EU"], artikkeli jolla molemmat tagit saa relevance=2.
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

    # 4. Injektoidaan dynaamiset kentät (like_count, dislike_count, updated) AS2-dokumentteihin.
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
            # 2. uutisseuranta-nimiavaruus — projektikohtaiset laajennukset (dislikes, reactionCount)
            #    Namespace on IRI-pohjainen AS2-spesifikaation mukaisesti; välttää konfliktit
            #    tulevien AS2-laajennusten kanssa. Ks. AS2_CONTRACT.md ja issue #33.
            obj["@context"] = [
                "https://www.w3.org/ns/activitystreams",
                {
                    "_uutisseuranta": "https://uutisseuranta.net/ns#",
                    "dislikes": "_uutisseuranta:dislikes"
                }
            ]

            # likes: AS2 Core §5.7 -kenttä, palautetaan Collection-muodossa (ei kokonaisluku).
            # totalItems riittää — koko aktiviteettilistan palauttaminen olisi liian raskas.
            obj["likes"] = {
                "type": "Collection",
                "totalItems": row["like_count"]
            }

            # dislikes: projektikohtainen laajennus — ei AS2 Core -kenttä (toisin kuin 'likes').
            # Kirjattu hallittuna laajennuksena AS2_CONTRACT.md:hen.
            # Collection-rakenne on yhdenmukainen AS2 Core §5.7 'likes'-käytännön kanssa.
            obj["dislikes"] = {
                "type": "Collection",
                "totalItems": row["dislike_count"]
            }

            # reactionCount = likes + dislikes (kaikki reaktiot yhteensä).
            # Neutraali nimitys: sisältää sekä Agree (Like) että Disagree (Dislike) -reaktiot.
            # Tarkoitus: frontend näyttää yhteenlasketun reaktiomäärän ilman asiakaspuolen laskentaa.
            # Invariantti: arvo on oikein vain jos write-api estää duplikaattiäänet per käyttäjä.
            # Toggle-logiikka ja duplikaattiesto on ratkaistu write-apissa (poistetaan vanha, lisätään uusi).
            # Hallittu AS2-poikkeama: toggle ei kirjaa 'Undo Like' -aktiviteettia — ks. AS2_CONTRACT.md §4.
            obj["_uutisseuranta:reactionCount"] = row["like_count"] + row["dislike_count"]

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
    import urllib.parse
    base_url = "https://activitystreams.uutisseuranta.net/ap/outbox"
    tag_params = "&".join(f"tag={urllib.parse.quote(t)}" for t in search_tags)
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


def update_archive_url_in_bq(url: str, archive_url: str):
    """Päivitetään uutisen arkistolinkki BigQueryyn taustatehtävänä (BackgroundTasks)."""
    try:
        # JSON_SET vaatii JSON-tyyppisen arvon, joten PARSE_JSON muuntaa merkkijonon BigQueryssä oikein.
        query = f"""
            UPDATE `{PROJECT}.{DATASET}.objects`
            SET object_json = JSON_SET(object_json, '$.url_archive', PARSE_JSON(@archive_url)),
                updated = CURRENT_TIMESTAMP()
            WHERE JSON_VALUE(object_json.url) = @url
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("archive_url", "STRING", json.dumps(archive_url)),
                bigquery.ScalarQueryParameter("url", "STRING", url)
            ]
        )
        bq_client.query(query, job_config=job_config).result()
        logger.info(f"Päivitetty uutisen {url} arkistolinkki BigQueryyn: {archive_url}")
    except Exception as e:
        logger.error(f"Virhe päivitettäessä arkistolinkkiä uutiselle {url}: {e}")


@app.get("/ap/check-status")
async def check_status(url: str, background_tasks: BackgroundTasks):
    """Tarkistaa onko artikkelilinkki tavoitettavissa ja tallentaa virhetilanteessa arkistolinkin BigQueryyn."""
    try:
        parsed = urllib.parse.urlparse(url)
        is_invalid_scheme = parsed.scheme not in ("http", "https") or not parsed.netloc
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid URL.")

    if is_invalid_scheme:
        raise HTTPException(status_code=400, detail="Invalid URL scheme.")

    alive = False
    try:
        async with httpx.AsyncClient(timeout=2.0, follow_redirects=True) as client:
            # Yritetään kevyempää HEAD-pyyntöä ensin
            response = await client.head(url)
            if response.status_code in (405, 501):
                response = await client.get(url)
            alive = response.status_code < 400
    except Exception as e:
        logger.warning(f"Uutissivun {url} ping-tarkistus epäonnistui: {e}")
        alive = False

    # Jos sivu on alhaalla, päivitetään BigQueryyn arkisto-URL taustatehtävänä
    if not alive:
        archive_url = f"https://web.archive.org/web/*/{url}"
        background_tasks.add_task(update_archive_url_in_bq, url, archive_url)

    return {"alive": alive}


@app.get("/healthz")
def liveness():
    # Cloud Run liveness-probe — vastaa aina 200 OK jos prosessi on elossa
    return {"status": "ok"}


@app.get("/readyz")
def readiness():
    try:
        # Aktiivinen BQ-yhteystarkistus: list_datasets on kevyt API-kutsu
        # joka vahvistaa sekä autentikaation että verkkoyhteyden toimivuuden
        bq_client.list_datasets(max_results=1)
        return {"status": "ready"}
    except Exception as e:
        logger.error(f"Readiness-tarkistus epäonnistui: {e}")
        raise HTTPException(status_code=503, detail="Database connection failed")

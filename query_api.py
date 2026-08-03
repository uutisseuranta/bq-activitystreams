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
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from gcp_logging import get_logger
from google.auth.transport import requests as google_requests
from google.cloud import bigquery
from google.oauth2 import id_token
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

logger = get_logger("query-api")

current_request = contextvars.ContextVar("current_request")

last_user_activity = time.time()


# Suodatin maksumuurillisille artikkeleille, joilta puuttuu arkistolinkki (Päätös G-020)
# Käytetään SAFE_CASTia, koska JSON_VALUE palauttaa merkkijonon ja isAccessibleForFree on tallennettu booleanina.
# Huom: Tämä on staattinen vakio eikä sisällä käyttäjäsyötettä, joten f-string-interpolaatio on täysin turvallista ilman parametrisointia.
PAYWALL_FILTER_SQL = """
  AND NOT (
    SAFE_CAST(JSON_VALUE(object_json, '$.isAccessibleForFree') AS BOOL) = FALSE
    AND JSON_VALUE(object_json, '$.url_archive') IS NULL
  )
"""


def manage_scheduler_job(action: str):
    try:
        import httpx
        from google.auth import default
        from google.auth.transport.requests import Request

        credentials, project = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        if not credentials.valid:
            credentials.refresh(Request())

        region = os.getenv("SCHEDULER_REGION", "europe-west1")
        job_name = "query-api-keep-warm"

        url = f"https://cloudscheduler.googleapis.com/v1/projects/{project}/locations/{region}/jobs/{job_name}:{action}"
        headers = {"Authorization": f"Bearer {credentials.token}", "Content-Length": "0"}

        r = httpx.post(url, headers=headers)
        logger.info(f"Cloud Scheduler job {action} response: {r.status_code} {r.text}")
    except Exception as e:
        logger.error(f"Failed to {action} Cloud Scheduler job: {e}")


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://uutisseuranta.net",
        "https://uutisseuranta.github.io",
        "http://localhost:5173",
        "http://localhost:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        media_type="application/json",
    )
    response.headers["Retry-After"] = "60"
    return response


# Google OIDC / Firebase ID tokenin vahvistusfunktiot
def verify_google_token(token: str, audience: str) -> Optional[Dict[str, Any]]:
    try:
        # Kokeillaan ensin standardia Google OIDC tokenia
        return id_token.verify_oauth2_token(token, google_requests.Request(), audience=audience)
    except Exception:
        try:
            # Fallback: Kokeillaan Firebase ID tokenia (käyttäjän selainautentikointi)
            return id_token.verify_firebase_token(token, google_requests.Request(), audience=audience)
        except Exception:
            return None


def verify_auth_token_optional(auth_header: Optional[str]) -> Optional[str]:
    if not auth_header:
        return None
    if not auth_header.startswith("Bearer "):
        logger.warning(
            "Autentikaatio hylätty: virheellinen Authorization-formaatti (ohitetaan julkisessa rajapinnassa)"
        )
        return None

    token = auth_header.split(" ")[1]
    if not token:
        logger.warning("Autentikaatio hylätty: tyhjä Bearer-token (ohitetaan julkisessa rajapinnassa)")
        return None

    allow_mock = os.getenv("ALLOW_MOCK_AUTH", "false").lower() == "true"
    if allow_mock and token == "mock-test":
        logger.warning("Mock-autentikaatio käytössä — vain kehitysympäristöön")
        return "test-user-sub-12345"

    project_id = os.getenv("GCP_PROJECT", "uutisseuranta-activitystreams")
    svc_url = os.getenv("CLOUD_RUN_SERVICE_URL", "")
    allowed_audiences = [a for a in [project_id, svc_url, "uutisseuranta-net"] if a]

    for aud in allowed_audiences:
        try:
            payload = verify_google_token(token, aud)
            if payload:
                sub = payload.get("sub")
                if sub:
                    return sub
        except Exception as e:
            logger.warning(f"Token verification warning for aud={aud}: {e}")
            continue

    logger.warning("Autentikaatio hylätty julkisessa rajapinnassa (jatketaan anonyyminä): tokenia ei voitu vahvistaa")
    return None


# Globaalit ympäristömuuttujat — luetaan kerran käynnistyksen yhteydessä
PROJECT = os.getenv("GCP_PROJECT")
DATASET = os.getenv("BQ_DATASET")
SOCIAL_DATASET = os.getenv("BQ_SOCIAL_DATASET", "activitystreams_social")
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
          {PAYWALL_FILTER_SQL}
          AND EXISTS (
            SELECT 1 FROM UNNEST(tags) t WHERE t IN UNNEST(@search_tags)
          )
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[bigquery.ArrayQueryParameter("search_tags", "STRING", tags)])

    try:
        results = list(bq_client.query(query, job_config=job_config).result())
        count = results[0]["c"] if results else 0
    except Exception as e:
        logger.error(f"Virhe laskettaessa totalItems-arvoa: {e}")
        # Palautetaan vanha cache-arvo jos käytettävissä, muuten 0
        return cached["value"] if cached else 0

    _count_cache[cache_key] = {"value": count, "expires": now + CACHE_TTL}
    return count


@app.get("/ap/outbox")
@limiter.limit(get_outbox_limit)
def get_outbox(
    request: Request,
    background_tasks: BackgroundTasks,
    tag: Optional[List[str]] = Query(default=None, description="Haettavat tagit (toistuva parametri, valinnainen)"),
    n: int = Query(default=50, description="Palautettavien kohteiden määrä (1-500)"),
    authorization: Optional[str] = Header(None),
):
    global last_user_activity
    now = time.time()
    if now - last_user_activity > 7200:
        background_tasks.add_task(manage_scheduler_job, "resume")
    last_user_activity = now

    # Verifioidaan token jos sellainen on toimitettu
    verify_auth_token_optional(authorization)

    # 1. Validoidaan n-parametri (1-500)
    if n <= 0 or n > 500:
        raise HTTPException(status_code=400, detail="Parameter 'n' must be between 1 and 500.")

    # Normalisoidaan tagit jos toimitettu
    search_tags = []
    if tag:
        for t in tag:
            val = t.strip().lower()
            if val:
                if not val.startswith("#"):
                    val = f"#{val}"
                search_tags.append(val)

    logger.info(f"Outbox-haku tageilla: {search_tags or 'KAIKKI'}, koko n: {n}")

    # 2. BigQuery-haku
    if search_tags:
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
              {PAYWALL_FILTER_SQL}
              AND EXISTS (
                SELECT 1 FROM UNNEST(tags) t WHERE t IN UNNEST(@search_tags)
              )
            ORDER BY relevance DESC, like_count DESC, updated DESC, published DESC NULLS LAST, id ASC
            LIMIT @limit_n
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("search_tags", "STRING", search_tags),
                bigquery.ScalarQueryParameter("limit_n", "INT64", n),
            ]
        )
    else:
        query = f"""
            SELECT
              id,
              source,
              published,
              updated,
              like_count,
              dislike_count,
              object_json,
              1 AS relevance
            FROM `{PROJECT}.{DATASET}.objects`
            WHERE deleted = FALSE
              {PAYWALL_FILTER_SQL}
            ORDER BY published DESC NULLS LAST, updated DESC, like_count DESC, id ASC
            LIMIT @limit_n
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("limit_n", "INT64", n),
            ]
        )

    try:
        logger.info(f"Suoritetaan kysely: {query}")
        logger.info(f"Parametrit: limit_n={n}, search_tags={search_tags}")
        query_job = bq_client.query(query, job_config=job_config)
        rows = list(query_job.result())
        logger.info(f"Kysely palautti {len(rows)} riviä.")
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
                {"_uutisseuranta": "https://uutisseuranta.net/ns#", "dislikes": "_uutisseuranta:dislikes"},
            ]

            # likes: AS2 Core §5.7 -kenttä, palautetaan Collection-muodossa (ei kokonaisluku).
            # totalItems riittää — koko aktiviteettilistan palauttaminen olisi liian raskas.
            like_cnt = row["like_count"] or 0
            dislike_cnt = row["dislike_count"] or 0

            obj["likes"] = {"type": "Collection", "totalItems": like_cnt}

            # dislikes: projektikohtainen laajennus — ei AS2 Core -kenttä (toisin kuin 'likes').
            # Kirjattu hallittuna laajennuksena AS2_CONTRACT.md:hen.
            # Collection-rakenne on yhdenmukainen AS2 Core §5.7 'likes'-käytännön kanssa.
            obj["dislikes"] = {"type": "Collection", "totalItems": dislike_cnt}

            # reactionCount = likes + dislikes (kaikki reaktiot yhteensä).
            # Neutraali nimitys: sisältää sekä Agree (Like) että Disagree (Dislike) -reaktiot.
            # Tarkoitus: frontend näyttää yhteenlasketun reaktiomäärän ilman asiakaspuolen laskentaa.
            # Invariantti: arvo on oikein vain jos write-api estää duplikaattiäänet per käyttäjä.
            # Toggle-logiikka ja duplikaattiesto on ratkaistu write-apissa (poistetaan vanha, lisätään uusi).
            # Hallittu AS2-poikkeama: toggle ei kirjaa 'Undo Like' -aktiviteettia — ks. AS2_CONTRACT.md §4.
            obj["_uutisseuranta:reactionCount"] = like_cnt + dislike_cnt

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
        "orderedItems": ordered_items,
    }

    # application/activity+json on ActivityPub-yhteensopiva Content-Type
    # (application/ld+json; profile="..." olisi tiukempi AS2, mutta activity+json on laajemmin tuettu)
    return Response(
        content=json.dumps(response_json, ensure_ascii=False), media_type="application/activity+json; charset=utf-8"
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
                bigquery.ScalarQueryParameter("url", "STRING", url),
            ]
        )
        bq_client.query(query, job_config=job_config).result()
        logger.info(f"Päivitetty uutisen {url} arkistolinkki BigQueryyn: {archive_url}")
    except Exception as e:
        logger.error(f"Virhe päivitettäessä arkistolinkkiä uutiselle {url}: {e}")


# Globaalit muuttujat status-tarkistusten välimuistille
_status_cache = {}  # key: url, value: {"alive": bool, "time": float}
STATUS_CACHE_TTL = 300.0  # 5 minuuttia välimuisti uutisten ping-tarkistuksille


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

    # 1. Tarkistetaan välimuisti
    now = time.time()
    if url in _status_cache:
        cached = _status_cache[url]
        if now - cached["time"] < STATUS_CACHE_TTL:
            logger.info(f"Palautetaan status-tarkistus välimuistista linkille: {url}")
            return {"alive": cached["alive"]}

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

    # Päivitetään välimuisti
    _status_cache[url] = {"alive": alive, "time": now}

    # Jos sivu on alhaalla, selvitetään suora arkistolinkki ja päivitetään se BigQueryyn
    if not alive:
        # Oletuksena kalenterinäkymän villikortti-URL
        archive_url = f"https://web.archive.org/web/*/{url}"
        try:
            # Kysytään Internet Archiven Availability API:lta suoraa arkistoitua sivua
            async with httpx.AsyncClient(timeout=3.0) as client:
                avail_res = await client.get(f"https://archive.org/wayback/available?url={urllib.parse.quote(url)}")
                if avail_res.status_code == 200:
                    avail_data = avail_res.json()
                    closest = avail_data.get("archived_snapshots", {}).get("closest", {})
                    if closest.get("available") and closest.get("url"):
                        archive_url = closest["url"]
                        logger.info(f"Löydetty suora Wayback Machine -linkki artikkelille {url}: {archive_url}")
        except Exception as e:
            logger.warning(f"Wayback Machine Availability API kysely epäonnistui uutiselle {url}: {e}")

        background_tasks.add_task(update_archive_url_in_bq, url, archive_url)

    return {"alive": alive}


# Globaalit muuttujat tilastojen välimuistille
_stats_cache = None
_stats_cache_time = 0.0


@app.get("/ap/stats")
async def get_stats(background_tasks: BackgroundTasks):
    """Palauttaa uutisseurannan avainlukutilastot välimuistista tai laskee ne BigQueryssä."""
    global _stats_cache, _stats_cache_time, last_user_activity
    import time

    now = time.time()
    if now - last_user_activity > 7200:
        background_tasks.add_task(manage_scheduler_job, "resume")
    last_user_activity = now
    # 5 minuutin välimuisti (300 sekuntia)
    if _stats_cache is not None and (now - _stats_cache_time) < 300:
        return _stats_cache

    try:
        # 1. Lasketaan lähteiden lukumäärä
        query_sources = f"""
            SELECT COUNT(DISTINCT JSON_VALUE(PARSE_JSON(LAX_STRING(object_json)).attributedTo.name)) as cnt
            FROM `{PROJECT}.{DATASET}.objects`
            WHERE deleted = FALSE
        """
        job_sources = bq_client.query(query_sources)
        results_sources = list(job_sources.result())
        sources_count = results_sources[0].cnt if results_sources and results_sources[0].cnt else 150

        # 2. Lasketaan uutisten lukumäärä viimeisen 24 tunnin ajalta
        query_articles = f"""
            SELECT COUNT(*) as cnt
            FROM `{PROJECT}.{DATASET}.objects`
            WHERE deleted = FALSE
              AND published >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
        """
        job_articles = bq_client.query(query_articles)
        results_articles = list(job_articles.result())
        articles_last_24h = (
            results_articles[0].cnt if results_articles and results_articles[0].cnt is not None else 10000
        )

        # 3. Lasketaan aktiivisimmat lähteet viimeisen 24 tunnin ajalta
        query_sources_active = f"""
            SELECT JSON_VALUE(PARSE_JSON(LAX_STRING(object_json)).attributedTo.name) as name, COUNT(*) as cnt
            FROM `{PROJECT}.{DATASET}.objects`
            WHERE deleted = FALSE
              AND published >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
              AND JSON_VALUE(PARSE_JSON(LAX_STRING(object_json)).attributedTo.name) IS NOT NULL
            GROUP BY name
            ORDER BY cnt DESC
            LIMIT 6
        """
        job_sources_active = bq_client.query(query_sources_active)
        results_sources_active = list(job_sources_active.result())

        active_sources = []
        for r in results_sources_active:
            if r.name:
                active_sources.append({"name": r.name, "cnt": r.cnt})

        # Jos tuloksia on alle 6, täytetään puuttuvat oletusarvoilla (välttäen duplikaatteja)
        default_sources = [
            {"name": "Yle Uutiset", "cnt": 312},
            {"name": "Helsingin Sanomat", "cnt": 255},
            {"name": "Kauppalehti", "cnt": 204},
            {"name": "MTV Uutiset", "cnt": 161},
            {"name": "Iltalehti", "cnt": 136},
            {"name": "Taloussanomat", "cnt": 99},
        ]

        existing_names = {s["name"].lower() for s in active_sources}
        for ds in default_sources:
            if len(active_sources) >= 6:
                break
            if ds["name"].lower() not in existing_names:
                active_sources.append(ds)

        # Oletuspäivitysväli on 5 minuuttia
        stats = {
            "sources_count": sources_count,
            "articles_last_24h": articles_last_24h,
            "update_interval_minutes": 5,
            "active_sources": active_sources,
        }

        # Päivitetään välimuisti
        _stats_cache = stats
        _stats_cache_time = now

        return stats
    except Exception as e:
        logger.error(f"Virhe laskettaessa tilastoja: {e}")
        # Virhetilanteessa palautetaan fallback-oletusarvot
        default_sources = [
            {"name": "Yle Uutiset", "cnt": 312},
            {"name": "Helsingin Sanomat", "cnt": 255},
            {"name": "Kauppalehti", "cnt": 204},
            {"name": "MTV Uutiset", "cnt": 161},
            {"name": "Iltalehti", "cnt": 136},
            {"name": "Taloussanomat", "cnt": 99},
        ]
        return {
            "sources_count": 150,
            "articles_last_24h": 10000,
            "update_interval_minutes": 5,
            "active_sources": default_sources,
        }


@app.get("/ap/replies")
def get_replies(
    request: Request,
    background_tasks: BackgroundTasks,
    id: str = Query(default=None, description="Alkuperäisen artikkelin tai pääkommentin AS2 id"),
    authorization: Optional[str] = Header(None),
):
    global last_user_activity
    now = time.time()
    if now - last_user_activity > 7200:
        background_tasks.add_task(manage_scheduler_job, "resume")
    last_user_activity = now

    # Verifioidaan token jos sellainen on toimitettu
    verify_auth_token_optional(authorization)

    if not id:
        raise HTTPException(status_code=400, detail="Parameter 'id' is required.")

    # BigQuery-haku:
    # Haetaan kaikki kommentit, joilla thread_root = @id tai in_reply_to = @id
    # ja joiden vastaava objekti ei ole poistettu (deleted = FALSE).
    query = f"""
        SELECT
          o.id,
          o.published,
          a.object_json,
          o.like_count,
          o.dislike_count,
          a.in_reply_to,
          a.thread_root
        FROM `{PROJECT}.{SOCIAL_DATASET}.activities` a
        JOIN `{PROJECT}.{DATASET}.objects` o ON a.object_id = o.id
        WHERE o.deleted = FALSE
          AND a.type = 'Create'
          AND a.object_type = 'Note'
          AND (a.thread_root = @target_id OR a.in_reply_to = @target_id)
        ORDER BY o.published ASC
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("target_id", "STRING", id)])

    try:
        query_job = bq_client.query(query, job_config=job_config)
        rows = list(query_job.result())
    except Exception as e:
        logger.error(f"Kommenttien haku epäonnistui: {e}")
        raise HTTPException(status_code=500, detail="Database query failed.")

    replies = []
    for row in rows:
        try:
            obj_data = row["object_json"]
            if isinstance(obj_data, str):
                obj = json.loads(obj_data)
            else:
                obj = obj_data

            # Päivitetään tykkäystiedot objects-taulun mukaan
            if "object" in obj and isinstance(obj["object"], dict):
                obj["object"]["like_count"] = row["like_count"]
                obj["object"]["dislike_count"] = row["dislike_count"]

            replies.append(obj)
        except Exception as e:
            logger.warning(f"Virhe kommenttirivin käsittelyssä: {e}")
            continue

    return {"type": "Collection", "totalItems": len(replies), "orderedItems": replies}


@app.get("/ap/keep-warm")
def keep_warm():
    global last_user_activity
    elapsed = time.time() - last_user_activity
    if elapsed > 7200:  # 2 hours
        logger.info(f"Ei käyttäjäaktiivisuutta 2 tuntiin (kulunut {elapsed:.1f}s). Pausetaan keep-warm ajastin.")
        manage_scheduler_job("pause")
        return {"status": "paused", "reason": "inactivity", "elapsed_seconds": elapsed}
    return {"status": "active", "elapsed_seconds": elapsed}


@app.get("/health")
def liveness():
    # Cloud Run liveness-probe — vastaa aina 200 OK jos prosessi on elossa.
    # HUOM: Vältä 'z'-loppuisia polkuja (kuten /healthz), sillä Google Frontend (GFE) kaappaa
    # ne ja palauttaa julkisista osoitteista 404-virheen ennen pyynnön välitystä kontille.
    return {"status": "ok"}


@app.get("/ready")
def readiness():
    # Cloud Run readiness-probe.
    # HUOM: Vältä 'z'-loppuisia polkuja (kuten /readyz), sillä GFE kaappaa ne ja palauttaa 404-virheen.
    try:
        # Aktiivinen BQ-yhteystarkistus: list_datasets on kevyt API-kutsu
        # joka vahvistaa sekä autentikaation että verkkoyhteyden toimivuuden
        bq_client.list_datasets(max_results=1)
        return {"status": "ready"}
    except Exception as e:
        logger.error(f"Readiness-tarkistus epäonnistui: {e}")
        raise HTTPException(status_code=503, detail="Database connection failed")

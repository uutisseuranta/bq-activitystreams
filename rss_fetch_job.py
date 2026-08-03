# src/rss_fetch_job/main.py
# Käyttötapaus UC-1: RSS-syötteen haku ja tallennus — ks. TECHNICAL_DESIGN.md
# Käyttötapaus UC-2: pubDate-ttomat uutiset — ks. TECHNICAL_DESIGN.md
import datetime
import email.utils
import hashlib
import json
import logging
import os
import re
import sys
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup
from google.cloud import bigquery, storage
from gcs_bronze import write_to_gcs_bronze


# Määritellään lokitustaso
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "time": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
logger = logging.getLogger("rss-fetch-job")


def clean_text(raw: str) -> str:
    """Poistaa HTML-tagit ja purkaa HTML-entiteetit."""
    if not raw:
        return ""
    stripped = re.sub(r"<[^>]+>", " ", raw)
    # Korjataan ylimääräiset välilyönnit
    stripped = re.sub(r"\s+", " ", stripped)
    import html as html_lib

    return html_lib.unescape(stripped).strip()


def parse_pubdate(pubdate_str: str) -> Optional[datetime.datetime]:
    """Parsii RSS pubDate (RFC 2822) ISO UTC-kellonajaksi."""
    if not pubdate_str:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(pubdate_str)
        # Muunnetaan aina UTC-aikaan
        return dt.astimezone(datetime.timezone.utc)
    except Exception as e:
        logger.warning(f"pubDate parsinta epäonnistui merkkijonolle '{pubdate_str}': {e}")
        return None


def discover_feed_url(page_url: str, timeout: int) -> Optional[str]:
    """Hakee RSS-syötteen osoitteen sivun <link rel=alternate> -tagista tai erikoissivulta."""
    try:
        target_url = page_url
        # Erikoistapaus: valtioneuvosto.fi etusivulla ei ole RSS-linkkejä, mutta /rss-syotteet on
        if "valtioneuvosto.fi" in page_url and not page_url.endswith("/rss-syotteet"):
            from urllib.parse import urljoin

            target_url = urljoin(page_url, "/rss-syotteet")
            logger.info(f"Ohjataan autodiscovery erikoissivulle: {target_url}")

        logger.info(f"Ajetaan autodiscovery osoitteelle: {target_url}")
        resp = httpx.get(target_url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")

        # 1. Yritetään ensin standardia <link rel="alternate">
        link = soup.find("link", rel="alternate", type="application/rss+xml")
        if link and link.get("href"):
            discovered_url = link["href"]
            if discovered_url.startswith("/"):
                from urllib.parse import urljoin

                discovered_url = urljoin(target_url, discovered_url)
            logger.info(f"Löydettiin dynaaminen feed-URL: {discovered_url}")
            return discovered_url

        # 2. Jos ei löydy, etsitään sivulta href-linkkejä, jotka päättyvät /rss
        logger.info("Standardia RSS-linkkiä ei löytynyt. Etsitään a-tageja...")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/rss" in href or href.endswith("/rss"):
                if href.startswith("/"):
                    from urllib.parse import urljoin

                    href = urljoin(target_url, href)
                logger.info(f"Löydettiin a-tagista dynaaminen feed-URL: {href}")
                return href

    except Exception as e:
        logger.error(f"Feed-autodiscovery epäonnistui osoitteelle {page_url}: {e}")
    return None


def get_or_discover_feed(
    bq_client: bigquery.Client, project: str, dataset: str, feed: Dict[str, Any], timeout: int
) -> Optional[str]:
    """Hakee dynaamisen feedin osoitteen config-taulusta tai ajaa autodiscoveryn."""
    feed_name = feed["name"]
    config_key = "valtioneuvosto.rss_url" if feed_name == "valtioneuvosto" else f"rss.{feed_name}.rss_url"

    # 1. Yritetään lukea config-taulusta
    query = f"""
        SELECT value
        FROM `{project}.{dataset}.config`
        WHERE key = @key
        LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("key", "STRING", config_key)])
    try:
        rows = bq_client.query(query, job_config=job_config).result()
        for row in rows:
            logger.info(f"Käytetään tallennettua URL:ia avaimelle '{config_key}': {row.value}")
            return row.value
    except Exception as e:
        logger.warning(f"Virhe luettaessa config-taulua (jatketaan suoraan autodiscoveryyn): {e}")

    # 2. Jos ei löydy, ajetaan autodiscovery
    discovered = discover_feed_url(feed["url"], timeout)
    if not discovered:
        return None

    # 3. Tallennetaan löydetty URL config-tauluun MERGE-operaatiolla
    merge_query = f"""
        MERGE `{project}.{dataset}.config` T
        USING (SELECT @key AS key, @value AS value) S ON T.key = S.key
        WHEN MATCHED THEN
            UPDATE SET T.value = S.value, T.updated_at = CURRENT_TIMESTAMP(), T.updated_by = 'rss-fetch-job'
        WHEN NOT MATCHED THEN
            INSERT (key, value, updated_at, updated_by)
            VALUES (S.key, S.value, CURRENT_TIMESTAMP(), 'rss-fetch-job')
    """
    merge_job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("key", "STRING", config_key),
            bigquery.ScalarQueryParameter("value", "STRING", discovered),
        ]
    )
    try:
        bq_client.query(merge_query, job_config=merge_job_config).result()
        logger.info(f"Tallennettiin dynaaminen URL avaimelle '{config_key}' config-tauluun.")
    except Exception as e:
        logger.error(f"Virhe tallennettaessa dynaamista URL:ia config-tauluun: {e}")

    return discovered




def fetch_rss_feed(feed_url: str, timeout: int) -> Tuple[Optional[bytes], List[Dict[str, Any]], Optional[str], Optional[str]]:
    """Hakee RSS XML -syötteen ja parseroi itemit BeautifulSoupilla."""
    logger.info(f"Haetaan feed: {feed_url}")
    try:
        resp = httpx.get(feed_url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"HTTP-virhe haettaessa feediä {feed_url}: {e}")
        return None, [], None, None

    etag = resp.headers.get("ETag")
    last_modified = resp.headers.get("Last-Modified")

    # Parsitaan XML BeautifulSoupin xml-parserilla
    soup = BeautifulSoup(resp.content, "xml")
    items = soup.find_all("item")

    parsed_items = []
    for item in items:
        # Otsikko
        title_tag = item.find("title")
        title = clean_text(title_tag.text) if title_tag else ""

        # Linkki
        link_tag = item.find("link")
        link = link_tag.text.strip() if link_tag else ""

        # Kuvaus / Yhteenveto
        desc_tag = item.find("description")
        summary = clean_text(desc_tag.text) if desc_tag else ""

        # Julkaisuaika (pubDate)
        pubdate_tag = item.find("pubDate")
        published_dt = parse_pubdate(pubdate_tag.text.strip() if pubdate_tag else "")

        # Kuva (media:thumbnail tai enclosure)
        image_url = None

        # 1. media:thumbnail
        media_thumb = item.find("media:thumbnail") or item.find("thumbnail")
        if media_thumb and media_thumb.get("url"):
            image_url = media_thumb["url"]

        # 1b. media:content (commonly used by Helsingin Sanomat, Iltalehti, etc.)
        if not image_url:
            media_content = item.find("media:content") or item.find("content")
            if media_content and media_content.get("url"):
                medium = media_content.get("medium", "")
                mime_type = media_content.get("type", "")
                if medium == "image" or mime_type.startswith("image/") or not (medium or mime_type):
                    image_url = media_content["url"]

        # 2. enclosure type="image/*"
        if not image_url:
            enclosures = item.find_all("enclosure")
            for enc in enclosures:
                if enc.get("type", "").startswith("image/") and enc.get("url"):
                    image_url = enc["url"]
                    break

        # 3. Fallback: kanavan oma kuva (ei item-kohtainen)
        if not image_url:
            channel_image = soup.find("image")
            if channel_image:
                ch_url = channel_image.find("url")
                if ch_url:
                    image_url = ch_url.text.strip()

        # 4. Maksumuurin tunnistus kategorioista (Tilaajille / Plus / Premium)
        is_paywalled = False
        categories = item.find_all("category")
        for cat in categories:
            if cat.text and cat.text.strip().lower() in ("tilaajille", "plus", "premium"):
                is_paywalled = True
                break

        parsed_items.append(
            {
                "title": title,
                "link": link,
                "summary": summary,
                "published": published_dt,
                "image_url": image_url,
                "is_paywalled": is_paywalled
            }
        )

    return resp.content, parsed_items, etag, last_modified


def build_as2_article(item: Dict[str, Any], source: str, domain: str) -> Dict[str, Any]:
    """Muodostaa standardin W3C Activity Streams 2.0 Article -rakenteen."""
    input_str = f"{source}{item['link']}"
    url_hash = hashlib.sha256(input_str.encode("utf-8")).hexdigest()[:16]
    as2_id = f"https://uutisseuranta.net/ap/objects/{url_hash}"

    # Kartoitetaan lähde julkaisijaksi
    publisher_names = {
        "hs": "Helsingin Sanomat",
        "iltalehti": "Iltalehti",
        "is": "Ilta-Sanomat",
        "kauppalehti": "Kauppalehti",
        "mtv": "MTV Uutiset",
        "valtioneuvosto": "Valtioneuvosto",
    }
    publisher_urls = {
        "hs": "https://www.hs.fi",
        "iltalehti": "https://www.iltalehti.fi",
        "is": "https://www.is.fi",
        "kauppalehti": "https://www.kauppalehti.fi",
        "mtv": "https://www.mtvuutiset.fi",
        "valtioneuvosto": "https://valtioneuvosto.fi",
    }

    publisher_name = publisher_names.get(source, source.capitalize())
    publisher_url = publisher_urls.get(source, "")

    if item["published"]:
        published_str = item["published"].isoformat().replace("+00:00", "Z")
    else:
        published_str = None

    article_json = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "type": "Article",
        "id": as2_id,
        "url": item["link"],
        "name": item["title"],
        "summary": item["summary"],
        "published": published_str,
        "updated": published_str,
        "attributedTo": {"type": "Organization", "name": publisher_name, "url": publisher_url},
    }

    if item.get("is_paywalled"):
        article_json["isAccessibleForFree"] = False
        article_json["tag"] = [{"type": "Hashtag", "name": "#tilaajille", "href": "https://uutisseuranta.net/?tag=%23tilaajille"}]
    else:
        article_json["isAccessibleForFree"] = True

    if item["image_url"]:
        article_json["image"] = {"type": "Image", "url": item["image_url"]}

    # Määritetään og_enriched arvo:
    # 1. Jos artikkeli on maksumuuritettu, se piilotetaan uutisvirrasta, eikä sille haeta mitään taustalla (og_enriched=True).
    # 2. Jos artikkeli on ilmainen ja sillä on jo kuva (saatu RSS:stä), se on valmis (og_enriched=True).
    # 3. Jos artikkeli on ilmainen mutta kuva puuttuu (esim. Kauppalehti ei tarjoa kuvia RSS-virrassa),
    #    og_enriched asetetaan Falseksi, jotta taustaprosessi (og-enrichment-job) käy hakemassa kuvan sivulta.
    if item.get("is_paywalled"):
        og_enriched = True
    else:
        og_enriched = bool(item["image_url"])

    return {
        "id": as2_id,
        "source": source,
        "published": item["published"],
        "updated": item["published"],
        "object_json": article_json,
        "og_enriched": og_enriched,
    }


def get_existing_ids(bq_client: bigquery.Client, project: str, dataset: str, sources: List[str]) -> set:
    """Hakee olemassa olevat uutistunnukset päätaulusta ja pending-taulusta annetuille lähteille yhdellä kyselyllä."""
    if not sources:
        return set()
    query = f"""
        SELECT id FROM `{project}.{dataset}.objects` WHERE source IN UNNEST(@sources)
        UNION DISTINCT
        SELECT id FROM `{project}.{dataset}.objects_pending` WHERE source IN UNNEST(@sources)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("sources", "STRING", sources)
        ]
    )
    try:
        query_job = bq_client.query(query, job_config=job_config)
        results = query_job.result()
        return {row.id for row in results}
    except Exception as e:
        logger.warning(f"Virhe haettaessa olemassa olevia tunnuksia: {e}")
        return set()


def write_to_bigquery(bq_client: bigquery.Client, project: str, dataset: str, articles: List[Dict[str, Any]]) -> None:
    """Tallentaa kaikki uudet AS2-artikkelit objects_pending-tauluun odottamaan rikastusta."""
    if not articles:
        logger.info("Ei uusia artikkeleita tallennettavaksi.")
        return

    logger.info(f"Ladataan {len(articles)} artikkelia pending-tauluun...")
    pending_rows = []
    for art in articles:
        pending_rows.append(
            {
                "id": art["id"],
                "source": art["source"],
                "received_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "object_json": json.dumps(art["object_json"]),
            }
        )
    pending_table_id = f"{project}.{dataset}.objects_pending"
    pending_schema = [
        bigquery.SchemaField("id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("received_at", "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("object_json", "JSON", mode="NULLABLE"),
    ]
    pending_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND", schema=pending_schema)
    try:
        load_job = bq_client.load_table_from_json(pending_rows, pending_table_id, job_config=pending_config)
        load_job.result()
        logger.info(f"{len(articles)} artikkelia lisätty pending-tauluun.")
    except Exception as e:
        logger.error(f"Virhe kirjoitettaessa pending-tauluun: {e}")
        raise e



def update_last_fetched_timestamp(
    bq_client: bigquery.Client, project: str, dataset: str, run_time: datetime.datetime
) -> None:
    """Päivittää config-tauluun tiedon milloin haku on viimeksi suoritettu onnistuneesti."""
    config_key = "rss.last_fetched_at"
    merge_query = f"""
        MERGE `{project}.{dataset}.config` T
        USING (SELECT @key AS key, @value AS value) S ON T.key = S.key
        WHEN MATCHED THEN
            UPDATE SET T.value = S.value, T.updated_at = CURRENT_TIMESTAMP(), T.updated_by = 'rss-fetch-job'
        WHEN NOT MATCHED THEN
            INSERT (key, value, updated_at, updated_by)
            VALUES (S.key, S.value, CURRENT_TIMESTAMP(), 'rss-fetch-job')
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("key", "STRING", config_key),
            bigquery.ScalarQueryParameter("value", "STRING", run_time.isoformat()),
        ]
    )
    try:
        bq_client.query(merge_query, job_config=job_config).result()
        logger.info(f"Päivitetty '{config_key}' -> {run_time.isoformat()} config-tauluun.")
    except Exception as e:
        logger.error(f"Virhe päivitettäessä viimeistä hakuajankohtaa config-tauluun: {e}")


def main() -> None:
    run_time = datetime.datetime.now(datetime.timezone.utc)

    # Luetaan ympäristömuuttujat
    project = os.getenv("GCP_PROJECT")
    dataset = os.getenv("BQ_DATASET")
    timeout_str = os.getenv("REQUEST_TIMEOUT", "10")
    # RSS_FEEDS luetaan suoraan ympäristömuuttujasta.
    # HUOM: Jos syötelistaan tulee muutoksia (kuten uuden median lisäys tai poisto),
    # Cloud Run Job voidaan päivittää suoraan ilman uutta konttikäännöstä (buildia) komennolla:
    # gcloud run jobs update rss-fetch-job --env-vars-file deploy/rss-fetch-job.env.yaml
    rss_feeds_raw = os.getenv("RSS_FEEDS")
    domain = os.getenv("DOMAIN", "activitystreams.uutisseuranta.net")

    if not project or not dataset:
        logger.critical("Virhe: GCP_PROJECT ja BQ_DATASET ympäristömuuttujat ovat pakollisia.")
        sys.exit(1)

    if not rss_feeds_raw:
        logger.critical("Virhe: RSS_FEEDS ympäristömuuttuja on tyhjä.")
        sys.exit(1)

    try:
        timeout = int(timeout_str)
    except ValueError:
        timeout = 10

    try:
        feeds = json.loads(rss_feeds_raw)
    except json.JSONDecodeError as e:
        logger.critical(f"Virhe RSS_FEEDS parsimisessa (ei validia JSONia): {e}")
        sys.exit(1)

    logger.info(f"Käynnistetään haku. Projektitunnus: {project}, dataset: {dataset}")

    bq_client = bigquery.Client(project=project)

    # Haetaan olemassa olevat ID:t kaikille lähteille yhdellä kertaa
    feed_names = [f.get("name") for f in feeds if f.get("name")]
    existing_ids = get_existing_ids(bq_client, project, dataset, feed_names)

    all_as2_articles = []
    seen_this_run = set()

    for feed in feeds:
        feed_name = feed.get("name")
        feed_url = feed.get("url")
        autodiscover = feed.get("autodiscover", False)

        if not feed_name or not feed_url:
            logger.warning(f"Ohitetaan virheellinen feed-konfiguraatio: {feed}")
            continue

        if autodiscover:
            # Dynaaminen URL-discovery
            discovered_url = get_or_discover_feed(bq_client, project, dataset, feed, timeout)
            if not discovered_url:
                logger.error(f"Ei pystytty selvittämään syötettä lähteelle: {feed_name}. Ohitetaan.")
                continue
            feed_url = discovered_url

        # Haetaan ja parsitaan feedin itemit ja arkistoidaan raakadata GCS:ään
        raw_xml, items, etag, last_modified = fetch_rss_feed(feed_url, timeout)
        if raw_xml:
            write_to_gcs_bronze(project, feed_name, raw_xml, "xml", source_type="rss_atom", etag=etag, last_modified=last_modified)
        logger.info(f"Haku onnistui. Löydettiin {len(items)} parsinakelpoista artikkelia lähteestä '{feed_name}'.")

        new_count = 0

        # Muunnetaan AS2 Article -muotoon
        for item in items:
            as2_art = build_as2_article(item, feed_name, domain)
            art_id = as2_art["id"]
            if art_id not in existing_ids and art_id not in seen_this_run:
                all_as2_articles.append(as2_art)
                seen_this_run.add(art_id)
                new_count += 1
        logger.info(f"Lisätty {new_count} uutta artikkelia lähteestä '{feed_name}' odottamaan latausta.")

    # Kirjoitetaan BigQueryyn
    try:
        write_to_bigquery(bq_client, project, dataset, all_as2_articles)
        # Päivitetään onnistuneen suorituksen timestamp config-tauluun
        update_last_fetched_timestamp(bq_client, project, dataset, run_time)
        logger.info("RSS-haku suoritettu onnistuneesti loppuun.")
    except Exception as e:
        logger.critical(f"Kriittinen virhe kantaan kirjoittamisessa: {e}")
        sys.exit(1)


if __name__ == "__main__":
    from gcp_logging import send_ops_notification

    try:
        main()
        send_ops_notification("rss-fetch-job", "success")
    except SystemExit as se:
        if se.code == 0:
            send_ops_notification("rss-fetch-job", "success")
        else:
            send_ops_notification("rss-fetch-job", "failure", f"SystemExit: {se.code}")
        raise se
    except BaseException as e:
        send_ops_notification("rss-fetch-job", "failure", str(e))
        raise e

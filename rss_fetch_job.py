# src/rss_fetch_job/main.py
import datetime
import email.utils
import hashlib
import json
import logging
import os
import re
import sys
import uuid
from typing import Any, Dict, List, Optional

import httpx
from bs4 import BeautifulSoup
from google.cloud import bigquery


# Määritellään lokitustaso
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


def get_or_discover_feed(bq_client: bigquery.Client, project: str, dataset: str, feed: Dict[str, Any], timeout: int) -> Optional[str]:
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
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("key", "STRING", config_key)]
    )
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
            bigquery.ScalarQueryParameter("value", "STRING", discovered)
        ]
    )
    try:
        bq_client.query(merge_query, job_config=merge_job_config).result()
        logger.info(f"Tallennettiin dynaaminen URL avaimelle '{config_key}' config-tauluun.")
    except Exception as e:
        logger.error(f"Virhe tallennettaessa dynaamista URL:ia config-tauluun: {e}")

    return discovered


def fetch_rss_feed(feed_url: str, timeout: int) -> List[Dict[str, Any]]:
    """Hakee RSS XML -syötteen ja parseroi itemit BeautifulSoupilla."""
    logger.info(f"Haetaan feed: {feed_url}")
    try:
        resp = httpx.get(feed_url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"HTTP-virhe haettaessa feediä {feed_url}: {e}")
        return []

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

        if not published_dt:
            logger.warning(f"Ohitetaan artikkeli ilman toimivaa pubDatea. Otsikko: '{title}', URL: {link}")
            continue

        # Kuva (media:thumbnail tai enclosure)
        image_url = None

        # 1. media:thumbnail
        media_thumb = item.find("media:thumbnail") or item.find("thumbnail")
        if media_thumb and media_thumb.get("url"):
            image_url = media_thumb["url"]

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

        parsed_items.append({
            "title": title,
            "link": link,
            "summary": summary,
            "published": published_dt,
            "image_url": image_url
        })

    return parsed_items


def build_as2_article(item: Dict[str, Any], source: str, domain: str) -> Dict[str, Any]:
    """Muodostaa standardin W3C Activity Streams 2.0 Article -rakenteen."""
    url = item["link"]
    # Lasketaan sha256 URL:sta ID:tä varten
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
    as2_id = f"https://{domain}/ap/objects/articles/{source}/{url_hash}"

    # Kartoitetaan lähde julkaisijaksi
    publisher_names = {
        "hs": "Helsingin Sanomat",
        "iltalehti": "Iltalehti",
        "is": "Ilta-Sanomat",
        "kauppalehti": "Kauppalehti",
        "mtv": "MTV Uutiset",
        "valtioneuvosto": "Valtioneuvosto"
    }
    publisher_urls = {
        "hs": "https://www.hs.fi",
        "iltalehti": "https://www.iltalehti.fi",
        "is": "https://www.is.fi",
        "kauppalehti": "https://www.kauppalehti.fi",
        "mtv": "https://www.mtvuutiset.fi",
        "valtioneuvosto": "https://valtioneuvosto.fi"
    }

    publisher_name = publisher_names.get(source, source.capitalize())
    publisher_url = publisher_urls.get(source, "")

    published_str = item["published"].isoformat().replace("+00:00", "Z")

    article_json = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "type": "Article",
        "id": as2_id,
        "url": url,
        "name": item["title"],
        "summary": item["summary"],
        "published": published_str,
        "updated": published_str,
        "attributedTo": {
            "type": "Organization",
            "name": publisher_name,
            "url": publisher_url
        }
    }

    if item["image_url"]:
        article_json["image"] = {
            "type": "Image",
            "url": item["image_url"]
        }

    return {
        "id": as2_id,
        "source": source,
        "published": item["published"],
        "updated": item["published"],
        "object_json": article_json
    }


def write_to_bigquery(bq_client: bigquery.Client, project: str, dataset: str, articles: List[Dict[str, Any]]) -> None:
    """Tallentaa AS2-artikkelit BigQueryyn väliaikaistaulun ja MERGE-lauseen kautta."""
    if not articles:
        logger.info("Ei uusia artikkeleita tallennettavaksi.")
        return

    # Luodaan uniikki temp-taulu tälle suoritukselle
    temp_table_id = f"{project}.{dataset}.objects_temp_{uuid.uuid4().hex}"

    # Muunnetaan datetime ISO-merkkijonoksi ja object_json JSON-yhteensopivaksi
    rows_to_load = []
    for art in articles:
        rows_to_load.append({
            "id": art["id"],
            "source": art["source"],
            "published": art["published"].isoformat(),
            "updated": art["updated"].isoformat(),
            "object_json": json.dumps(art["object_json"])
        })

    # Temp-taulun latausskeema
    schema = [
        bigquery.SchemaField("id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("published", "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("updated", "TIMESTAMP", mode="NULLABLE"),
        bigquery.SchemaField("object_json", "JSON", mode="NULLABLE"),
    ]

    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE",
        schema=schema
    )

    logger.info(f"Ladataan {len(rows_to_load)} riviä väliaikaistauluun {temp_table_id}")
    load_job = bq_client.load_table_from_json(rows_to_load, temp_table_id, job_config=job_config)
    load_job.result()  # Odotetaan latauksen valmistumista

    # Suoritetaan MERGE varsinaiseen tauluun.
    # Tärkeää: tags, tags_enriched, like_count ja deleted jätetään MATCHED-päivityksen ulkopuolelle
    merge_query = f"""
        MERGE `{project}.{dataset}.objects` T
        USING `{temp_table_id}` S ON T.id = S.id
        WHEN MATCHED AND S.updated > T.updated THEN
            UPDATE SET
                T.object_json = S.object_json,
                T.published   = S.published,
                T.updated     = S.updated,
                T.source      = S.source
        WHEN NOT MATCHED THEN
            INSERT (id, source, published, updated, tags, tags_enriched, like_count, deleted, object_json)
            VALUES (S.id, S.source, S.published, S.updated, [], FALSE, 0, FALSE, S.object_json)
    """

    try:
        logger.info("Suoritetaan BigQuery MERGE-operaatio varsinaiseen objects-tauluun...")
        query_job = bq_client.query(merge_query)
        query_job.result()
        logger.info("MERGE suoritettu onnistuneesti.")
    finally:
        # Aina siivotaan väliaikaistaulu suorituksen jälkeen
        logger.info(f"Poistetaan väliaikaistaulu {temp_table_id}")
        bq_client.delete_table(temp_table_id, not_found_ok=True)


def update_last_fetched_timestamp(bq_client: bigquery.Client, project: str, dataset: str, run_time: datetime.datetime) -> None:
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
            bigquery.ScalarQueryParameter("value", "STRING", run_time.isoformat())
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

    all_as2_articles = []

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

        # Haetaan ja parsitaan feedin itemit
        items = fetch_rss_feed(feed_url, timeout)
        logger.info(f"Haku onnistui. Löydettiin {len(items)} parsinakelpoista artikkelia lähteestä '{feed_name}'.")

        # Muunnetaan AS2 Article -muotoon
        for item in items:
            as2_art = build_as2_article(item, feed_name, domain)
            all_as2_articles.append(as2_art)

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
    main()

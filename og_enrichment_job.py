# Käyttötapaus UC-2: pubDate-ttomat artikkelit — ks. TECHNICAL_DESIGN.md
# Käyttötapaus UC-3: OG-rikastus — ks. TECHNICAL_DESIGN.md ja STANDARDS.md §4
import datetime
import email.utils
import json
import os
import sys
import uuid

# Käytetään jaettua OG-parseria
import og_parser
from gcp_logging import get_logger
from google.cloud import bigquery

logger = get_logger("og-enrichment-job")


def main() -> None:
    # Luetaan ympäristömuuttujat — kaikki konfiguroitavissa Cloud Run -ympäristömuuttujina
    project = os.getenv("GCP_PROJECT")
    dataset = os.getenv("BQ_DATASET", "activitystreams")
    batch_size_str = os.getenv("BATCH_SIZE", "100")
    timeout_str = os.getenv("HTTP_TIMEOUT_S", "10")
    max_bytes_str = os.getenv("MAX_RESPONSE_BYTES", "2097152")  # 2 MB oletus

    if not project:
        logger.critical("Virhe: GCP_PROJECT ympäristömuuttuja on pakollinen.")
        sys.exit(1)

    try:
        batch_size = int(batch_size_str)
    except ValueError:
        batch_size = 100

    try:
        timeout = float(timeout_str)
    except ValueError:
        timeout = 10.0

    try:
        max_bytes = int(max_bytes_str)
    except ValueError:
        max_bytes = 2097152

    logger.info(f"Käynnistetään OG-rikastus. Projekti: {project}, dataset: {dataset}, batch_size: {batch_size}")

    bq_client = bigquery.Client(project=project)

    # 1. Haetaan rikastamattomat rivit sekä päätaulusta että pending-taulusta
    query = f"""
        SELECT id, object_json, 'main' as origin, source
        FROM `{project}.{dataset}.objects`
        WHERE source = 'rss'
          AND og_enriched = FALSE
          AND deleted = FALSE
        UNION ALL
        SELECT id, object_json, 'pending' as origin, source
        FROM `{project}.{dataset}.objects_pending`
        LIMIT {batch_size}
    """

    try:
        query_job = bq_client.query(query)
        rows = list(query_job.result())
    except Exception as e:
        logger.critical(f"Rikastamattomien rivien haku epäonnistui: {e}")
        sys.exit(1)

    if not rows:
        logger.info("Ei rikastamattomia RSS-artikkeleita tai pending-artikkeleita.")
        return

    logger.info(f"Löydettiin {len(rows)} rikastamatonta artikkelia. Aloitetaan haku...")

    rows_to_load = []

    for row in rows:
        row_id = row["id"]
        # row:sta haetaan source, oletusarvona 'rss' jos kenttää ei ole (kuten testien mockeissa)
        try:
            row_source = row["source"]
        except (KeyError, TypeError):
            row_source = "rss"
        # BigQuery Python -asiakas voi palauttaa JSON-kentän joko dictinä (uudempi SDK)
        # tai JSON-merkkijonona (vanhempi SDK/schema). Käsitellään molemmat tapaukset.
        obj_json_raw = row["object_json"]
        if isinstance(obj_json_raw, str):
            try:
                object_json = json.loads(obj_json_raw)
            except Exception as e:
                logger.error(f"Virhe parsiessa rivin {row_id} object_jsonia: {e}")
                continue
        elif isinstance(obj_json_raw, dict):
            object_json = obj_json_raw
        else:
            logger.error(f"Rivin {row_id} object_json on tuntematon tyyppi: {type(obj_json_raw)}")
            continue

        url = object_json.get("url")
        if not url:
            logger.warning(f"Rivi {row_id} ohitetaan: url-kenttä puuttuu.")
            # Merkitään enriched=TRUE virheellä — estää äärettömän uudelleenyrityksen
            rows_to_load.append({
                "id": row_id,
                "source": row_source,
                "object_json": json.dumps(object_json),
                "og_enriched": True,
                "og_enriched_error": "Missing url in object_json"
            })
            continue

        # 2. Tarkistetaan robots.txt — kunnioitetaan sivuston crawling-kieltoja
        if not og_parser.robots_check(url):
            logger.warning(f"robots.txt estää URL:n: {url} (id: {row_id})")
            rows_to_load.append({
                "id": row_id,
                "source": row_source,
                "object_json": json.dumps(object_json),
                "og_enriched": True,
                "og_enriched_error": "Blocked by robots.txt"
            })
            continue

        # 3. Haetaan ja parsitetaan OG-tagit
        try:
            # Katsotaan onko fetch_url_stream_with_headers käytettävissä ja palauttaako se tuplen (testiyhteensopivuus)
            try:
                res = og_parser.fetch_url_stream_with_headers(url, timeout=timeout, max_bytes=max_bytes)
                if isinstance(res, tuple) and len(res) == 2:
                    html_content, headers = res
                else:
                    html_content = og_parser.fetch_url_stream(url, timeout=timeout, max_bytes=max_bytes)
                    headers = {}
            except Exception:
                html_content = og_parser.fetch_url_stream(url, timeout=timeout, max_bytes=max_bytes)
                headers = {}

            metadata = og_parser.parse_og_metadata(html_content, url)

            # 4. Rikastetaan kentät — sovelletaan prioriteettisäännöt:
            # name: pidempi teksti voittaa (RSS-otsikko vs. OG-title)
            enriched_name = og_parser.longer(object_json.get("name"), metadata.get("title"))
            if enriched_name:
                object_json["name"] = enriched_name

            # summary: pidempi voittaa (RSS-kuvaus vs. OG-description)
            enriched_summary = og_parser.longer(object_json.get("summary"), metadata.get("description"))
            if enriched_summary:
                object_json["summary"] = enriched_summary

            # image: OG voittaa aina jos saatavilla (OG-kuva on yksiselitteisesti valittu)
            if metadata.get("image"):
                object_json["image"] = {
                    "type": "Image",
                    "url": metadata["image"]
                }

            # published prioriteetti: JSON-LD (jo parsittu metadata["published_time"]:iin) -> OG -> Last-Modified
            resolved_published = metadata.get("published_time")
            if not resolved_published and headers.get("Last-Modified"):
                try:
                    dt = email.utils.parsedate_to_datetime(headers["Last-Modified"])
                    resolved_published = dt.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
                except Exception:
                    pass

            if resolved_published:
                object_json["published"] = resolved_published
                if not object_json.get("updated"):
                    object_json["updated"] = resolved_published

            # updated: OG modified_time voittaa aina jos saatavilla
            if metadata.get("modified_time"):
                object_json["updated"] = metadata["modified_time"]

            logger.info(f"Artikkeli {row_id} rikastettu onnistuneesti.")
            rows_to_load.append({
                "id": row_id,
                "source": row_source,
                "object_json": json.dumps(object_json),
                "og_enriched": True,
                "og_enriched_error": None
            })

        except PermissionError as pe:
            # og_parser.fetch_url_stream nostaa PermissionErrorin SSRF-validointivirheistä
            logger.warning(f"SSRF-validointivirhe haettaessa {url}: {pe}")
            rows_to_load.append({
                "id": row_id,
                "source": row_source,
                "object_json": json.dumps(object_json),
                "og_enriched": True,
                "og_enriched_error": f"SSRF check failed: {pe}"
            })
        except Exception as e:
            logger.warning(f"Virhe haettaessa tai parsiessa URL {url}: {e}")
            rows_to_load.append({
                "id": row_id,
                "source": row_source,
                "object_json": json.dumps(object_json),
                "og_enriched": True,
                "og_enriched_error": f"Fetch/Parse error: {e}"
            })

    # 5. Päivitetään BigQueryyn temp-taulu + MERGE -patternilla
    # Syy temp-tauluun: BigQuery ei tue suoraa batch-UPDATE:a JSON-arvoilla.
    # load_table_from_json on tehokas ja atominen yhden erän sisällä.
    # MERGE korvaa vanhat rivit uusilla — idempotenttisuus taattu ON T.id = S.id -ehdolla.
    if not rows_to_load:
        return

    # Uniikki UUID-suffiksi estää race conditionin rinnakkaisissa ajoissa
    temp_table_id = f"{project}.{dataset}.objects_enrich_temp_{uuid.uuid4().hex}"
    schema = [
        bigquery.SchemaField("id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("object_json", "JSON", mode="NULLABLE"),
        bigquery.SchemaField("og_enriched", "BOOLEAN", mode="REQUIRED"),
        bigquery.SchemaField("og_enriched_error", "STRING", mode="NULLABLE"),
    ]

    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE",
        schema=schema
    )

    try:
        logger.info(f"Ladataan {len(rows_to_load)} rikastustulosta väliaikaistauluun {temp_table_id}")
        load_job = bq_client.load_table_from_json(rows_to_load, temp_table_id, job_config=job_config)
        load_job.result()

        # Suoritetaan MERGE päätauluun 'objects'
        # Päivitetään olemassa olevat ja lisätään uudet, jos julkaisupäivämäärä selvisi (pending-taulusta siirretyt)
        merge_query = f"""
            MERGE `{project}.{dataset}.objects` T
            USING `{temp_table_id}` S ON T.id = S.id
            WHEN MATCHED THEN
                UPDATE SET
                    T.object_json = S.object_json,
                    T.published   = COALESCE(SAFE_CAST(JSON_VALUE(S.object_json, '$.published') AS TIMESTAMP), T.published),
                    T.updated     = COALESCE(SAFE_CAST(JSON_VALUE(S.object_json, '$.updated') AS TIMESTAMP), T.updated),
                    T.og_enriched = S.og_enriched,
                    T.og_enriched_error = S.og_enriched_error
            WHEN NOT MATCHED AND JSON_VALUE(S.object_json, '$.published') IS NOT NULL THEN
                INSERT (id, source, published, updated, tags, tags_enriched, like_count, deleted, og_enriched, og_enriched_error, object_json)
                VALUES (
                    S.id,
                    S.source,
                    SAFE_CAST(JSON_VALUE(S.object_json, '$.published') AS TIMESTAMP),
                    SAFE_CAST(JSON_VALUE(S.object_json, '$.updated') AS TIMESTAMP),
                    [],
                    FALSE,
                    0,
                    FALSE,
                    S.og_enriched,
                    S.og_enriched_error,
                    S.object_json
                )
        """
        logger.info("Suoritetaan BigQuery MERGE rikastetuille riveille...")
        bq_client.query(merge_query).result()
        logger.info("Rikastuksen MERGE suoritettu onnistuneesti.")

        # Poistetaan käsitellyt rivit pending-taulukosta
        delete_pending_query = f"""
            DELETE FROM `{project}.{dataset}.objects_pending`
            WHERE id IN (SELECT id FROM `{temp_table_id}`)
        """
        logger.info("Poistetaan käsitellyt rivit pending-taulukosta...")
        bq_client.query(delete_pending_query).result()
        logger.info("Pending-taulun siivous suoritettu.")

    except Exception as e:
        logger.critical(f"Rikastustulosten tallennus epäonnistui: {e}")
        sys.exit(1)
    finally:
        # Siivotaan temp-taulu aina — myös virheen sattuessa
        logger.info(f"Poistetaan väliaikaistaulu {temp_table_id}")
        bq_client.delete_table(temp_table_id, not_found_ok=True)


if __name__ == "__main__":
    main()

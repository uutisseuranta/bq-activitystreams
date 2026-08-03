# ahjo_fetch_job.py
# Ingests recent decisions from Helsinki Ahjo REST API, caches raw JSON to GCS Bronze,
# and stages unseen items into BigQuery activitystreams.objects_pending.
import datetime
import hashlib
import json
import os
import sys
from typing import Any, Dict, List, Optional

import httpx
from gcp_logging import get_logger, send_ops_notification
from google.cloud import bigquery, storage

logger = get_logger("ahjo-fetch-job")


def write_to_gcs_bronze(project_id: str, source: str, data: bytes, extension: str = "json") -> None:
    """Saves raw API response payload to GCS Bronze bucket."""
    try:
        client = storage.Client(project=project_id)
        bucket_name = f"{project_id}-bronze-{source}"
        bucket = client.bucket(bucket_name)
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_raw.{extension}"
        blob = bucket.blob(filename)
        content_type = "application/json" if extension == "json" else "application/xml"
        blob.upload_from_string(data, content_type=content_type)
        logger.info(f"Raw data cached to GCS: gs://{bucket_name}/{filename}")
    except Exception as e:
        logger.error(f"Failed to save raw data to GCS Bronze: {e}")


def get_existing_ids(bq_client: bigquery.Client, project: str, dataset: str, source: str) -> set:
    """Fetches existing IDs in objects and objects_pending tables for the source."""
    query = f"""
        SELECT id FROM `{project}.{dataset}.objects` WHERE source = @source
        UNION DISTINCT
        SELECT id FROM `{project}.{dataset}.objects_pending` WHERE source = @source
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("source", "STRING", source)
        ]
    )
    try:
        query_job = bq_client.query(query, job_config=job_config)
        results = query_job.result()
        return {row.id for row in results}
    except Exception as e:
        logger.warning(f"Failed to fetch existing IDs from BigQuery: {e}")
        return set()


def build_as2_article(decision: Dict[str, Any], source: str, domain: str) -> Dict[str, Any]:
    """Converts an Ahjo decision dictionary into AS2 Article format."""
    # Kenttämallinnus Ahjo REST API -> AS2 Article
    # Ahjossa on yleensä päätöksen id tai tunniste (esim. HEL-2026-XXXX) tai suora linkki
    decision_id = decision.get("id") or decision.get("register_id") or decision.get("decision_id")
    
    # Yritetään etsiä suoraa linkkiä
    decision_url = decision.get("url") or decision.get("link") or decision.get("resource_uri")
    if not decision_url and decision_id:
        decision_url = f"https://paatokset.hel.fi/fi/asia/{decision_id}"
    elif not decision_url:
        decision_url = "https://paatokset.hel.fi"

    # Generoidaan ID
    input_str = f"{source}{decision_url}"
    url_hash = hashlib.sha256(input_str.encode("utf-8")).hexdigest()[:16]
    as2_id = f"https://{domain}/ap/objects/{url_hash}"

    # Päätöksen julkaisuaika
    date_str = decision.get("date") or decision.get("published") or decision.get("meeting_date")
    pub_date = None
    if date_str:
        try:
            clean_str = date_str.replace("Z", "+00:00")
            pub_date = datetime.datetime.fromisoformat(clean_str).astimezone(datetime.timezone.utc)
        except Exception:
            pass
    
    pub_str = pub_date.isoformat().replace("+00:00", "Z") if pub_date else None

    # Päätöksentekijä
    pm_org = decision.get("policymaker") or decision.get("organization") or decision.get("decider")
    org_name = "Helsingin kaupunki"
    if isinstance(pm_org, dict):
        org_name = pm_org.get("name") or org_name
    elif isinstance(pm_org, str):
        org_name = pm_org

    article_json = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "type": "Article",
        "id": as2_id,
        "url": decision_url,
        "name": decision.get("title") or decision.get("subject") or "Ahjo Päätös",
        "summary": f"Helsingin kaupungin päätöksenteko. Päätöksentekijä: {org_name}.",
        "published": pub_str,
        "updated": pub_str,
        "attributedTo": {
            "type": "Organization",
            "name": org_name,
            "url": "https://www.hel.fi"
        },
        "license": "https://www.hel.fi/fi/avoimet-tietolahteet-ja-rajapinnat"
    }

    return {
        "id": as2_id,
        "source": source,
        "published": pub_date,
        "object_json": article_json
    }


def write_to_bigquery(bq_client: bigquery.Client, project: str, dataset: str, items: List[Dict[str, Any]]) -> None:
    """Writes decisions to objects_pending staging table."""
    if not items:
        logger.info("No new decisions to stage.")
        return

    logger.info(f"Loading {len(items)} items to objects_pending...")
    pending_rows = []
    for item in items:
        pending_rows.append({
            "id": item["id"],
            "source": item["source"],
            "received_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "object_json": json.dumps(item["object_json"])
        })

    table_id = f"{project}.{dataset}.objects_pending"
    schema = [
        bigquery.SchemaField("id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("received_at", "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("object_json", "JSON", mode="NULLABLE")
    ]
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND", schema=schema)
    try:
        load_job = bq_client.load_table_from_json(pending_rows, table_id, job_config=job_config)
        load_job.result()
        logger.info("Successfully loaded new items into objects_pending.")
    except Exception as e:
        logger.error(f"Failed to load items to BQ: {e}")
        raise e


def update_last_fetched_timestamp(bq_client: bigquery.Client, project: str, dataset: str, run_time: datetime.datetime) -> None:
    """Updates config key for tracking fetch history."""
    config_key = "ahjo.last_fetched_at"
    query = f"""
        MERGE `{project}.{dataset}.config` T
        USING (SELECT @key AS key, @value AS value) S ON T.key = S.key
        WHEN MATCHED THEN
            UPDATE SET T.value = S.value, T.updated_at = CURRENT_TIMESTAMP(), T.updated_by = 'ahjo-fetch-job'
        WHEN NOT MATCHED THEN
            INSERT (key, value, updated_at, updated_by)
            VALUES (S.key, S.value, CURRENT_TIMESTAMP(), 'ahjo-fetch-job')
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("key", "STRING", config_key),
            bigquery.ScalarQueryParameter("value", "STRING", run_time.isoformat())
        ]
    )
    try:
        bq_client.query(query, job_config=job_config).result()
    except Exception as e:
        logger.error(f"Failed to update last fetched timestamp: {e}")


def main() -> None:
    run_time = datetime.datetime.now(datetime.timezone.utc)
    project = os.getenv("GCP_PROJECT")
    dataset = os.getenv("BQ_DATASET", "activitystreams")
    domain = os.getenv("DOMAIN", "activitystreams.uutisseuranta.net")
    api_key = os.getenv("AHJO_API_KEY")

    if not project:
        logger.critical("GCP_PROJECT environment variable is required.")
        sys.exit(1)

    bq_client = bigquery.Client(project=project)
    existing_ids = get_existing_ids(bq_client, project, dataset, "ahjo")

    # Ahjo REST API endpoint
    api_url = "https://ahjo.hel.fi/api/v1/decisions"
    logger.info(f"Querying Ahjo API: {api_url}")

    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
        # OAuth / Bearer fallback
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        # Haetaan uusimmat päätökset (lisätään parametreja jos uusi API tukee, esim. limit=100)
        resp = httpx.get(f"{api_url}?limit=100", headers=headers, timeout=20, follow_redirects=True)
        # Jos uusi API osoite antaa 404, kokeillaan helsinki-openapi fallbackia
        if resp.status_code == 404:
            fallback_url = "https://helsinki-openapi.api.hel.fi/v1/decisions?limit=100"
            logger.info(f"Ahjo API returned 404, trying fallback: {fallback_url}")
            resp = httpx.get(fallback_url, headers=headers, timeout=20, follow_redirects=True)
            
        resp.raise_for_status()
    except Exception as e:
        logger.critical(f"Ahjo API request failed: {e}")
        sys.exit(1)

    raw_content = resp.content
    write_to_gcs_bronze(project, "ahjo", raw_content, "json")

    try:
        payload = resp.json()
        decisions = payload.get("decisions", []) or payload.get("results", []) or payload.get("value", [])
        if not decisions and isinstance(payload, list):
            decisions = payload
    except Exception as e:
        logger.critical(f"Failed to parse JSON response: {e}")
        sys.exit(1)

    logger.info(f"Retrieved {len(decisions)} decisions from API. Processing...")

    new_articles = []
    for dec in decisions:
        as2_item = build_as2_article(dec, "ahjo", domain)
        if as2_item["id"] not in existing_ids:
            new_articles.append(as2_item)

    logger.info(f"Found {len(new_articles)} new decisions to insert.")

    if new_articles:
        write_to_bigquery(bq_client, project, dataset, new_articles)

    update_last_fetched_timestamp(bq_client, project, dataset, run_time)
    logger.info("Ahjo fetch completed successfully.")


if __name__ == "__main__":
    try:
        main()
        send_ops_notification("ahjo-fetch-job", "success")
    except SystemExit as se:
        if se.code == 0:
            send_ops_notification("ahjo-fetch-job", "success")
        else:
            send_ops_notification("ahjo-fetch-job", "failure", f"SystemExit: {se.code}")
        raise se
    except BaseException as e:
        send_ops_notification("ahjo-fetch-job", "failure", str(e))
        raise e

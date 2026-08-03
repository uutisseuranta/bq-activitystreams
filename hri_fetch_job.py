# hri_fetch_job.py
# Ingests recent datasets from HRI (Helsinki Region Infoshare) CKAN API,
# caches raw JSON to GCS Bronze, and stages unseen items into BigQuery.
import datetime
import hashlib
import json
import os
import sys
import html
from bs4 import BeautifulSoup
from typing import Any, Dict, List, Optional

import httpx
from gcp_logging import get_logger, send_ops_notification
from google.cloud import bigquery, storage

logger = get_logger("hri-fetch-job")


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


def parse_timestamp(ts_str: str) -> Optional[datetime.datetime]:
    """Parses CKAN timestamp string to datetime object."""
    if not ts_str:
        return None
    try:
        # CKAN timestamps are often like "2026-08-03T19:35:22.123456"
        clean_str = ts_str.replace("Z", "+00:00")
        if "T" not in clean_str:
            clean_str = clean_str.replace(" ", "T")
        return datetime.datetime.fromisoformat(clean_str).astimezone(datetime.timezone.utc)
    except Exception:
        return None


def build_as2_article(package: Dict[str, Any], source: str, domain: str) -> Dict[str, Any]:
    """Converts a CKAN package dictionary into AS2 Article format."""
    package_name = package.get("name")
    dataset_url = package.get("url")
    if not dataset_url and package_name:
        dataset_url = f"https://hri.fi/data/fi/dataset/{package_name}"
    elif not dataset_url:
        dataset_url = "https://hri.fi"

    # Generoidaan ID
    input_str = f"{source}{dataset_url}"
    url_hash = hashlib.sha256(input_str.encode("utf-8")).hexdigest()[:16]
    as2_id = f"https://{domain}/ap/objects/{url_hash}"

    # Aikaleimat
    created_dt = parse_timestamp(package.get("metadata_created"))
    modified_dt = parse_timestamp(package.get("metadata_modified")) or created_dt

    pub_str = created_dt.isoformat().replace("+00:00", "Z") if created_dt else None
    upd_str = modified_dt.isoformat().replace("+00:00", "Z") if modified_dt else None

    # Organisaatio
    org = package.get("organization") or {}
    org_name = org.get("title") or org.get("name") or "Helsinki Region Infoshare"

    # Lisenssi
    license_url = package.get("license_url") or "https://creativecommons.org/licenses/by/4.0/deed.fi"

    # Siivotaan HTML-tagit ja unescapataan merkit notes-kentästä
    notes = package.get("notes") or ""
    if notes:
        try:
            soup = BeautifulSoup(notes, "html.parser")
            clean_notes = soup.get_text(separator=" ")
            clean_notes = " ".join(clean_notes.split()).strip()
            summary = html.unescape(clean_notes)
        except Exception:
            summary = notes
    else:
        summary = "Helsinki Region Infoshare avoin aineisto."

    article_json = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "type": "Article",
        "id": as2_id,
        "url": dataset_url,
        "name": package.get("title") or package.get("name") or "HRI Aineisto",
        "summary": summary,
        "published": pub_str,
        "updated": upd_str,
        "attributedTo": {
            "type": "Organization",
            "name": org_name,
            "url": "https://hri.fi"
        },
        "license": license_url
    }

    return {
        "id": as2_id,
        "source": source,
        "published": created_dt,
        "object_json": article_json
    }


def write_to_bigquery(bq_client: bigquery.Client, project: str, dataset: str, items: List[Dict[str, Any]]) -> None:
    """Writes datasets to objects_pending staging table."""
    if not items:
        logger.info("No new datasets to stage.")
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
    config_key = "hri.last_fetched_at"
    query = f"""
        MERGE `{project}.{dataset}.config` T
        USING (SELECT @key AS key, @value AS value) S ON T.key = S.key
        WHEN MATCHED THEN
            UPDATE SET T.value = S.value, T.updated_at = CURRENT_TIMESTAMP(), T.updated_by = 'hri-fetch-job'
        WHEN NOT MATCHED THEN
            INSERT (key, value, updated_at, updated_by)
            VALUES (S.key, S.value, CURRENT_TIMESTAMP(), 'hri-fetch-job')
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

    if not project:
        logger.critical("GCP_PROJECT environment variable is required.")
        sys.exit(1)

    bq_client = bigquery.Client(project=project)
    existing_ids = get_existing_ids(bq_client, project, dataset, "hri")

    # HRI CKAN API package_search query URL
    api_url = "https://hri.fi/data/api/3/action/package_search?sort=metadata_modified+desc&rows=100"
    logger.info(f"Querying HRI CKAN API: {api_url}")

    try:
        resp = httpx.get(api_url, timeout=25, follow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        logger.critical(f"HRI API request failed: {e}")
        sys.exit(1)

    raw_content = resp.content
    write_to_gcs_bronze(project, "hri", raw_content, "json")

    try:
        payload = resp.json()
        packages = payload.get("result", {}).get("results", []) if payload.get("success") else []
    except Exception as e:
        logger.critical(f"Failed to parse JSON response: {e}")
        sys.exit(1)

    logger.info(f"Retrieved {len(packages)} datasets from API. Processing...")

    new_articles = []
    for pkg in packages:
        as2_item = build_as2_article(pkg, "hri", domain)
        if as2_item["id"] not in existing_ids:
            new_articles.append(as2_item)

    logger.info(f"Found {len(new_articles)} new datasets to insert.")

    if new_articles:
        write_to_bigquery(bq_client, project, dataset, new_articles)

    update_last_fetched_timestamp(bq_client, project, dataset, run_time)
    logger.info("HRI fetch completed successfully.")


if __name__ == "__main__":
    try:
        main()
        send_ops_notification("hri-fetch-job", "success")
    except SystemExit as se:
        if se.code == 0:
            send_ops_notification("hri-fetch-job", "success")
        else:
            send_ops_notification("hri-fetch-job", "failure", f"SystemExit: {se.code}")
        raise se
    except BaseException as e:
        send_ops_notification("hri-fetch-job", "failure", str(e))
        raise e

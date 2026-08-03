# lausuntopalvelu_fetch_job.py
# Ingests recent proposals from lausuntopalvelu.fi OData API, caches raw JSON to GCS Bronze,
# and stages unseen items into BigQuery activitystreams.objects_pending.
import datetime
import hashlib
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

import httpx
from gcp_logging import get_logger, send_ops_notification
from google.cloud import bigquery, storage

logger = get_logger("lausuntopalvelu-fetch-job")


def parse_odata_date(date_str: str) -> Optional[datetime.datetime]:
    """Parses OData V2 Date format '/Date(1478174542000)/' or ISO timestamps."""
    if not date_str:
        return None
    m = re.search(r"/Date\((\d+)([+-]\d+)?\)/", date_str)
    if m:
        try:
            ms = int(m.group(1))
            return datetime.datetime.fromtimestamp(ms / 1000.0, datetime.timezone.utc)
        except Exception as e:
            logger.warning(f"OData-päivämäärän muunnos epäonnistui ({date_str}): {e}")
    try:
        # Kokeillaan ISO 8601 -formaattia
        clean_str = date_str.replace("Z", "+00:00")
        return datetime.datetime.fromisoformat(clean_str).astimezone(datetime.timezone.utc)
    except Exception:
        return None


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


def build_as2_article(prop: Dict[str, Any], source: str, domain: str) -> Dict[str, Any]:
    """Converts a proposal dictionary into AS2 Article format."""
    prop_id = prop.get("Id")
    proposal_url = prop.get("Url")
    if not proposal_url and prop_id:
        proposal_url = f"https://www.lausuntopalvelu.fi/FI/Proposal/ProposalDetails?proposalId={prop_id}"
    elif not proposal_url:
        proposal_url = "https://www.lausuntopalvelu.fi"

    # Generoidaan ID
    input_str = f"{source}{proposal_url}"
    url_hash = hashlib.sha256(input_str.encode("utf-8")).hexdigest()[:16]
    as2_id = f"https://{domain}/ap/objects/{url_hash}"

    pub_date = parse_odata_date(prop.get("PublishDate"))
    pub_str = pub_date.isoformat().replace("+00:00", "Z") if pub_date else None

    org_name = prop.get("OrganizationName") or "Lausuntopalvelu"

    article_json = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "type": "Article",
        "id": as2_id,
        "url": proposal_url,
        "name": prop.get("Title") or prop.get("Name") or "Lausuntopyyntö",
        "summary": f"Lausuntopyyntö palvelussa lausuntopalvelu.fi. Julkaisija: {org_name}.",
        "published": pub_str,
        "updated": pub_str,
        "attributedTo": {
            "type": "Organization",
            "name": org_name,
            "url": "https://www.lausuntopalvelu.fi"
        },
        "license": "https://www.lausuntopalvelu.fi/FI/Help/TermsOfService"  # Oletuskäyttöehdot
    }

    return {
        "id": as2_id,
        "source": source,
        "published": pub_date,
        "object_json": article_json
    }


def write_to_bigquery(bq_client: bigquery.Client, project: str, dataset: str, items: List[Dict[str, Any]]) -> None:
    """Writes recent proposals to objects_pending staging table."""
    if not items:
        logger.info("No new proposals to stage.")
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
    config_key = "lausuntopalvelu.last_fetched_at"
    query = f"""
        MERGE `{project}.{dataset}.config` T
        USING (SELECT @key AS key, @value AS value) S ON T.key = S.key
        WHEN MATCHED THEN
            UPDATE SET T.value = S.value, T.updated_at = CURRENT_TIMESTAMP(), T.updated_by = 'lausuntopalvelu-fetch-job'
        WHEN NOT MATCHED THEN
            INSERT (key, value, updated_at, updated_by)
            VALUES (S.key, S.value, CURRENT_TIMESTAMP(), 'lausuntopalvelu-fetch-job')
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
    existing_ids = get_existing_ids(bq_client, project, dataset, "lausuntopalvelu")

    # Haetaan 100 uusinta lausuntopyyntöä OData API:sta JSON-muodossa
    api_url = "https://www.lausuntopalvelu.fi/api/v1/Lausuntopalvelu.svc/Proposals?$orderby=PublishDate desc&$top=100&$format=json"
    logger.info(f"Querying Lausuntopalvelu API: {api_url}")

    try:
        resp = httpx.get(api_url, timeout=20, follow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        logger.critical(f"Lausuntopalvelu API request failed: {e}")
        sys.exit(1)

    raw_content = resp.content
    write_to_gcs_bronze(project, "lausuntopalvelu", raw_content, "json")

    try:
        payload = resp.json()
        # OData V2 yleensä palauttaa datan d-rakenteen alla
        proposals = payload.get("d", {}).get("results", []) if "d" in payload else payload.get("value", [])
        if not proposals and isinstance(payload, list):
            proposals = payload
    except Exception as e:
        logger.critical(f"Failed to parse JSON response: {e}")
        sys.exit(1)

    logger.info(f"Retrieved {len(proposals)} proposals from API. Processing...")

    new_articles = []
    for prop in proposals:
        as2_item = build_as2_article(prop, "lausuntopalvelu", domain)
        if as2_item["id"] not in existing_ids:
            new_articles.append(as2_item)

    logger.info(f"Found {len(new_articles)} new proposals to insert.")

    if new_articles:
        write_to_bigquery(bq_client, project, dataset, new_articles)

    update_last_fetched_timestamp(bq_client, project, dataset, run_time)
    logger.info("Lausuntopalvelu fetch completed successfully.")


if __name__ == "__main__":
    try:
        main()
        send_ops_notification("lausuntopalvelu-fetch-job", "success")
    except SystemExit as se:
        if se.code == 0:
            send_ops_notification("lausuntopalvelu-fetch-job", "success")
        else:
            send_ops_notification("lausuntopalvelu-fetch-job", "failure", f"SystemExit: {se.code}")
        raise se
    except BaseException as e:
        send_ops_notification("lausuntopalvelu-fetch-job", "failure", str(e))
        raise e

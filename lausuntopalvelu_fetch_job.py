# lausuntopalvelu_fetch_job.py
# Ingests recent proposals from lausuntopalvelu.fi OData API, caches raw XML to GCS Bronze,
# and stages unseen items into BigQuery activitystreams.objects_pending.
import datetime
import hashlib
import json
import os
import sys
import html
from bs4 import BeautifulSoup
import defusedxml.ElementTree as ET
from typing import Any, Dict, List, Optional

import httpx
from gcp_logging import get_logger, send_ops_notification
from google.cloud import bigquery, storage
from gcs_bronze import write_to_gcs_bronze

logger = get_logger("lausuntopalvelu-fetch-job")


def parse_xml_date(date_str: str) -> Optional[datetime.datetime]:
    """Parses OData XML ISO timestamps (e.g., '2015-05-13T14:25:24')."""
    if not date_str:
        return None
    try:
        clean_str = date_str.replace("Z", "+00:00")
        if "T" not in clean_str and " " not in clean_str:
            # Päivämäärä-edge-case (esim. "2026-08-03") -> lisätään T00:00:00
            clean_str += "T00:00:00"
        clean_str = clean_str.replace(" ", "T")
        if len(clean_str) <= 19:
            # Jos ei aikavyöhykettä, oletetaan UTC
            clean_str += "+00:00"
        return datetime.datetime.fromisoformat(clean_str).astimezone(datetime.timezone.utc)
    except Exception:
        return None




def get_last_fetched_timestamp(bq_client: bigquery.Client, project: str, dataset: str) -> Optional[str]:
    """Retrieves the last fetched timestamp for Lausuntopalvelu from config table."""
    query = f"SELECT value FROM `{project}.{dataset}.config` WHERE key = 'lausuntopalvelu.last_fetched_at'"
    try:
        query_job = bq_client.query(query)
        results = list(query_job.result())
        if results:
            return results[0].value
    except Exception as e:
        logger.warning(f"Failed to fetch last_fetched_at from config: {e}")
    return None


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
    proposal_url = f"https://www.lausuntopalvelu.fi/FI/Proposal/ProposalDetails?proposalId={prop_id}" if prop_id else "https://www.lausuntopalvelu.fi"

    # Generoidaan ID
    input_str = f"{source}{proposal_url}"
    url_hash = hashlib.sha256(input_str.encode("utf-8")).hexdigest()[:16]
    as2_id = f"https://{domain}/ap/objects/{url_hash}"

    pub_date = parse_xml_date(prop.get("PublishedOn"))
    pub_str = pub_date.isoformat().replace("+00:00", "Z") if pub_date else None

    org_name = prop.get("OrganizationName") or "Lausuntopalvelu"

    # Siivotaan HTML-tagit ja unescapataan Goals-kenttä
    goals = prop.get("Goals") or ""
    if goals:
        try:
            soup = BeautifulSoup(goals, "html.parser")
            clean_goals = soup.get_text(separator=" ")
            clean_goals = " ".join(clean_goals.split()).strip()
            summary = html.unescape(clean_goals)
        except Exception:
            summary = goals
    else:
        summary = f"Lausuntopyyntö palvelussa lausuntopalvelu.fi. Julkaisija: {org_name}."

    article_json = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "type": "Article",
        "id": as2_id,
        "url": proposal_url,
        "name": prop.get("Name") or "Lausuntopyyntö",
        "summary": summary,
        "published": pub_str,
        "updated": pub_str,
        "attributedTo": {
            "type": "Organization",
            "name": org_name,
            "url": "https://www.lausuntopalvelu.fi"
        },
        "license": None
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

    # Haetaan edellisen onnistuneen haun aikaleima OData-suodatusta varten
    last_fetched_raw = get_last_fetched_timestamp(bq_client, project, dataset)
    filter_query = ""
    if last_fetched_raw:
        try:
            dt = datetime.datetime.fromisoformat(last_fetched_raw).astimezone(datetime.timezone.utc)
            dt_str = dt.strftime("%Y-%m-%dT%H:%M:%S")
            filter_query = f"$filter=PublishedOn gt datetime'{dt_str}'&"
            logger.info(f"Using OData filter window: PublishedOn > {dt_str}")
        except Exception as e:
            logger.warning(f"Failed to parse last_fetched_at: {e}")

    # Haetaan lausuntopyynnöt OData API:sta XML-muodossa
    api_url = f"https://www.lausuntopalvelu.fi/api/v1/Lausuntopalvelu.svc/Proposals?{filter_query}$orderby=PublishedOn desc&$top=100"
    
    proposals = []
    current_url = api_url
    page_count = 1

    while current_url:
        logger.info(f"Querying Lausuntopalvelu API page {page_count}: {current_url}")
        try:
            headers = {"User-Agent": "uutisseuranta-fetch-bot/1.0 (+https://uutisseuranta.net)"}
            resp = httpx.get(current_url, headers=headers, timeout=25, follow_redirects=True)
            resp.raise_for_status()
        except Exception as e:
            logger.critical(f"Lausuntopalvelu API request failed on page {page_count}: {e}")
            sys.exit(1)

        raw_content = resp.content
        write_to_gcs_bronze(project, "lausuntopalvelu", raw_content, "xml", source_type="odata_xml")

        # Parsitaan XML Atom feed
        try:
            root = ET.fromstring(raw_content)
            ns = {
                'atom': 'http://www.w3.org/2005/Atom',
                'm': 'http://schemas.microsoft.com/ado/2007/08/dataservices/metadata',
                'd': 'http://schemas.microsoft.com/ado/2007/08/dataservices'
            }
            
            page_proposals = []
            for entry in root.findall('atom:entry', ns):
                content = entry.find('atom:content', ns)
                if content is None:
                    continue
                properties = content.find('m:properties', ns)
                if properties is None:
                    continue
                
                prop_data = {}
                for child in properties:
                    tag_local = child.tag.replace(f"{{{ns['d']}}}", "")
                    prop_data[tag_local] = child.text
                page_proposals.append(prop_data)
            
            proposals.extend(page_proposals)
            logger.info(f"Parsed {len(page_proposals)} proposals from page {page_count}.")

            # Etsitään OData nextLink/seuraava sivu -elementtiä
            next_link = None
            for link in root.findall('atom:link', ns):
                if link.attrib.get('rel') == 'next':
                    next_link = link.attrib.get('href')
                    break
            
            if next_link:
                if next_link.startswith("http"):
                    current_url = next_link
                else:
                    current_url = f"https://www.lausuntopalvelu.fi/api/v1/Lausuntopalvelu.svc/{next_link}"
                page_count += 1
                if page_count > 10:
                    logger.warning("Reached maximum page limit (10). Truncating proposals.")
                    break
            else:
                current_url = None

        except Exception as e:
            logger.critical(f"Failed to parse XML response on page {page_count}: {e}")
            sys.exit(1)

    logger.info(f"Retrieved {len(proposals)} proposals in total. Processing...")

    new_articles = []
    seen_this_run = set()
    for prop in proposals:
        as2_item = build_as2_article(prop, "lausuntopalvelu", domain)
        art_id = as2_item["id"]
        if art_id not in existing_ids and art_id not in seen_this_run:
            new_articles.append(as2_item)
            seen_this_run.add(art_id)

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

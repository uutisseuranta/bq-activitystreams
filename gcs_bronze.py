# gcs_bronze.py
import datetime
from google.cloud import storage
import json
from gcp_logging import get_logger

from typing import Optional

logger = get_logger("gcs-bronze-utils")

def write_to_gcs_bronze(
    project_id: str,
    source: str,
    data: bytes,
    extension: str = "json",
    source_type: str = "user_json",
    etag: Optional[str] = None,
    last_modified: Optional[str] = None
) -> None:
    """Saves raw API response payload and meta.json metadata sidecar to GCS Bronze bucket."""
    try:
        client = storage.Client(project=project_id)
        # Lähdenimi kartoittuu suoraan ämpärin nimeen. Esim. source="hel-fi" -> {project}-bronze-hel-fi.
        # GCS sallii yhdysmerkit (hyphens) bucket-nimissä.
        bucket_name = f"{project_id}-bronze-{source}"
        bucket = client.bucket(bucket_name)
        time_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        
        # 1. Kirjoitetaan raakadata
        filename = f"{time_str}_raw.{extension}"
        blob = bucket.blob(filename)
        content_type = "application/json" if extension == "json" else "application/xml"
        blob.upload_from_string(data, content_type=content_type)
        logger.info(f"Raw data cached to GCS: gs://{bucket_name}/{filename}")

        # 2. Kirjoitetaan meta.json-metadatasivuvaunu (Päätös L-017)
        meta_filename = f"{time_str}_meta.json"
        meta_blob = bucket.blob(meta_filename)
        meta_payload = {
            "source": source,
            "source_type": source_type,
            "extension": extension,
            "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "etag": etag,
            "last_modified": last_modified
        }
        meta_blob.upload_from_string(json.dumps(meta_payload), content_type="application/json")
        logger.info(f"Metadata sidecar cached to GCS: gs://{bucket_name}/{meta_filename}")
    except Exception as e:
        logger.error(f"Failed to save raw data or metadata to GCS Bronze: {e}")

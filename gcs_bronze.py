# gcs_bronze.py
import datetime
from google.cloud import storage
from gcp_logging import get_logger

logger = get_logger("gcs-bronze-utils")

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

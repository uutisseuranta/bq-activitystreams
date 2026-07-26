# src/likes_and_updated_job/main.py
#
# Cloud Run -job: päivittää like_count + dislike_count + objects.updated
#
# Muutoshistoria:
#   #33/#48 — like_count ja dislike_count lasketaan samassa COUNTIF-kyselyssä
#             kustannustehokkuuden vuoksi. 7 päivän ikkuna kattaa myös
#             tilanteet joissa edellinen job-ajo epäonnistui.
import datetime
import json
import logging
import os
import sys

from google.cloud import bigquery


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
logger = logging.getLogger("likes-and-updated-job")


def main() -> None:
    project = os.getenv("GCP_PROJECT")
    dataset = os.getenv("BQ_DATASET")
    social_dataset = os.getenv("BQ_SOCIAL_DATASET", "activitystreams_social")

    if not project or not dataset:
        logger.critical("Virhe: GCP_PROJECT ja BQ_DATASET ovat pakollisia ympäristömuuttujia.")
        sys.exit(1)

    logger.info(f"Käynnistetään synkronointi. Projekti: {project}, Julkinen dataset: {dataset}, Sosiaalinen dataset: {social_dataset}")

    bq_client = bigquery.Client(project=project)

    # VAIHE 1: like_count + dislike_count yhdessä COUNTIF-kyselyssä (#33/#48)
    # 7 päivän ikkuna: kattaa myös tilanteet joissa edellinen ajo epäonnistui.
    # COUNTIF käy activities-taulun kerran — ei kahta erillistä kyselyä.
    reaction_merge_query = f"""
        MERGE `{project}.{dataset}.objects` T
        USING (
          SELECT
            object_id,
            COUNTIF(type = 'Like')    AS like_count,
            COUNTIF(type = 'Dislike') AS dislike_count
          FROM `{project}.{social_dataset}.activities`
          WHERE type IN ('Like', 'Dislike')
            AND DATE(published) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
          GROUP BY object_id
        ) S ON T.id = S.object_id
        WHEN MATCHED AND T.deleted = FALSE THEN
          UPDATE SET
            T.like_count    = S.like_count,
            T.dislike_count = S.dislike_count
    """

    # VAIHE 2: updated-aikaleiman päivitys thread_root-artikkeleille
    updated_merge_query = f"""
        MERGE `{project}.{dataset}.objects` T
        USING (
          SELECT COALESCE(thread_root, object_id) AS root_url,
                 MAX(received_at) AS last_activity_at
          FROM `{project}.{social_dataset}.activities` a
          WHERE type IN ('Like', 'Dislike', 'Create')
            AND NOT EXISTS (
              SELECT 1 FROM `{project}.{social_dataset}.activities` d
              WHERE d.type = 'Delete' AND d.object_id = a.object_id
            )
          GROUP BY root_url
        ) S ON T.id = S.root_url
        WHEN MATCHED AND S.last_activity_at > T.updated THEN
          UPDATE SET T.updated = S.last_activity_at
    """

    try:
        logger.info("Ajetaan Vaihe 1: like_count + dislike_count COUNTIF-aggregointi...")
        reaction_job = bq_client.query(reaction_merge_query)
        reaction_job.result()
        logger.info("Reaktiolaskureiden päivitys suoritettu onnistuneesti.")

        logger.info("Ajetaan Vaihe 2: Aktiivisuus-aikaleimojen päivitys...")
        updated_job = bq_client.query(updated_merge_query)
        updated_job.result()
        logger.info("Aktiivisuus-aikaleimojen päivitys suoritettu onnistuneesti.")

        logger.info("Kaikki synkronointivaiheet suoritettu onnistuneesti loppuun.")

    except Exception as e:
        logger.critical(f"Synkronointi epäonnistui tietokantavirheen vuoksi: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

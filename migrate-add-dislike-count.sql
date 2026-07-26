-- sql/migrate_add_dislike_count.sql
--
-- Idempotentti migraatio: lisää dislike_count INT64 -sarake activitystreams.objects-tauluun.
-- IF NOT EXISTS varmistaa että migraatio on turvallista ajaa uudelleen.
--
-- Viite: #48 AC: skeemamigraatio on idempotentti (IF NOT EXISTS)
-- Ajaminen:
--   bq query --project_id=$GCP_PROJECT --use_legacy_sql=false < sql/migrate_add_dislike_count.sql

ALTER TABLE activitystreams.objects
ADD COLUMN IF NOT EXISTS dislike_count INT64
  OPTIONS(description='AS2 Dislike -aktiviteettien määrä tälle objektille. Päivitetään likes_and_updated_job:ssa.');

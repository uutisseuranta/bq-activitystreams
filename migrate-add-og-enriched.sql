-- SQL Migration: Add og_enriched and og_enriched_error columns to activitystreams.objects
-- Run: bq query --use_legacy_sql=false < sql/migrate_add_og_enriched.sql

ALTER TABLE activitystreams.objects
ADD COLUMN IF NOT EXISTS og_enriched BOOL OPTIONS(description='TRUE kun OG-rikastus on tehty (onnistui tai epäonnistui)'),
ADD COLUMN IF NOT EXISTS og_enriched_error STRING OPTIONS(description='Virheilmoitus jos OG-rikastus epäonnistui, NULL jos onnistui');

-- Alustetaan aiemmin luodut rivit siten, että og_enriched on FALSE
UPDATE activitystreams.objects
SET og_enriched = FALSE
WHERE og_enriched IS NULL;

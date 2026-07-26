-- activitystreams.config
-- Dynaaminen konfiguraatio jobeille. Rivejä ei koskaan poisteta – päivitetään MERGE UPDATE -operaatiolla.
-- Ks. TECHNICAL_DESIGN.md: "Kaikki avain-arvo-parit"

CREATE TABLE IF NOT EXISTS activitystreams.config (
  key           STRING    NOT NULL OPTIONS(description='Konfiguraatioavain, esim. rss.hs.last_fetched_at'),
  value         STRING    NOT NULL OPTIONS(description='Arvo – aina merkkijono'),
  updated_at    TIMESTAMP NOT NULL OPTIONS(description='Viimeisin päivitys'),
  updated_by    STRING             OPTIONS(description='Päivittäjä: Cloud Run Job -palvelun nimi, esim. rss-fetch-job')
);

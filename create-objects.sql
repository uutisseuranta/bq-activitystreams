-- activitystreams.objects
-- Artikkelit, päätökset, datasetit kaikista lähteistä.
-- tags ja like_count ovat erillisten jobien omistamia sarakkeita (ks. TECHNICAL_DESIGN.md).
-- Oletusarvot: tags_enriched=FALSE, like_count=0, deleted=FALSE asetetaan INSERT-lauseissa, ei DDL:ssä.

CREATE TABLE IF NOT EXISTS activitystreams.objects (
  id            STRING    NOT NULL OPTIONS(description='AS2 id – domain-pohjainen IRI, primääriavain'),
  source        STRING    NOT NULL OPTIONS(description='Lähde: rss | ahjo | hri | scraped | user'),
  published     TIMESTAMP NOT NULL OPTIONS(description='AS2 published – pakollinen, taulu on partitionoitu tämän mukaan'),
  updated       TIMESTAMP          OPTIONS(description='AS2 updated – päivittyy käyttäjäaktiivisuudesta (#12)'),
  tags          ARRAY<STRING>      OPTIONS(description='Lemmatisoidut tagit (Voikko #6)'),
  tags_enriched BOOL      NOT NULL OPTIONS(description='TRUE kun Voikko-job on käsitellyt rivin – estää ikuisen uudelleenkäsittelysilmukan'),
  og_enriched   BOOL      NOT NULL OPTIONS(description='TRUE kun OG-rikastus on tehty (onnistui tai epäonnistui)'),
  og_enriched_error STRING             OPTIONS(description='Virheilmoitus jos OG-rikastus epäonnistui, NULL jos onnistui'),
  like_count    INT64     NOT NULL OPTIONS(description='Tykkäysmäärä activitystreams.likes-taulusta, päivitetään likes-and-updated-jobilla (#11/#12)'),
  deleted       BOOL      NOT NULL OPTIONS(description='Pehmeä poisto – rivi pysyy taulussa, deleted=TRUE piilottaa sen hauista'),
  object_json   JSON               OPTIONS(description='Koko AS2-objekti natiivina JSON-tyypinä')
)
PARTITION BY DATE(published)
CLUSTER BY source, published;

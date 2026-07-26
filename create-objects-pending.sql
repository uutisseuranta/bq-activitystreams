-- activitystreams.objects_pending
-- Väliaikainen taulu uutisille, joilta puuttuu julkaisupäivämäärä (pubDate).
-- Siirretään päätauluun 'objects' heti kun päivämäärä saadaan selvitettyä og_enrichment_jobilla.

CREATE TABLE IF NOT EXISTS activitystreams.objects_pending (
  id            STRING    NOT NULL OPTIONS(description='AS2 id – domain-pohjainen IRI'),
  source        STRING    NOT NULL OPTIONS(description='Lähde: rss | valtioneuvosto jne.'),
  received_at   TIMESTAMP NOT NULL OPTIONS(description='Aika jolloin artikkeli haettiin järjestelmään'),
  object_json   JSON               OPTIONS(description='Koko AS2-objekti ilman published-kenttää')
);

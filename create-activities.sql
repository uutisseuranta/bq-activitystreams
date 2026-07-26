-- activitystreams.activities
-- Append-only event log käyttäjätoiminnoille.
-- Jobit #2, #3, #4 EIVÄT kirjoita tähän tauluun – ne kirjoittavat suoraan objects-tauluun.

CREATE TABLE IF NOT EXISTS activitystreams.activities (
  id            STRING    NOT NULL OPTIONS(description='AS2 aktiviteetin id'),
  type          STRING    NOT NULL OPTIONS(description='Create | Update | Delete | Add | Remove | Like'),
  actor         STRING    NOT NULL OPTIONS(description='Käyttäjän tai palvelun AS2 id'),
  object_id     STRING             OPTIONS(description='Kohteen AS2 id'),
  object_type   STRING             OPTIONS(description='Note | Article | Comment jne.'),
  object_json   JSON               OPTIONS(description='Koko kohde-objekti aktiviteetin hetkellä'),
  target_id     STRING             OPTIONS(description='Add/Remove: kokoelman AS2 id'),
  in_reply_to   STRING             OPTIONS(description='Vanhemman objektin AS2 id (kommenttiketju)'),
  thread_root   STRING             OPTIONS(description='Artikkelin AS2 id – aina taso 0, täydennetään palvelimella'),
  published     TIMESTAMP NOT NULL OPTIONS(description='AS2 published – aktiviteetin aika'),
  received_at   TIMESTAMP NOT NULL OPTIONS(description='Palvelimen vastaanottokellonaika')
)
PARTITION BY DATE(published)
CLUSTER BY type, actor;

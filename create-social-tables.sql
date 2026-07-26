-- sql/create_social_tables.sql
-- Alustaa yksityisen/sosiaalisen BigQuery-kannan taulut.
-- Nämä taulut sisältävät henkilötietoja (Google sub/actor-id) ja vaativat autentikoinnin.

CREATE TABLE IF NOT EXISTS activitystreams_social.activities (
  -- Identiteetti
  id            STRING NOT NULL,   -- AS2-aktiviteetin id (ulid tai url)
  type          STRING NOT NULL,   -- Create | Update | Delete | Add | Remove | Like
  actor         STRING NOT NULL,   -- käyttäjän AS2 id (https://activitystreams.uutisseuranta.net/ap/users/{google-sub})

  -- Kohde
  object_id     STRING,            -- Kohteen AS2 id
  object_type   STRING,            -- Note | Article | Comment jne.
  object_json   JSON,              -- Koko AS2 objekti

  -- Add/Remove: kokoelmaoperaatiot
  target_id     STRING,            -- Kohdekokoelman AS2 id

  -- Kommentti/thread-hierarkia
  in_reply_to   STRING,            -- Vanhemman kommentin/artikkelin AS2 id
  thread_root   STRING,            -- Alkuperäisen artikkelin AS2 id (taso 0)

  -- Aikaleimat
  published     TIMESTAMP NOT NULL, -- Käyttäjän asettama / laitteen aika
  received_at   TIMESTAMP NOT NULL  -- Palvelimen vastaanottoaika (luotettavin updated-laskennassa)
)
PARTITION BY DATE(published)
CLUSTER BY type, actor;

CREATE TABLE IF NOT EXISTS activitystreams_social.likes (
  activity_id   STRING NOT NULL,   -- Like-aktiviteetin AS2 id
  actor         STRING NOT NULL,   -- Tykkääjän AS2 id (käytetään duplikaattisuojaukseen)
  object_id     STRING NOT NULL,   -- Tykätyn artikkelin/kommentin AS2 id
  published     TIMESTAMP NOT NULL -- Tykkäyshetki
)
PARTITION BY DATE(published)
CLUSTER BY object_id, actor;

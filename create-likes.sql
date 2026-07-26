-- activitystreams.likes
-- Tykkäykset. actor-sarake on sisäinen (sosiaaliseen BigQueryyn) – avoimeen dataan siirtyy vain like_count objects-taulusta.

CREATE TABLE IF NOT EXISTS activitystreams.likes (
  activity_id   STRING    NOT NULL OPTIONS(description='Viittaus activities-taulun id-kenttään'),
  actor         STRING    NOT NULL OPTIONS(description='Tykkääjän AS2 id – ei siirry avoimeen dataan'),
  object_id     STRING    NOT NULL OPTIONS(description='Tykätyn objektin AS2 id'),
  published     TIMESTAMP NOT NULL OPTIONS(description='Tykkäyksen aikaleima')
)
PARTITION BY DATE(published)
CLUSTER BY object_id, actor;

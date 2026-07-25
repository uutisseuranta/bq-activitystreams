# sql/

BigQuery-taulujen CREATE TABLE -lauseet. Toimivat sekä dokumentaationa että cold-start-skripteinä.

## Projekti ja dataset

- **GCP-projekti:** `uutisseuranta-activitystreams`
- **Dataset:** `activitystreams`
- **Sijainti:** `europe-north1`

## Taulut

| Tiedosto | Taulu | Kuvaus |
|---|---|---|
| `create_objects.sql` | `activitystreams.objects` | Artikkelit, päätökset, datasetit — kaikkien jobien kirjoittama |
| `create_activities.sql` | `activitystreams.activities` | Append-only event log käyttäjätoiminnoille |
| `create_likes.sql` | `activitystreams.likes` | Tykkäykset — sisäinen, ei avoimeen dataan |
| `create_config.sql` | `activitystreams.config` | Dynaaminen konfiguraatio jobeille |

## Ajo

```bash
PROJECT=uutisseuranta-activitystreams

for f in sql/create_*.sql; do
  bq query --project_id=$PROJECT --use_legacy_sql=false < "$f"
done
```

## Viitteet

- [TECHNICAL_DESIGN.md](../TECHNICAL_DESIGN.md) — autoritatiivinen skeemadokumentti
- [DESIGN_GUIDELINES.md](../DESIGN_GUIDELINES.md) — arkkitehtuuriperiaatteet

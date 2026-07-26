# bq-activitystreams

Google Cloud Services -pohjainen ActivityStreams 2.0 -backend, joka tuottaa
Article-, Note- ja Collection-objekteja uutisseuranta.fi-sovellukselle.
Ei täyttä ActivityPub-implementaatiota — tavoitteena AS2-yhteensopivuus.

## Dokumentaatio

- [ARCHITECTURE.md](./ARCHITECTURE.md) — arkkitehtuuri ja muutoshistoria
- [STANDARDS.md](./STANDARDS.md) — normatiiviset vaatimukset (AS2, RFC 3339, GDPR)
- [DESIGN_GUIDELINES.md](./DESIGN_GUIDELINES.md) — backend-spesifiset poikkeamat
- [LICENSES.md](./LICENSES.md) — lisenssit ja attribuutiot

## Deploy

Manuaalinen deploy tehdään [`deploy/deploy.sh`](./deploy/deploy.sh)-skriptillä.
Skripti kopioi `src/shared/`-hakemiston väliaikaisesti palveluiden build-kontekstiin
ennen Docker-buildia (`linux/amd64`), ja siivoaa kopiot buildin jälkeen.

```bash
bash deploy/deploy.sh
```

Tarvittavat esiehdot:
- `docker` kirjautunut `europe-north1-docker.pkg.dev`-rekisteriin (`gcloud auth configure-docker`)
- `gcloud` autentikoitu oikeaan projektiin (`uutisseuranta-activitystreams`)
- `terraform` alustettu (`terraform init` hakemistossa `terraform/`)
- `gh` CLI kirjautunut (`gh auth login`)

## Infrakustannukset

Arvioitu ~$0.10–2/kk normaalikuormalla:
- BigQuery: kyselyt ja tallennus (pay-per-query)
- Cloud Run Jobs: 4 ajastettua jobia (rss-fetch, og-enrichment, voikko, likes-and-updated)
- Cloud Run Services: query-api, write-api, og-scraper (scale-to-zero)
- Cloud Scheduler: 4 triggeriä (europe-west1)
- Artifact Registry: Docker-kuvat
- Secret Manager: google-client-secret

## Ristiin-linkit

- [patterns/STANDARDS.md](https://github.com/uutisseuranta/patterns/blob/main/STANDARDS.md) — frontend AS2-kenttäkartta
- [uutisseuranta/uutisseuranta.github.io](https://github.com/uutisseuranta/uutisseuranta.github.io) — frontend-sovellus

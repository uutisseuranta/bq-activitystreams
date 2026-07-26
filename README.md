# bq-activitystreams

Google Cloud Services -pohjainen ActivityStreams 2.0 -backend, joka tuottaa
Article-, Note- ja Collection-objekteja uutisseuranta.fi-sovellukselle.
Ei täyttä ActivityPub-implementaatiota — tavoitteena AS2-yhteensopivuus.

## Dokumentaatio

- [ARCHITECTURE.md](./ARCHITECTURE.md) — arkkitehtuuri ja muutoshistoria
- [STANDARDS.md](./STANDARDS.md) — normatiiviset vaatimukset (AS2, RFC 3339, GDPR)
- [DESIGN_GUIDELINES.md](./DESIGN_GUIDELINES.md) — backend-spesifiset poikkeamat
- [LICENSES.md](./LICENSES.md) — lisenssit ja attribuutiot

## Infrastruktuuri

GCP-infrastruktuuri ja resurssit (Cloud Run -palvelut ja -jobit, Cloud Scheduler, IAM-oikeudet, BigQuery-taulut sekä Secret Manager) hallitaan koodina Terraformin avulla.
Terraform-määrittelyt sijaitsevat kansiossa [`terraform/`](./terraform/).

## Deploy

Tuotantodeploy tapahtuu automaattisesti CI:ssä (`unit-tests.yml` `deploy`-job)
jokaisella `main`-haaran pushilla. CI käyttää `gcloud run deploy --source`
-komentoa, joka buildaa palvelun Cloud Buildin buildpackeilla suoraan
lähdekoodista — erillisiä Dockerfileja ei tarvita.

Manuaalinen re-deploy yksittäiselle palvelulle:

```bash
gcloud run deploy <palvelu> \
  --source src/<palvelu> \
  --region europe-north1 \
  --project uutisseuranta-activitystreams
```

Palvelut: `query-api`, `write-api`, `og-scraper`

Bootstrap (ensimmäinen deploy tai WIF-secretien uudelleenasetus):
katso [`terraform/DEPLOY.md`](./terraform/DEPLOY.md).

## Infrakustannukset

Arvioitu ~$0.10–2/kk normaalikuormalla:
- BigQuery: kyselyt ja tallennus (pay-per-query)
- Cloud Run Jobs: 4 ajastettua jobia (rss-fetch, og-enrichment, voikko, likes-and-updated)
- Cloud Run Services: query-api, write-api, og-scraper (scale-to-zero)
- Cloud Scheduler: 4 triggeriä (europe-west1)
- Secret Manager: google-client-secret

## Ristiin-linkit

- [patterns/STANDARDS.md](https://github.com/uutisseuranta/patterns/blob/main/STANDARDS.md) — frontend AS2-kenttäkartta
- [uutisseuranta/uutisseuranta.github.io](https://github.com/uutisseuranta/uutisseuranta.github.io) — frontend-sovellus

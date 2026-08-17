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
Terraform-määrittelyt sijaitsevat suoraan repositorion juurihakemistossa (`*.tf` tiedostot).

## Deploy

Tuotantodeploy tapahtuu automaattisesti CI:ssä (`unit-tests.yml` `deploy`-job) jokaisella `main`-haaran pushilla. CI käyttää `gcloud run deploy --source` -komentoa, joka buildaa palvelun Cloud Buildin buildpackeilla suoraan repositorion juuresta:

```bash
gcloud run deploy <palvelu> \
  --source . \
  --region europe-north1 \
  --project uutisseuranta-activitystreams \
  --base-image python312 \
  --command "<käynnistyskomento>"
```

Cloud Run -palvelut (aina käynnissä, scale-to-zero) ja niiden käynnistyskomennot:
* `query-api`: `uvicorn query_api:app --host 0.0.0.0 --port 8080` (CORS-tuki käytössä selainhakuja varten)
* `write-api`: `uvicorn write_api:app --host 0.0.0.0 --port 8080` (CORS-tuki käytössä reaktioiden tallentamista varten)

OG-tietojen rikastus (`og_enrichment_job.py`, käyttää `og_parser.py`-kirjastoa) ei ole oma Cloud Run -palvelu, vaan ajastettu Cloud Run Job — ks. Cloud Run Jobs -lista alla.


Bootstrap (ensimmäinen deploy tai WIF-secretien uudelleenasetus):
katso [`terraform-deploy.md`](./terraform-deploy.md).

## Infrakustannukset

Arvioitu ~$0.10–2/kk normaalikuormalla:
- BigQuery: kyselyt ja tallennus (pay-per-query)
- Cloud Run Jobs: 5 ajastettua jobia (rss-fetch, og-enrichment, voikko, likes-and-updated, lausuntopalvelu-fetch)
- Cloud Run Services: query-api, write-api (scale-to-zero)
- Cloud Scheduler: 6 triggeriä (europe-west1), joista 5 ajastaa Cloud Run Jobeja ja yksi (query-api-keep-warm) pitää query-apin lämpimänä
- Secret Manager: google-client-secret

## Ristiin-linkit

- [patterns/STANDARDS.md](https://github.com/uutisseuranta/patterns/blob/main/STANDARDS.md) — frontend AS2-kenttäkartta
- [uutisseuranta/uutisseuranta.github.io](https://github.com/uutisseuranta/uutisseuranta.github.io) — frontend-sovellus

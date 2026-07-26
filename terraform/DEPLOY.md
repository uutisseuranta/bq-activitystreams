# PR #68 — Manuaaliset toimenpiteet ennen mergea

Tämä ohje kattaa toimenpiteet jotka täytyy tehdä paikallisesti ennen kuin
PR #68 (`feat/infra-ci-deploy-wif-branch-protection-terraform`) voidaan mergata.

---

## Vaatimukset

- `gcloud` CLI asennettu ja autentikoitu
- `terraform` CLI asennettu (versio >= 1.9)
- `gh` CLI asennettu (GitHub CLI)
- Oikeudet GCP-projektiin `uutisseuranta-activitystreams`
- Oikeudet GitHub-repositorioon `uutisseuranta/bq-activitystreams`

---

## Vaihe 1 — GCP-autentikointi

```bash
gcloud auth application-default login
gcloud config set project uutisseuranta-activitystreams
```

Varmista että olet oikealla projektilla:

```bash
gcloud config get-value project
# → uutisseuranta-activitystreams
```

---

## Vaihe 2 — Terraform apply

```bash
cd terraform/
terraform init
terraform plan
```

Tarkista planista että muutokset ovat vain nämä neljä:

| Resurssi | Operaatio |
|---|---|
| `google_iam_workload_identity_pool.github` | create |
| `google_iam_workload_identity_pool_provider.github` | create |
| `google_service_account_iam_member.wif_backend` | create |
| `github_branch_protection.main` | update |

Jos planissa näkyy muita resursseja, tarkista muutokset ennen applya.

```bash
terraform apply
```

---

## Vaihe 3 — Outputit talteen

```bash
terraform output wif_provider
terraform output wif_service_account
```

Outputit ovat muotoa:

```
wif_provider         = "projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/providers/github-provider"
wif_service_account  = "backend-sa@uutisseuranta-activitystreams.iam.gserviceaccount.com"
```

---

## Vaihe 4 — GitHub Secrets

Aseta Secrets komentoriviltä (suositeltu — ei kopiointia käsin):

```bash
gh secret set WIF_PROVIDER \
  --body "$(terraform output -raw wif_provider)" \
  --repo uutisseuranta/bq-activitystreams

gh secret set WIF_SERVICE_ACCOUNT \
  --body "$(terraform output -raw wif_service_account)" \
  --repo uutisseuranta/bq-activitystreams
```

Tai manuaalisesti:
**GitHub → Settings → Secrets and variables → Actions → New repository secret**
- `WIF_PROVIDER` = `wif_provider`-outputin arvo
- `WIF_SERVICE_ACCOUNT` = `wif_service_account`-outputin arvo

> ⚠️ **Järjestys on kriittinen:** Secrets täytyy olla tallennettuna ennen seuraavaa
> push-to-main-tapahtumaa. Jos Secrets puuttuu, `terraform-plan`- ja `deploy`-jobit
> epäonnistuvat välittömästi WIF-autentikoinnissa.

### Miksi Secrets asetetaan manuaalisesti eikä Terraformilla?

Terraform tallentaa kaikkien hallinnoimiensa resurssien arvot `terraform.tfstate`-tiedostoon
selkotekstinä. Jos state on paikallinen tiedosto (ei remote backend kuten GCS),
`github_actions_secret`-resurssit vuotaisivat Secretien arvot levylle.

Lisäksi `github_actions_secret` vaatisi GitHub-tokenin `secrets:write`-scopella
Terraform-ajon aikana — laajempi scope kuin pelkkä `contents:read + actions:read`
jota käytetään nyt.

`WIF_PROVIDER` ja `WIF_SERVICE_ACCOUNT` eivät ole salaisia arvoja (näkyvät
GCP-konsolissa), mutta niiden hallinta erillään Terraform statesta on selkeämpi
ja turvallisempi malli. Jos myöhemmin siirrytään remote backendiin (esim. GCS-bucket
state-tiedostolle), automatisointi `github_actions_secret`-resurssilla on
helppo lisätä — ks. `wif.tf`-kommentit.

---

## Vaihe 5 — Varmista CI ja merge

Tarkista että `unit-test`-job on vihreä PR:n Checks-välilehdeltä:
https://github.com/uutisseuranta/bq-activitystreams/pull/68/checks

Jos CI ei ole käynnissä, laukaise se tyhjällä commitilla:

```bash
git commit --allow-empty -m "ci: trigger unit-test check"
git push
```

Kun `unit-test`-check on vihreä, PR on valmis mergettäväksi.

---

## Vaihe 6 — Post-merge: smoke-test

Ensimmäinen automaattinen deploy käynnistyy push-to-main-mergen yhteydessä.
Deploy ajaa healthcheckin (`/healthz`) kaikille kolmelle palvelulle ja
tekee automaattisen rollbackin jos jokin epäonnistuu.

Mergen jälkeen voit ajaa manuaalisen smoke-testin:

```bash
# GitHub Actions → Actions → Live smoke test → Run workflow
gh workflow run smoke-test.yml --repo uutisseuranta/bq-activitystreams
```

---

## Päätösviitteet

| Päätös | Kuvaus |
|---|---|
| I-001 | WIF GitHub Actions -autentikoinnille (korvaa SA-avaimet) |
| I-002 | CI deploy-vaihe `unit-tests.yml`:ssä `push main` -triggerillä |
| I-003 | Branch protection Terraformiin: `contexts=["unit-test"]`, `enforce_admins=true` |

# terraform/DEPLOY.md — Bootstrap ja deploy-ohje

Tämä ohje kattaa toimenpiteet jotka täytyy tehdä **kerran paikallisesti**
ennen kuin PR #68 voidaan mergata ja CI ottaa hallinnan.

> **Miksi paikallinen ajo?**
> Chicken-and-egg-ongelma: CI tarvitsee WIF:iä autentikoidakseen GCP:hen,
> mutta WIF-resursseja ei ole olemassa ennen ensimmäistä `terraform apply`:a.
> HashiCorpin best practice: bootstrappaa IAM/identity-resurssit kerran
> paikallisesti `-target`-flagilla, jonka jälkeen CI ottaa hallinnan.

---

## Vaatimukset

- `gcloud` CLI asennettu ja autentikoitu
- `terraform` CLI asennettu (versio >= 1.9)
- `gh` CLI asennettu (GitHub CLI)
- Oikeudet GCP-projektiin `uutisseuranta-activitystreams`
- Oikeudet GitHub-repositorioon `uutisseuranta/bq-activitystreams`

---

## Vaihe 0 — Hae PR-branchi ja luo GCS-bucket

```bash
git fetch origin
git checkout feat/infra-ci-deploy-wif-branch-protection-terraform
```

Luo GCS state-bucket (kertaluonteinen — jos bucket on jo olemassa, ohita):

```bash
gsutil mb -l europe-north1 gs://uutisseuranta-activitystreams-tfstate
gsutil versioning set on gs://uutisseuranta-activitystreams-tfstate
```

> **Miksi GCS-backend?**
> Paikallinen `terraform.tfstate` estäisi CI:tä ajamasta `plan`/`apply`:a —
> CI ei pääse käsiksi paikalliseen tiedostoon. Remote backend GCS:ssä
> mahdollistaa CI:n täyden Terraform-hallinnan WIF-autentikoinnilla.
> HashiCorpin suositus: remote backend aina kun useampi henkilö tai CI
> ajaa `apply`:a.

---

## Vaihe 1 — GCP-autentikointi

```bash
gcloud auth application-default login
gcloud config set project uutisseuranta-activitystreams
```

Varmista projekti:

```bash
gcloud config get-value project
# → uutisseuranta-activitystreams
```

---

## Vaihe 2 — Bootstrap: WIF-resurssit ensin, sitten remote backend

Bootstrappaus tehdään kahdessa vaiheessa koska GCS-bucket täytyy olla
olemassa ennen kuin backend voidaan aktivoida.

### Vaihe 2a — Aja WIF ilman backendia

```bash
cd terraform/
terraform init -backend=false
terraform apply \
  -target=google_iam_workload_identity_pool.github \
  -target=google_iam_workload_identity_pool_provider.github \
  -target=google_service_account_iam_member.wif_backend
```

Tarkista planista että muutokset ovat **vain nämä kolme**:

| Resurssi | Operaatio |
|---|---|
| `google_iam_workload_identity_pool.github` | create |
| `google_iam_workload_identity_pool_provider.github` | create |
| `google_service_account_iam_member.wif_backend` | create |

### Vaihe 2b — Aktivoi GCS-backend ja migroi state

```bash
terraform init -migrate-state
```

Terraform kysyy: `Do you want to copy existing state to the new backend?`
Vastaa: **yes**

Varmista että state siirtyi:

```bash
terraform state list
# → näyttää juuri luodut WIF-resurssit
```

### Vaihe 2c — Täysi apply lopulle infralle

```bash
terraform plan
terraform apply
```

Tarkista planista että `google_iam_workload_identity_pool.*`-resurssit näkyvät
tilassa `no changes` (jo luotu vaiheessa 2a). Muut resurssit luodaan tai
päivitetään normaalisti.

---

## Vaihe 3 — Outputit talteen

```bash
terraform output wif_provider
terraform output wif_service_account
```

Outputit ovat muotoa:

```
wif_provider        = "projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/providers/github-provider"
wif_service_account = "backend-sa@uutisseuranta-activitystreams.iam.gserviceaccount.com"
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
> push-to-main-tapahtumaa. Jos Secrets puuttuu, `deploy`-job kaatuu
> selkeällä virheilmoituksella (ei hiljaisella WIF-auth-virheellä).

### Miksi Secrets asetetaan manuaalisesti eikä Terraformilla?

Terraform tallentaa kaikkien hallinnoimiensa resurssien arvot `terraform.tfstate`-tiedostoon
selkotekstinä. `WIF_PROVIDER` ja `WIF_SERVICE_ACCOUNT` eivät ole salaisia arvoja
(näkyvät GCP-konsolissa), mutta niiden hallinta erillään Terraform statesta on
selkeämpi malli. Ks. `wif.tf`-kommentit.

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
Mergen jälkeen voit ajaa manuaalisen smoke-testin:

```bash
gh workflow run smoke-test.yml --repo uutisseuranta/bq-activitystreams
```

---

## Päätösviitteet

| Päätös | Kuvaus |
|---|---|
| I-001 | WIF GitHub Actions -autentikoinnille (korvaa SA-avaimet) |
| I-002 | CI deploy-vaihe `unit-tests.yml`:ssä `push main` -triggerillä |
| I-003 | Branch protection Terraformiin: `contexts=["unit-test"]`, `enforce_admins=true` |
| I-004 | Cloud Run `--source` deploy-strategia (ei Dockerfileja) |

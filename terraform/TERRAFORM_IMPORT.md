# Terraform Import – bq-activitystreams

Tämä tiedosto dokumentoi `terraform import` -komennot kaikille resursseille
jotka saattavat olla olemassa Google Cloudissa tai GitHubissa ennen
`terraform apply`:n ensimmäistä ajoa.

Bq-activitystreams on backend-repo: importoitavia resursseja on sekä
GCP-puolella (palvelutili, Artifact Registry, Cloud Run, Scheduler, WIF)
että GitHub-puolella (branch protection, labelit).

## Edellytykset

```bash
cd terraform/

# GitHub
export GITHUB_TOKEN="ghp_..."

# Google Cloud – käytä Workload Identity Federationia (WIF) tai avaintiedostoa
export GOOGLE_APPLICATION_CREDENTIALS="~/.config/gcloud/application_default_credentials.json"
# TAI: gcloud auth application-default login

# Aseta muuttujat
cp terraform.tfvars.example terraform.tfvars
# Muokkaa terraform.tfvars: google_client_id, bq_dataset jne.

terraform init
```

---

## 1. IAM – Palvelutili

Jos `deploy/init-sa.sh` on jo ajettu, palvelutili on olemassa.

```bash
# Muoto: projects/<project>/serviceAccounts/<sa_email>
terraform import google_service_account.deploy \
  "projects/uutisseuranta-activitystreams/serviceAccounts/deploy-sa@uutisseuranta-activitystreams.iam.gserviceaccount.com"
```

**IAM-bindingeille ei voi käyttää importia** – ne luodaan aina uusina (idempotent).
Jos Terraform haluaa poistaa olemassa olevan bindingin, tarkista
`terraform plan` -tulostus ennen `apply`:tä.

---

## 2. Artifact Registry

```bash
# Muoto: projects/<project>/locations/<location>/repositories/<name>
terraform import google_artifact_registry_repository.docker \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/repositories/uutisseuranta"
```

> **Huom:** `prevent_destroy = true` on asetettu. Terraform ei anna poistaa
> repositoriota vahingossa.

---

## 3. Cloud Run – Palvelut

Jos palvelut on jo otettu käyttöön (Cloud Console tai `gcloud run deploy`):

```bash
# Muoto: locations/<region>/namespaces/<project>/services/<name>
terraform import google_cloud_run_v2_service.query_api \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/services/query-api"

terraform import google_cloud_run_v2_service.write_api \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/services/write-api"

terraform import google_cloud_run_v2_service.og_scraper \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/services/og-scraper"
```

---

## 4. Cloud Run – Jobit

```bash
terraform import google_cloud_run_v2_job.rss_fetch \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/rss-fetch-job"

terraform import google_cloud_run_v2_job.voikko \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/voikko-job"

terraform import google_cloud_run_v2_job.og_enrichment \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/og-enrichment-job"

terraform import google_cloud_run_v2_job.likes_and_updated \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/likes-and-updated-job"
```

---

## 5. Cloud Scheduler

```bash
# Muoto: projects/<project>/locations/<region>/jobs/<name>
terraform import google_cloud_scheduler_job.rss_pipeline \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/rss-pipeline"

terraform import google_cloud_scheduler_job.og_enrichment_schedule \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/og-enrichment-schedule"

terraform import google_cloud_scheduler_job.likes_and_updated_schedule \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/likes-and-updated-schedule"
```

---

## 6. GitHub Branch Protection

```bash
terraform import github_branch_protection.main "bq-activitystreams:main"
```

---

## 7. Workload Identity Federation (WIF)

WIF-resurssit on määritelty `terraform/wif.tf`:ssä. Ne luodaan `apply`:ssä
automatically jos eivät ole olemassa. Jos pooli tai provider on luotu jo
käsin Cloud Consolessa, importoi ne ennen `apply`:tä:

```bash
# WIF-pooli
# Muoto: projects/<project>/locations/global/workloadIdentityPools/<pool-id>
terraform import google_iam_workload_identity_pool.github \
  "projects/uutisseuranta-activitystreams/locations/global/workloadIdentityPools/github-pool"

# OIDC-provider
# Muoto: projects/<project>/locations/global/workloadIdentityPools/<pool-id>/providers/<provider-id>
terraform import google_iam_workload_identity_pool_provider.github \
  "projects/uutisseuranta-activitystreams/locations/global/workloadIdentityPools/github-pool/providers/github-provider"

# IAM-binding (yleensä ei importoida – idempotent)
# Jos backend-SA:n binding on jo olemassa, Terraform ylikirjoittaa sen harmittomasti
```

> **Huom:** `google_service_account_iam_member.wif_backend` on idempotent –
> sitä ei tarvitse importoida. `apply` lisää bindingin jos se puuttuu tai
> vahvistaa sen jos se on jo olemassa.

---

## 8. GitHub Labelit – Shell-skripti

```bash
#!/usr/bin/env bash
# Tallenna: terraform/import_labels.sh
# Käyttö: bash import_labels.sh

set -e
REPO="bq-activitystreams"

declare -A LABELS
# Jira-sync
LABELS["priority_highest"]="priority:highest"
LABELS["priority_high_jira"]="priority:high"
LABELS["priority_medium"]="priority:medium"
LABELS["priority_low"]="priority:low"
LABELS["priority_lowest"]="priority:lowest"
LABELS["sprint_1"]="sprint:1"
LABELS["sprint_2"]="sprint:2"
LABELS["sprint_3"]="sprint:3"
LABELS["sprint_4"]="sprint:4"
LABELS["sprint_5"]="sprint:5"
LABELS["status_todo"]="status:to-do"
LABELS["status_in_progress"]="status:in-progress"
LABELS["status_in_review"]="status:in-review"
LABELS["status_done"]="status:done"

for resource_name in "${!LABELS[@]}"; do
  label_name="${LABELS[$resource_name]}"
  echo "Importing github_issue_label.${resource_name} <- ${label_name}"
  terraform import "github_issue_label.${resource_name}" "${REPO}:${label_name}" || \
    echo "  SKIP: ${label_name} ei löydy reposta (luodaan apply:ssä)"
done

echo ""
echo "Import valmis. Aja seuraavaksi: terraform plan"
```

---

## 9. Täydellinen workflow ennen ensimmäistä apply:tä

```bash
cd terraform/

# 1. Alusta
terraform init

# 2. GCP-resurssit (jos jo olemassa)
terraform import google_service_account.deploy \
  "projects/uutisseuranta-activitystreams/serviceAccounts/deploy-sa@uutisseuranta-activitystreams.iam.gserviceaccount.com"

terraform import google_artifact_registry_repository.docker \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/repositories/uutisseuranta"

# Cloud Run -palvelut (jos jo olemassa)
terraform import google_cloud_run_v2_service.query_api \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/services/query-api"
terraform import google_cloud_run_v2_service.write_api \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/services/write-api"
terraform import google_cloud_run_v2_service.og_scraper \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/services/og-scraper"

# Cloud Run -jobit (jos jo olemassa)
terraform import google_cloud_run_v2_job.rss_fetch \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/rss-fetch-job"
terraform import google_cloud_run_v2_job.voikko \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/voikko-job"
terraform import google_cloud_run_v2_job.og_enrichment \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/og-enrichment-job"
terraform import google_cloud_run_v2_job.likes_and_updated \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/likes-and-updated-job"

# Scheduler (jos jo olemassa)
terraform import google_cloud_scheduler_job.rss_pipeline \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/rss-pipeline"
terraform import google_cloud_scheduler_job.og_enrichment_schedule \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/og-enrichment-schedule"
terraform import google_cloud_scheduler_job.likes_and_updated_schedule \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/likes-and-updated-schedule"

# WIF (jos jo olemassa GCP:ssä)
terraform import google_iam_workload_identity_pool.github \
  "projects/uutisseuranta-activitystreams/locations/global/workloadIdentityPools/github-pool"
terraform import google_iam_workload_identity_pool_provider.github \
  "projects/uutisseuranta-activitystreams/locations/global/workloadIdentityPools/github-pool/providers/github-provider"

# 3. GitHub
terraform import github_branch_protection.main "bq-activitystreams:main"
bash import_labels.sh

# 4. Tarkista
terraform plan

# 5. Aja
terraform apply
```

---

## 10. WIF-käyttöönottoguide (GitHub Actions)

Tämä luku kattaa kolme vaihetta joita tarvitaan **WIF:n aktivoimiseen GitHub
Actionsissa** sen jälkeen kun `terraform apply` on ajettu.

### Vaihe 1 — `terraform apply`

```bash
cd terraform/
terraform init
terraform plan   # tarkista diff: pitäisi luoda wif-pooli + provider + IAM-binding
terraform apply
```

Mitä `apply` luo (`terraform/wif.tf`):

| Resurssi | Nimi GCP:ssä | Kuvaus |
|---|---|---|
| `google_iam_workload_identity_pool.github` | `github-pool` | WIF-pooli GitHub Actionsille |
| `google_iam_workload_identity_pool_provider.github` | `github-provider` | OIDC-provider (`token.actions.githubusercontent.com`) |
| `google_service_account_iam_member.wif_backend` | — | Backend-SA:n personointioikeus poolille |

Mitä `apply` päivittää (`terraform/github.tf`):

| Resurssi | Muutos |
|---|---|
| `github_branch_protection.main` | `contexts=["unit-test"]` (korjattu), `enforce_admins=true`, force push estetty |

### Vaihe 2 — GitHub Secrets

Hae arvot Terraformin outputeista:

```bash
terraform output wif_provider
# Esim: projects/123456789/locations/global/workloadIdentityPools/github-pool/providers/github-provider

terraform output wif_service_account
# Esim: backend@uutisseuranta-activitystreams.iam.gserviceaccount.com
```

Aseta seuraavat secretit GitHubissa:
**Settings → Secrets and variables → Actions → New repository secret**

| Secret-nimi | Arvo (komennon tulos) |
|---|---|
| `WIF_PROVIDER` | `terraform output wif_provider` |
| `WIF_SERVICE_ACCOUNT` | `terraform output wif_service_account` |

> **Huom:** Arvot ovat pitkiä resurssinimiä – kopioi ne täsmälleen ilman
> rivinvaihtoja tai lainausmerkkejä.

### Vaihe 3 — Varmistus

Odota ensin että GitHub Secrets on tallennettu. Tee sen jälkeen tyhjä
test-push:

```bash
git commit --allow-empty -m "chore: test CI deploy trigger"
git push origin main
```

Odotettu tulos GitHub Actionsissa
(**Actions → unit-tests.yml → viimeisin ajo**):

| Vaihe | Tila | Mitä tapahtuu |
|---|---|---|
| `unit-test` | ✅ vihreä | Ruff + yksikkötestit + STANDARDS.md-tarkistus |
| `deploy` | ✅ vihreä | WIF-autentikointi + Cloud Run deploy × 3 |

Sekä manuaalisesti:

```
Actions → Live smoke test → Run workflow (workflow_dispatch)
```

| Vaihe | Tila |
|---|---|
| WIF-autentikointi | ✅ vihreä |
| `bash live-smoke-test.sh` | ✅ vihreä |

Jos `deploy`-vaihe epäonnistuu `PERMISSION_DENIED`-virheellä:
1. Tarkista että Secrets on asetettu oikein (ei ylimääräisiä välejä)
2. Tarkista että `terraform apply` on ajettu ja WIF-pooli on olemassa GCP:ssä:
   `gcloud iam workload-identity-pools list --location=global --project=uutisseuranta-activitystreams`
3. Tarkista että backend-SA:lla on `roles/run.admin`:
   `gcloud projects get-iam-policy uutisseuranta-activitystreams --flatten="bindings[].members" --filter="bindings.members:backend@"`

---

## 11. Virhetilanteet

### `Error: googleapi: Error 409: Resource already exists`
Resurssi on GCP:ssä mutta ei Terraform state:ssa. Aja kyseinen import-komento.

### `Error: Error acquiring the state lock`
Jos käytät GCS-backendia ja edellinen ajo jumi. Poista lukko:
```bash
terraform force-unlock <LOCK_ID>
```

### `Error: Service account does not exist`
Palvelutili on poistettu tai projekti on väärä. Tarkista `var.gcp_project`.

### Cloud Run 404 importissa
Palvelu ei ole olemassa. Terraform luo sen `apply`:ssä. Ei tarvita importia.

### `Error: Permission denied on Cloud Run`
Palvelutilillä pitää olla `roles/run.admin` sekä `roles/iam.serviceAccountUser`.

### `PERMISSION_DENIED` GitHub Actionsissa (WIF)
Ks. Vaihe 3 -vianmääritys yllä.

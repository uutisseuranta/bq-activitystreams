# terraform/iam.tf
# Palvelukohtaiset service accountit ja IAM-bindinkit.
#
# ARKKITEHTUURIPÄÄTÖS (kaanonpäätös G-010):
#   Aiempi yhden SA:n malli ("backend") on korvattu neljällä palvelukohtaisella SA:lla.
#   Perustelu: blast radius pienenee merkittävästi — query-api-kompromissi ei anna
#   kirjoitusoikeutta BigQueryyn eikä pääsyä Secret Manageriin.
#
#   SA-jako:
#     sa-query-api   → bigquery.dataViewer (datasetti-taso) + bigquery.user
#     sa-write-api   → bigquery.dataEditor (datasetti-taso) + bigquery.user + secretAccessor
#     sa-og-scraper  → bigquery.dataEditor (og_cache-datasetti, datasetti-taso) + run.invoker (write-api kutsuoikeus)
#     backend        → deploy-SA: cloudbuild.builds.editor + storage.objectCreator + WIF-binding
#
# VÄHIMMÄISOIKEUSPERIAATE:
#   - Projekti-tason bigquery.dataEditor POISTETTU. Korvattu datasetti-tason bindingillä.
#   - bigquery.user säilyy projekti-tasolla (pakollinen kyselyjobien ajamiseen — ei datasetti-tason vaihtoehto).
#   - Secret Manager -oikeus vain write-api SA:lle (secrets.tf).
#
# Issue-viittaukset:
#   #28  Security Hardening – service account vähimmäisoikeudet
#   #71  IAM: Cloud Build -roolit + palvelukohtaiset SA:t
#   #72  BQ lifecycle + datasetti-tason IAM

# ---------------------------------------------------------------------------
# SA: query-api (julkinen read-only endpoint)
# ---------------------------------------------------------------------------

resource "google_service_account" "query_api" {
  account_id   = "sa-query-api"
  display_name = "query-api"
  description  = "ActivityStreams query-api: read-only BigQuery"
  project      = var.gcp_project
}

# bigquery.user: pakollinen projekti-tasolla kyselyjobien ajamiseen
resource "google_project_iam_member" "query_api_bq_user" {
  #checkov:skip=CKV_GCP_34: bigquery.user on pakollinen projekti-tason rooli — kyselyjobien ajaminen vaatii sen
  project = var.gcp_project
  role    = "roles/bigquery.user"
  member  = "serviceAccount:${google_service_account.query_api.email}"
}

# ---------------------------------------------------------------------------
# SA: write-api (IAM-suojattu kirjoitusendpoint)
# ---------------------------------------------------------------------------

resource "google_service_account" "write_api" {
  account_id   = "sa-write-api"
  display_name = "write-api"
  description  = "ActivityStreams write-api: BQ-kirjoitus + Secret Manager"
  project      = var.gcp_project
}

# bigquery.user: pakollinen projekti-tasolla
resource "google_project_iam_member" "write_api_bq_user" {
  #checkov:skip=CKV_GCP_34: bigquery.user on pakollinen projekti-tason rooli
  project = var.gcp_project
  role    = "roles/bigquery.user"
  member  = "serviceAccount:${google_service_account.write_api.email}"
}

# og-scraper kutsuu write-apita Cloud Run IAM:n kautta
resource "google_cloud_run_v2_service_iam_member" "write_api_invoker" {
  project  = var.gcp_project
  location = var.region
  name     = google_cloud_run_v2_service.write_api.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.og_scraper.email}"
}

# ---------------------------------------------------------------------------
# SA: og-scraper (IAM-suojattu scraper)
# ---------------------------------------------------------------------------

resource "google_service_account" "og_scraper" {
  account_id   = "sa-og-scraper"
  display_name = "og-scraper"
  description  = "ActivityStreams og-scraper: OG-metatiedot + og_cache-kirjoitus"
  project      = var.gcp_project
}

# bigquery.user: pakollinen projekti-tasolla
resource "google_project_iam_member" "og_scraper_bq_user" {
  #checkov:skip=CKV_GCP_34: bigquery.user on pakollinen projekti-tason rooli
  project = var.gcp_project
  role    = "roles/bigquery.user"
  member  = "serviceAccount:${google_service_account.og_scraper.email}"
}

# ---------------------------------------------------------------------------
# SA: backend (deploy-SA — käytetään vain CI/CD-deployssa, ei runtime)
# ---------------------------------------------------------------------------

resource "google_service_account" "backend" {
  account_id   = var.sa_name
  display_name = "backend"
  description  = "ActivityStreams deploy-SA: Cloud Build + WIF. Ei runtime-käyttöä."
  project      = var.gcp_project
}

# Cloud Build source deploy
resource "google_project_iam_member" "backend_cloudbuild" {
  #checkov:skip=CKV_GCP_34: Cloud Build source deploy vaatii projekti-tason roolin
  project = var.gcp_project
  role    = "roles/cloudbuild.builds.editor"
  member  = "serviceAccount:${google_service_account.backend.email}"
}

# GCS-kirjoitusoikeus Cloud Build source uploadille
resource "google_project_iam_member" "backend_storage_source" {
  #checkov:skip=CKV_GCP_34: Cloud Build source upload vaatii GCS-kirjoitusoikeuden
  # Huom: roles/storage.objectCreator antaa oikeuden kaikkiin projektin GCS-bucketeihin.
  # Rajaus Cloud Build -bucketille resurssitasolla ei ole mahdollinen gcloud run deploy --source -polulla.
  project = var.gcp_project
  role    = "roles/storage.objectCreator"
  member  = "serviceAccount:${google_service_account.backend.email}"
}

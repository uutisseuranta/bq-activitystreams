# terraform/iam.tf
# Korvaa deploy/init-sa.sh:n kokonaan.
#
# Resurssit:
#   - IAM-palvelutili "backend"
#   - roles/bigquery.dataEditor  (taulujen luku + kirjoitus)
#   - roles/bigquery.user        (kyselyjobien ajaminen)
#   - roles/run.invoker          (write-api ja og-scraper: Cloud Run IAM -kutsu)
#   - roles/secretmanager.secretAccessor  (google-client-secret lukuoikeus)
#
# Issue-viittaukset:
#   #28  Security Hardening – service account vähimmäisoikeudet

resource "google_service_account" "backend" {
  account_id   = var.sa_name
  display_name = "backend"
  description  = "ActivityStreams backend service account"
  project      = var.gcp_project
}

locals {
  sa_email = google_service_account.backend.email
}

resource "google_project_iam_member" "bq_data_editor" {
  project = var.gcp_project
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${local.sa_email}"
}

resource "google_project_iam_member" "bq_user" {
  project = var.gcp_project
  role    = "roles/bigquery.user"
  member  = "serviceAccount:${local.sa_email}"
}

# Secret Manager -lukuoikeus: tarvitaan google-client-secret:n lukemiseen
# Cloud Run -konttien käynnistyksen yhteydessä (cloudrun_services.tf).
# secrets.tf määrittelee dataset-tason secretAccessor-bindingin lisäksi;
# tämä projekti-tason binding varmistaa ettei uusia salaisuuksia tarvitse
# erikseen valtuuttaa jos niitä lisätään myöhemmin.
resource "google_project_iam_member" "secret_accessor" {
  project = var.gcp_project
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${local.sa_email}"
}

# Cloud Run Invoker write-api:lle (IAM-suojattu, ei julkinen)
resource "google_cloud_run_v2_service_iam_member" "write_api_invoker" {
  project  = var.gcp_project
  location = var.region
  name     = google_cloud_run_v2_service.write_api.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${local.sa_email}"
}

# Cloud Run Invoker og-scraperille (IAM-suojattu, ei julkinen)
resource "google_cloud_run_v2_service_iam_member" "og_scraper_invoker" {
  project  = var.gcp_project
  location = var.region
  name     = google_cloud_run_v2_service.og_scraper.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${local.sa_email}"
}

# terraform/iam.tf
# Korvaa deploy/init-sa.sh:n kokonaan.
#
# TESTISTRATEGIA — ks. TERRAFORM_TESTING.md, Kerros 2
#
#   terraform plan (CI, PR) validoi IAM-roolit GCP-provider-skeemaa vasten.
#   Checkov (terraform-lint job) tarkistaa vähimmäisoikeus-periaatteen:
#   CKV_GCP_34 (project-level roles) on skipattu perustellusti koska
#   bigquery.dataEditor ja bigquery.user ovat projekti-tason rooleja —
#   BQ-datasetti-tason IAM ei ole mahdollinen näillä rooleilla.
#
# VÄHIMMÄISOIKEUSPERIAATE (kaanonpäätös G-009):
#   Projekti-tason secretAccessor on POISTETTU. Secret Manager -oikeus on
#   annettu palvelukohtaisesti secrets.tf:ssä (secret_id-tason binding).
#   Tämä rajoittaa kompromissin vaikutuksen: kompromissoitu SA ei pääse
#   kaikkiin projektissa oleviin secreteihin.
#
# Resurssit:
#   - IAM-palvelutili "backend"
#   - roles/bigquery.dataEditor  (taulujen luku + kirjoitus)
#   - roles/bigquery.user        (kyselyjobien ajaminen)
#   - roles/run.invoker          (write-api ja og-scraper: Cloud Run IAM -kutsu)
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
  #checkov:skip=CKV_GCP_34: bigquery.dataEditor on pakollinen projekti-tason rooli —
  # BQ-datasetti-tason IAM ei ole mahdollinen tällä roolilla. Projekti-tason
  # secretAccessor ON poistettu (G-009). Kompensaatio: secret-oikeudet annettu
  # palvelukohtaisesti secrets.tf:ssä (secret_id-tason binding).
  project = var.gcp_project
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${local.sa_email}"
}

resource "google_project_iam_member" "bq_user" {
  #checkov:skip=CKV_GCP_34: bigquery.user on pakollinen projekti-tason rooli —
  # kyselyjobien ajaminen vaatii projekti-tason oikeuden, datasetti-tason
  # IAM ei riitä. Ks. bq_data_editor -annotaatio lisäperusteluille.
  project = var.gcp_project
  role    = "roles/bigquery.user"
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

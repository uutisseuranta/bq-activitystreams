# terraform/bigquery.tf
# BigQuery-datasetit ja ydintaulut.
#
# Kaksi datasettia:
#   activitystreams        – artikkelit, syötteet, OG-välimuisti
#   activitystreams_social – käyttäjäkohtaiset tykkäykset (erillinen dataset
#                            tietosuojan kannalta: henkilötieto eristetty)
#
# Taulujen skeemoja ei hallita Terraformilla (schema = []) vaan
# sovellustason migraatioilla (generate-standards.sh + STANDARDS.md).
# Terraform vastaa vain resurssien olemassaolosta ja elinkaaresta.
#
# prevent_destroy = true: estää vahingollisen 'terraform destroy'.
# Poista väliaikaisesti jos dataset täytyy oikeasti poistaa.

# ---------------------------------------------------------------------------
# Dataset: activitystreams
# ---------------------------------------------------------------------------

resource "google_bigquery_dataset" "main" {
  dataset_id                 = var.bq_dataset            # "activitystreams"
  location                   = var.region
  delete_contents_on_destroy = false

  lifecycle {
    prevent_destroy = true
  }
}

# Artikkelitaulu – RSS-syötteistä parsitut uutisartikkelit
resource "google_bigquery_table" "articles" {
  dataset_id          = google_bigquery_dataset.main.dataset_id
  table_id            = "articles"
  deletion_protection = true

  # Skeema hallitaan sovellustason migraatioilla; Terraform ei muuta
  # olemassa olevan taulun sarakkeita. Lisää schema-lohko vain jos taulua
  # ei ole vielä olemassa ja haluat Terraformin luoda sen oikealla skeemalla.
  schema = jsonencode([])
}

# Syötetaulu – seurattavat RSS-syötteet
resource "google_bigquery_table" "feeds" {
  dataset_id          = google_bigquery_dataset.main.dataset_id
  table_id            = "feeds"
  deletion_protection = true
  schema              = jsonencode([])
}

# OG-välimuisti – Open Graph -metatiedot artikkeleille
resource "google_bigquery_table" "og_cache" {
  dataset_id          = google_bigquery_dataset.main.dataset_id
  table_id            = "og_cache"
  deletion_protection = true
  schema              = jsonencode([])
}

# ---------------------------------------------------------------------------
# Dataset: activitystreams_social
# Käyttäjäkohtainen data erotettu omaan datasettiin tietosuojan vuoksi.
# ---------------------------------------------------------------------------

resource "google_bigquery_dataset" "social" {
  dataset_id                 = var.bq_social_dataset     # "activitystreams_social"
  location                   = var.region
  delete_contents_on_destroy = false

  lifecycle {
    prevent_destroy = true
  }
}

# Tykkäykset – käyttäjä × artikkeli -parit
resource "google_bigquery_table" "social_likes" {
  dataset_id          = google_bigquery_dataset.social.dataset_id
  table_id            = "social_likes"
  deletion_protection = true
  schema              = jsonencode([])
}

# ---------------------------------------------------------------------------
# IAM – palvelutilille lukuoikeus molempiin datasetteihin
# (kirjoitusoikeus tulee projekti-tason bigquery.dataEditor-roolista iam.tf:ssä)
# ---------------------------------------------------------------------------

resource "google_bigquery_dataset_iam_member" "main_reader" {
  dataset_id = google_bigquery_dataset.main.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_bigquery_dataset_iam_member" "social_reader" {
  dataset_id = google_bigquery_dataset.social.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.backend.email}"
}

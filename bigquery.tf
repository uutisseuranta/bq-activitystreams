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
# lifecycle.ignore_changes = [schema]: estää terraform apply:n ylikirjoittamasta
# sovellustason migraatioiden luomaa skeemaa. Pakollinen kun schema = jsonencode([]).
#
# prevent_destroy = true: estää vahingollisen 'terraform destroy'.
# Poista väliaikaisesti jos dataset täytyy oikeasti poistaa.
#
# Datasetti-tason IAM (G-010):
#   Projekti-tason bigquery.dataEditor on poistettu iam.tf:stä.
#   Kirjoitusoikeudet annetaan datasetti-tasolla palvelukohtaisille SA:ille.
#   query-api saa dataViewer-oikeuden vain activitystreams-datasettiin.

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

# query-api: read-only activitystreams-datasettiin
resource "google_bigquery_dataset_iam_member" "query_api_bq_viewer" {
  dataset_id = google_bigquery_dataset.main.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.query_api.email}"
}

# write-api: kirjoitusoikeus activitystreams-datasettiin
resource "google_bigquery_dataset_iam_member" "write_api_bq_editor_main" {
  dataset_id = google_bigquery_dataset.main.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.write_api.email}"
}

# og-scraper: kirjoitusoikeus activitystreams-datasettiin (og_cache)
resource "google_bigquery_dataset_iam_member" "og_scraper_bq_editor_main" {
  dataset_id = google_bigquery_dataset.main.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.og_scraper.email}"
}

# Artikkelitaulu – RSS-syötteistä parsitut uutisartikkelit
resource "google_bigquery_table" "articles" {
  dataset_id          = google_bigquery_dataset.main.dataset_id
  table_id            = "articles"
  deletion_protection = true
  schema              = jsonencode([])

  lifecycle {
    # Skeema hallitaan sovellustason migraatioilla — terraform apply ei ylikirjoita
    ignore_changes = [schema]
  }
}

# Syötetaulu – seurattavat RSS-syötteet
resource "google_bigquery_table" "feeds" {
  dataset_id          = google_bigquery_dataset.main.dataset_id
  table_id            = "feeds"
  deletion_protection = true
  schema              = jsonencode([])

  lifecycle {
    ignore_changes = [schema]
  }
}

# Väliaikainen pending-taulu julkaisupäivämäärättömille RSS-artikkeleille
resource "google_bigquery_table" "objects_pending" {
  dataset_id          = google_bigquery_dataset.main.dataset_id
  table_id            = "objects_pending"
  deletion_protection = false
  schema              = jsonencode([])

  lifecycle {
    ignore_changes = [schema]
  }
}

# OG-välimuisti – Open Graph -metatiedot artikkeleille
resource "google_bigquery_table" "og_cache" {
  dataset_id          = google_bigquery_dataset.main.dataset_id
  table_id            = "og_cache"
  deletion_protection = true
  schema              = jsonencode([])

  lifecycle {
    ignore_changes = [schema]
  }
}

# ---------------------------------------------------------------------------
# Dataset: activitystreams_social
# Käyttäjäkohtainen data erotettu omaan datasettiin tietosuojan vuoksi.
# GDPR Art. 17: erillinen datasetti helpottaa poisto-operaatioita (#37).
# ---------------------------------------------------------------------------

resource "google_bigquery_dataset" "social" {
  dataset_id                 = var.bq_social_dataset     # "activitystreams_social"
  location                   = var.region
  delete_contents_on_destroy = false

  lifecycle {
    prevent_destroy = true
  }
}

# write-api: kirjoitusoikeus activitystreams_social-datasettiin
# (tykkäysten tallennus — käyttäjäkohtainen data)
resource "google_bigquery_dataset_iam_member" "write_api_bq_editor_social" {
  dataset_id = google_bigquery_dataset.social.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.write_api.email}"
}

# query-api: read-only activitystreams_social-datasettiin
resource "google_bigquery_dataset_iam_member" "query_api_bq_viewer_social" {
  dataset_id = google_bigquery_dataset.social.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.query_api.email}"
}

# Tykkäykset – käyttäjä × artikkeli -parit
resource "google_bigquery_table" "social_likes" {
  dataset_id          = google_bigquery_dataset.social.dataset_id
  table_id            = "social_likes"
  deletion_protection = true
  schema              = jsonencode([])

  lifecycle {
    ignore_changes = [schema]
  }
}

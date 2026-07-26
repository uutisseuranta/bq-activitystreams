# terraform/cloudrun_jobs.tf
# Cloud Run Jobs (ajastetut eräajot).
# Korvaa:
#   deploy/rss-fetch-job.env.yaml
#   deploy/voikko-job.env.yaml
#   deploy/og-enrichment-job.env.yaml
#   deploy/likes-and-updated-job.env.yaml
# yhdistettynä deploy/deploy.sh -logiikkaan.
#
# Ajastus (Cloud Scheduler) on eriytetty omaan tiedostoonsa
# terraform/scheduler.tf jotta riippuvuudet pysyvät selkeinä.

# ── rss-fetch-job ──────────────────────────────────────────────────────────
resource "google_cloud_run_v2_job" "rss_fetch" {
  name     = "rss-fetch-job"
  location = var.region
  project  = var.gcp_project

  template {
    template {
      service_account = local.write_api_sa_email

      containers {
        image = "${local.image_base}/rss-fetch-job:latest"

        env {
          name  = "GCP_PROJECT"
          value = var.gcp_project
        }
        env {
          name  = "BQ_DATASET"
          value = var.bq_dataset
        }
        env {
          name  = "BQ_LOCATION"
          value = var.region
        }
        env {
          name  = "BQ_TABLE"
          value = "objects"
        }
        env {
          name  = "SERVICE_ACCOUNT_EMAIL"
          value = local.write_api_sa_email
        }
        env {
          name  = "REQUEST_TIMEOUT"
          value = var.rss_request_timeout
        }
        env {
          name  = "RSS_FEEDS"
          value = var.rss_feeds
        }
        env {
          name = "OPS_DISPATCH_TOKEN"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.ops_dispatch_token.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [google_artifact_registry_repository.jobs]
}

# ── voikko-job ─────────────────────────────────────────────────────────────
resource "google_cloud_run_v2_job" "voikko" {
  name     = "voikko-job"
  location = var.region
  project  = var.gcp_project

  template {
    template {
      service_account = local.write_api_sa_email

      containers {
        image = "${local.image_base}/voikko-job:latest"

        env {
          name  = "GCP_PROJECT"
          value = var.gcp_project
        }
        env {
          name  = "BQ_DATASET"
          value = var.bq_dataset
        }
        env {
          name  = "BQ_LOCATION"
          value = var.region
        }
        env {
          name  = "SERVICE_ACCOUNT_EMAIL"
          value = local.write_api_sa_email
        }
        env {
          name  = "BATCH_SIZE"
          value = var.voikko_batch_size
        }
        env {
          name = "OPS_DISPATCH_TOKEN"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.ops_dispatch_token.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [google_artifact_registry_repository.jobs]
}

# ── og-enrichment-job ─────────────────────────────────────────────────────
resource "google_cloud_run_v2_job" "og_enrichment" {
  name     = "og-enrichment-job"
  location = var.region
  project  = var.gcp_project

  template {
    template {
      service_account = local.write_api_sa_email

      containers {
        image = "${local.image_base}/og-enrichment-job:latest"

        env {
          name  = "GCP_PROJECT"
          value = var.gcp_project
        }
        env {
          name  = "BQ_DATASET"
          value = var.bq_dataset
        }
        env {
          name  = "BQ_LOCATION"
          value = var.region
        }
        env {
          name  = "SERVICE_ACCOUNT_EMAIL"
          value = local.write_api_sa_email
        }
        env {
          name  = "BATCH_SIZE"
          value = var.og_batch_size
        }
        env {
          name  = "HTTP_TIMEOUT_S"
          value = var.og_http_timeout
        }
        env {
          name = "OPS_DISPATCH_TOKEN"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.ops_dispatch_token.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [google_artifact_registry_repository.jobs]
}

# ── likes-and-updated-job ─────────────────────────────────────────────────
resource "google_cloud_run_v2_job" "likes_and_updated" {
  name     = "likes-and-updated-job"
  location = var.region
  project  = var.gcp_project

  template {
    template {
      service_account = local.write_api_sa_email

      containers {
        image = "${local.image_base}/likes-and-updated-job:latest"

        env {
          name  = "GCP_PROJECT"
          value = var.gcp_project
        }
        env {
          name  = "BQ_DATASET"
          value = var.bq_dataset
        }
        env {
          name  = "BQ_SOCIAL_DATASET"
          value = var.bq_social_dataset
        }
        env {
          name  = "BQ_LOCATION"
          value = var.region
        }
        env {
          name  = "SERVICE_ACCOUNT_EMAIL"
          value = local.write_api_sa_email
        }
        env {
          name = "OPS_DISPATCH_TOKEN"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.ops_dispatch_token.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [google_artifact_registry_repository.jobs]
}

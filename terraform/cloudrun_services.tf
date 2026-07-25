# terraform/cloudrun_services.tf
# Cloud Run -palvelut (Services, ei Jobs).
# Korvaa deploy/query-api.env.yaml, deploy/write-api.env.yaml
# ja deploy/og-scraper.env.yaml yhdistettynä deploy/deploy.sh -logiikkaan.
#
# Autentikointi:
#   query-api   → julkinen  (--allow-unauthenticated)
#   write-api   → IAM-suojattu (--no-allow-unauthenticated)
#   og-scraper  → IAM-suojattu (--no-allow-unauthenticated)
#
# max_instance_count = var.max_instance_count (default 10):
#   Estää odottamattoman kustannusräjähdyksen bot-liikenteen tai
#   sovelluksen bugin sattuessa. Nosta terraform.tfvars:ssa jos tarve kasvaa.
#
# Issue-viittaukset:
#   #28  Security Hardening – CORS, autentikointi

# ── query-api ──────────────────────────────────────────────────────────────
resource "google_cloud_run_v2_service" "query_api" {
  name     = "query-api"
  location = var.region
  project  = var.gcp_project

  ingress = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = local.sa_email

    scaling {
      max_instance_count = var.max_instance_count
    }

    containers {
      image = "${local.image_base}/query-api:latest"

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
        value = local.sa_email
      }

      liveness_probe {
        http_get {
          path = "/healthz"
        }
      }
      startup_probe {
        http_get {
          path = "/readyz"
        }
      }
    }
  }

  depends_on = [google_artifact_registry_repository.jobs]
}

# Salli julkinen liikenne query-apille
resource "google_cloud_run_v2_service_iam_member" "query_api_public" {
  project  = var.gcp_project
  location = var.region
  name     = google_cloud_run_v2_service.query_api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ── write-api ──────────────────────────────────────────────────────────────
resource "google_cloud_run_v2_service" "write_api" {
  name     = "write-api"
  location = var.region
  project  = var.gcp_project

  ingress = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = local.sa_email

    scaling {
      max_instance_count = var.max_instance_count
    }

    containers {
      image = "${local.image_base}/write-api:latest"

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
        value = local.sa_email
      }
      env {
        name  = "GOOGLE_CLIENT_ID"
        value = var.google_client_id
      }
      env {
        name  = "CLOUD_RUN_SERVICE_URL"
        value = var.write_api_url
      }
      env {
        name  = "ALLOW_MOCK_AUTH"
        value = var.allow_mock_auth
      }

      # GOOGLE_CLIENT_SECRET luetaan Secret Managerista, ei plain-text env:stä.
      # Secret Manager -resurssi on määritelty terraform/secrets.tf:ssä.
      # Arvo syötetään käsin tai CI:ssä:
      #   echo -n "$SECRET" | gcloud secrets versions add google-client-secret --data-file=-
      env {
        name = "GOOGLE_CLIENT_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.google_client_secret.secret_id
            version = "latest"
          }
        }
      }

      liveness_probe {
        http_get {
          path = "/healthz"
        }
      }
      startup_probe {
        http_get {
          path = "/readyz"
        }
      }
    }
  }

  depends_on = [
    google_artifact_registry_repository.jobs,
    google_secret_manager_secret.google_client_secret,
  ]
}

# ── og-scraper ─────────────────────────────────────────────────────────────
resource "google_cloud_run_v2_service" "og_scraper" {
  name     = "og-scraper"
  location = var.region
  project  = var.gcp_project

  ingress = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = local.sa_email

    scaling {
      max_instance_count = var.max_instance_count
    }

    containers {
      image = "${local.image_base}/og-scraper:latest"

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
        value = local.sa_email
      }

      liveness_probe {
        http_get {
          path = "/healthz"
        }
      }
      startup_probe {
        http_get {
          path = "/readyz"
        }
      }
    }
  }

  depends_on = [google_artifact_registry_repository.jobs]
}

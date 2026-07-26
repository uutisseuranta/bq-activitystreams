# terraform/scheduler.tf
# Cloud Scheduler -ajastukset Cloud Run Jobeille.
# Edellyttää Cloud Scheduler API:n käyttöönottoa projektissa.
#
# Ajastuslogiikka perustuu TECHNICAL_DESIGN.md:n kuvaamaan
# pipeline-arkkitehtuuriin:
#   1. rss-fetch-job       → hakee uudet artikkelit RSS-syötteistä
#   2. og-enrichment-job   → rikastaa OG-metatiedoilla
#   3. voikko-job          → lemmatisoi suomenkieliset termit
#   4. likes-and-updated   → päivittää sosiaaliset metriikat
#
# Huom: Cloud Scheduler ei tue europe-north1-regioonia.
# Käytetään var.scheduler_region (default: europe-west3).

resource "google_cloud_scheduler_job" "rss_fetch" {
  name             = "rss-fetch-job-trigger"
  region           = var.scheduler_region
  project          = var.gcp_project
  description      = "Ajaa rss-fetch-jobin joka 15. minuutti"
  schedule         = "*/15 * * * *"
  time_zone        = "Europe/Helsinki"
  attempt_deadline = "320s"

  retry_config {
    retry_count = 2
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.gcp_project}/jobs/${google_cloud_run_v2_job.rss_fetch.name}:run"

    oauth_token {
      service_account_email = local.sa_email
    }
  }
}

resource "google_cloud_scheduler_job" "og_enrichment" {
  name             = "og-enrichment-job-trigger"
  region           = var.scheduler_region
  project          = var.gcp_project
  description      = "Rikastaa OG-metatiedot uusille artikkeleille tunneittain"
  schedule         = "5 * * * *"
  time_zone        = "Europe/Helsinki"
  attempt_deadline = "540s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.gcp_project}/jobs/${google_cloud_run_v2_job.og_enrichment.name}:run"

    oauth_token {
      service_account_email = local.sa_email
    }
  }
}

resource "google_cloud_scheduler_job" "voikko" {
  name             = "voikko-job-trigger"
  region           = var.scheduler_region
  project          = var.gcp_project
  description      = "Lemmatisoi uudet artikkelit kerran tunnissa"
  schedule         = "20 * * * *"
  time_zone        = "Europe/Helsinki"
  attempt_deadline = "540s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.gcp_project}/jobs/${google_cloud_run_v2_job.voikko.name}:run"

    oauth_token {
      service_account_email = local.sa_email
    }
  }
}

resource "google_cloud_scheduler_job" "likes_and_updated" {
  name             = "likes-and-updated-job-trigger"
  region           = var.scheduler_region
  project          = var.gcp_project
  description      = "Päivittää sosiaaliset metriikat (tykkäykset, päivitysajat) joka toinen tunti"
  schedule         = "0 */2 * * *"
  time_zone        = "Europe/Helsinki"
  attempt_deadline = "320s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.gcp_project}/jobs/${google_cloud_run_v2_job.likes_and_updated.name}:run"

    oauth_token {
      service_account_email = local.sa_email
    }
  }
}

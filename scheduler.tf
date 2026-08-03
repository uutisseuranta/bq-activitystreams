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
#
# oauth_token käyttää write_api_sa_email:ia (G-010):
#   deploy_sa:lla ei ole run.invoker-oikeutta jobeihin.
#   write_api-SA:lla on run.invoker kaikille neljälle jobille (iam.tf).

resource "google_cloud_scheduler_job" "rss_fetch" {
  name             = "rss-fetch-job-trigger"
  region           = var.scheduler_region
  project          = var.gcp_project
  description      = "Ajaa rss-fetch-jobin joka 15. minuutti (minuuteilla 7, 22, 37, 52)"
  schedule         = "7,22,37,52 * * * *"
  time_zone        = "Europe/Helsinki"
  attempt_deadline = "320s"

  retry_config {
    retry_count = 2
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.gcp_project}/jobs/${google_cloud_run_v2_job.rss_fetch.name}:run"

    oauth_token {
      service_account_email = local.write_api_sa_email
    }
  }
}

resource "google_cloud_scheduler_job" "og_enrichment" {
  name             = "og-enrichment-job-trigger"
  region           = var.scheduler_region
  project          = var.gcp_project
  description      = "Rikastaa OG-metatiedot uusille artikkeleille tunneittain (minuutilla :14)"
  schedule         = "14 * * * *"
  time_zone        = "Europe/Helsinki"
  attempt_deadline = "540s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.gcp_project}/jobs/${google_cloud_run_v2_job.og_enrichment.name}:run"

    oauth_token {
      service_account_email = local.write_api_sa_email
    }
  }
}

resource "google_cloud_scheduler_job" "voikko" {
  name             = "voikko-job-trigger"
  region           = var.scheduler_region
  project          = var.gcp_project
  description      = "Lemmatisoi uudet artikkelit kerran tunnissa (minuutilla :19)"
  schedule         = "19 * * * *"
  time_zone        = "Europe/Helsinki"
  attempt_deadline = "540s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.gcp_project}/jobs/${google_cloud_run_v2_job.voikko.name}:run"

    oauth_token {
      service_account_email = local.write_api_sa_email
    }
  }
}

resource "google_cloud_scheduler_job" "likes_and_updated" {
  name             = "likes-and-updated-job-trigger"
  region           = var.scheduler_region
  project          = var.gcp_project
  description      = "Päivittää sosiaaliset metriikat joka toinen tunti (minuutilla :21)"
  schedule         = "21 */2 * * *"
  time_zone        = "Europe/Helsinki"
  attempt_deadline = "320s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.gcp_project}/jobs/${google_cloud_run_v2_job.likes_and_updated.name}:run"

    oauth_token {
      service_account_email = local.write_api_sa_email
    }
  }
}

resource "google_cloud_scheduler_job" "query_api_keep_warm" {
  name             = "query-api-keep-warm"
  region           = var.scheduler_region
  project          = var.gcp_project
  description      = "Pings query-api every 10 minutes to keep it warm. Paused automatically after 2 hours of inactivity."
  schedule         = "*/10 * * * *"
  time_zone        = "Europe/Helsinki"
  attempt_deadline = "60s"

  http_target {
    http_method = "GET"
    uri         = "${google_cloud_run_v2_service.query_api.uri}/ap/keep-warm"
  }
}

resource "google_cloud_scheduler_job" "lausuntopalvelu_fetch" {
  name             = "lausuntopalvelu-fetch-job-trigger"
  region           = var.scheduler_region
  project          = var.gcp_project
  description      = "Ajaa lausuntopalvelu-fetch-jobin päivittäin klo 07:00 Helsinki-aikaa"
  schedule         = "0 7 * * *"
  time_zone        = "Europe/Helsinki"
  attempt_deadline = "320s"

  retry_config {
    retry_count = 2
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.gcp_project}/jobs/${google_cloud_run_v2_job.lausuntopalvelu_fetch.name}:run"

    oauth_token {
      service_account_email = local.write_api_sa_email
    }
  }
}

resource "google_cloud_scheduler_job" "ahjo_fetch" {
  name             = "ahjo-fetch-job-trigger"
  region           = var.scheduler_region
  project          = var.gcp_project
  description      = "Ajaa ahjo-fetch-jobin päivittäin klo 06:00 Helsinki-aikaa"
  schedule         = "0 6 * * *"
  time_zone        = "Europe/Helsinki"
  attempt_deadline = "320s"

  retry_config {
    retry_count = 2
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.gcp_project}/jobs/${google_cloud_run_v2_job.ahjo_fetch.name}:run"

    oauth_token {
      service_account_email = local.write_api_sa_email
    }
  }
}

resource "google_cloud_scheduler_job" "hri_fetch" {
  name             = "hri-fetch-job-trigger"
  region           = var.scheduler_region
  project          = var.gcp_project
  description      = "Ajaa hri-fetch-jobin päivittäin klo 06:30 Helsinki-aikaa"
  schedule         = "30 6 * * *"
  time_zone        = "Europe/Helsinki"
  attempt_deadline = "320s"

  retry_config {
    retry_count = 2
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.gcp_project}/jobs/${google_cloud_run_v2_job.hri_fetch.name}:run"

    oauth_token {
      service_account_email = local.write_api_sa_email
    }
  }
}


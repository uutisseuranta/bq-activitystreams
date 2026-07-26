# terraform/variables.tf
# Kaikki muuttujat on kuvattu; arvot annetaan terraform.tfvars:ssa
# tai CI/CD-ympäristömuuttujina (TF_VAR_*).

variable "gcp_project" {
  description = "GCP-projektin tunnus"
  type        = string
  default     = "uutisseuranta-activitystreams"
}

variable "region" {
  description = "Cloud Run ja Artifact Registry -alue"
  type        = string
  default     = "europe-north1"
}

variable "scheduler_region" {
  description = "Cloud Scheduler -alue (ei tue europe-north1, käytetään europe-west3)"
  type        = string
  default     = "europe-west3"
}

variable "bq_dataset" {
  description = "BigQuery-pääddataset"
  type        = string
  default     = "activitystreams"
}

variable "bq_social_dataset" {
  description = "BigQuery sosiaalinen dataset"
  type        = string
  default     = "activitystreams_social"
}

variable "sa_name" {
  description = "IAM-palvelutilin lyhytnimi (ei @-jälkeen)"
  type        = string
  default     = "backend"
}

variable "google_client_id" {
  description = "Google OAuth2 client ID write-apille"
  type        = string
  sensitive   = true
}

variable "write_api_url" {
  description = "Cloud Run write-api -palvelun URL (täytetään ensimmäisen deployn jälkeen)"
  type        = string
  default     = "https://write-api-7y62heo36a-lz.a.run.app"
}

variable "allow_mock_auth" {
  description = "Salli mock-autentikointi (ei koskaan true tuotannossa)"
  type        = string
  default     = "false"
}

variable "rss_feeds" {
  description = "JSON-lista RSS-syötteistä rss-fetch-jobille"
  type        = string
  default     = <<-EOT
    [{"name":"hs","url":"https://www.hs.fi/rss/tuoreimmat.xml"},
     {"name":"iltalehti","url":"https://www.iltalehti.fi/rss/uutiset.xml"},
     {"name":"is","url":"https://www.is.fi/rss/tuoreimmat.xml"},
     {"name":"kauppalehti","url":"https://feeds.kauppalehti.fi/rss/main"},
     {"name":"valtioneuvosto","url":"https://valtioneuvosto.fi","autodiscover":true}]
  EOT
}

variable "og_batch_size" {
  description = "og-enrichment-job: kuinka monta artikkelia käsitellään kerralla"
  type        = string
  default     = "100"
}

variable "og_http_timeout" {
  description = "og-enrichment-job HTTP-timeout sekunteina"
  type        = string
  default     = "10"
}

variable "voikko_batch_size" {
  description = "voikko-job: kuinka monta artikkelia analysoidaan kerralla"
  type        = string
  default     = "200"
}

variable "rss_request_timeout" {
  description = "rss-fetch-job: HTTP-timeout sekunteina"
  type        = string
  default     = "10"
}

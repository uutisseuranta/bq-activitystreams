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

variable "bq_dataset" {
  description = "BigQuery-päädataset"
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

# write_api_url on poistettu muuttujista.
# Arvo tulee suoraan Cloud Run -resurssista outputs.tf:n kautta:
#   google_cloud_run_v2_service.write_api.uri
# Kovakoodattu default oli virhealtis: URL voi muuttua deployn yhteydessä
# ja muuttujan päivittäminen vaatisi ylimääräisen terraform apply -kierroksen.
# Jos URL tarvitaan toisessa moduulissa tai skriptissä, käytä:
#   terraform output write_api_url

variable "allow_mock_auth" {
  description = "Salli mock-autentikointi (ei koskaan true tuotannossa)"
  type        = string
  default     = "false"

  validation {
    # Estää vahingollisen allow_mock_auth = "true" tai "1" tuotannossa.
    # Ainoa sallittu arvo on "false". Jos tarvitset mock-autentikointia
    # kehityksessä, käytä LOCAL_DEV=true -ympäristömuuttujaa suoraan
    # prosessissa — älä muuta tätä muuttujaa.
    condition     = var.allow_mock_auth == "false"
    error_message = "allow_mock_auth ei saa olla muu kuin \"false\" tuotannossa. Mock-auth on sallittu vain paikallisessa kehityksessä."
  }
}

variable "max_instance_count" {
  description = "Cloud Run -palveluiden maksimi-instanssimäärä. Estää odottamattoman kustannusräjähdyksen bot-liikenteen tai bugin sattuessa."
  type        = number
  default     = 10

  validation {
    condition     = var.max_instance_count >= 1 && var.max_instance_count <= 100
    error_message = "max_instance_count on oltava välillä 1–100."
  }
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

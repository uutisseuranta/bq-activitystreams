# terraform/outputs.tf

output "sa_email" {
  description = "Backend-palvelutilin sähköpostiosoite"
  value       = google_service_account.backend.email
}

output "query_api_url" {
  description = "query-api Cloud Run -palvelun URL"
  value       = google_cloud_run_v2_service.query_api.uri
}

output "write_api_url" {
  description = "write-api Cloud Run -palvelun URL"
  value       = google_cloud_run_v2_service.write_api.uri
}

output "og_scraper_url" {
  description = "og-scraper Cloud Run -palvelun URL"
  value       = google_cloud_run_v2_service.og_scraper.uri
}

output "image_base" {
  description = "Artifact Registry -imagen peruspolku"
  value       = local.image_base
}

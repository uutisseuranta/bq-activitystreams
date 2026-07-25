# terraform/artifact_registry.tf
# Artifact Registry Docker-repositorio kontti-imageille.
# Korvaa deploy.sh:n ad-hoc "gcloud artifacts repositories create" -komennon.
#
# image_base local on siirretty locals.tf:ään — ks. sieltä.

resource "google_artifact_registry_repository" "jobs" {
  project       = var.gcp_project
  location      = var.region
  repository_id = "jobs"
  description   = "Cloud Run Job / Service Docker-imagit"
  format        = "DOCKER"

  lifecycle {
    prevent_destroy = true
  }
}

# terraform/storage.tf
# Bronze-tason GCS-bucketit raakadatan tallentamiseen medallion-arkkitehtuurin mukaisesti.

resource "google_storage_bucket" "bronze_buckets" {
  for_each      = toset(var.bronze_bucket_sources)
  name          = "${var.gcp_project}-bronze-${each.key}"
  location      = var.region
  # force_destroy poistettu tuotannon tietoturvan vuoksi (default: false)

  uniform_bucket_level_access = true

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = 90
    }
  }
}

# write_api_bronze_creator relies on the presence of the "user" key in var.bronze_bucket_sources.
# This dependency is enforced by a contains() validation rule in variables.tf.
resource "google_storage_bucket_iam_member" "write_api_bronze_creator" {
  bucket   = google_storage_bucket.bronze_buckets["user"].name
  role     = "roles/storage.objectCreator"
  member   = "serviceAccount:${google_service_account.write_api.email}"
}

resource "google_storage_bucket_iam_member" "fetch_jobs_bronze_creator" {
  for_each = google_storage_bucket.bronze_buckets
  bucket   = each.value.name
  role     = "roles/storage.objectCreator"
  member   = "serviceAccount:${google_service_account.fetch_jobs.email}"
}


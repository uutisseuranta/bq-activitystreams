# terraform/secrets.tf
# Google Secret Manager – salaiset arvot Cloud Run -palveluille.
#
# Mikä menee Secret Manageriin vs. terraform.tfvars?
#
#   terraform.tfvars (sensitive = true, ei versionhallintaan):
#     google_client_id   – OAuth2 client ID; ei salainen arvo itsessään
#                          (näkyy selaimen redirecteissä), mutta ympäristö-
#                          kohtainen → tfvars riittää.
#
#   Secret Manager (tämä tiedosto):
#     google_client_secret – OAuth2 client secret; oikea salainen arvo
#                            joka ei saa koskaan näkyä lokeissa tai
#                            ympäristömuuttujissa selkokielisenä.
#
# G-010: secretAccessor-oikeus on vain sa-write-api SA:lle.
# Aiempi binding google_service_account.backend:ille on poistettu.
# query-api ja og-scraper eivät tarvitse Google OAuth -salaisuutta.
#
# Cloud Run -palvelut viittaavat salaisuuksiin muodossa:
#   env { name = "GOOGLE_CLIENT_SECRET"
#         value_source { secret_key_ref { secret = google_secret_manager_secret.google_client_secret.secret_id
#                                         version = "latest" } } }
#
# HUOM: Terraform luo Secret Manager -resurssin (metadata) mutta ei
# tallenna salaisuuden arvoa. Arvo syötetään kerran käsin tai CI:ssä:
#   echo -n "$SECRET_VALUE" | \
#     gcloud secrets versions add google-client-secret --data-file=-

resource "google_secret_manager_secret" "google_client_secret" {
  secret_id = "google-client-secret"

  replication {
    auto {}
  }
}

# Vain write-api SA:lla on oikeus lukea OAuth-salaisuus (G-010)
resource "google_secret_manager_secret_iam_member" "write_api_can_read_secret" {
  secret_id = google_secret_manager_secret.google_client_secret.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.write_api.email}"
}

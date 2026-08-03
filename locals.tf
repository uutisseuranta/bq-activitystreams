# terraform/locals.tf
# Keskitetty locals-lohko — kaikki projektilaajuiset lasketut arvot tässä.
#
# Miksi erillinen tiedosto?
#   Terraform yhdistää kaikki .tf-tiedostot ennen evaluointia, joten locals
#   voi teknisesti olla missä tiedostossa tahansa. Käytännön syy erottaa:
#   - Helpompi navigoida: "mistä löydän sa_email?" → locals.tf
#   - Estää tilanne jossa locals katoaa jos isäntätiedosto refaktoroidaan
#     (esim. iam.tf jaettaisiin useaan tiedostoon)
#   - Välttää sirkulariteettiongelmat jos locals viittaa resursseihin eri
#     tiedostoissa
#
# G-010: yhteinen sa_email poistettu — palvelukohtaiset SA:t (ks. iam.tf)

locals {
  # Artifact Registry -imagen peruspolku.
  # Muoto: REGION-docker.pkg.dev/PROJECT/REPOSITORY
  # Käyttö: "${local.image_base}/palvelu:latest"
  image_base = "${var.region}-docker.pkg.dev/${var.gcp_project}/jobs"

  # Palvelukohtaiset SA-emailit (G-010, iam.tf).
  # deploy-SA (backend) käytetään vain CI/CD:ssä + WIF-bindingissä.
  query_api_sa_email  = google_service_account.query_api.email
  write_api_sa_email  = google_service_account.write_api.email
  fetch_jobs_sa_email = google_service_account.fetch_jobs.email
}

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
# Siirretty:
#   image_base  ← artifact_registry.tf
#   sa_email    ← iam.tf

locals {
  # Artifact Registry -imagen peruspolku.
  # Muoto: REGION-docker.pkg.dev/PROJECT/REPOSITORY
  # Käyttö: "${local.image_base}/palvelu:latest"
  image_base = "${var.region}-docker.pkg.dev/${var.gcp_project}/jobs"

  # Backend-palvelutilin sähköpostiosoite lyhytmuodossa.
  # Viittaa google_service_account.backend -resurssiin (iam.tf).
  # Käytetään Cloud Run -palveluissa, IAM-sidoksissa ja WIF:ssä.
  sa_email = google_service_account.backend.email
}

# terraform/wif.tf
# Workload Identity Federation (WIF) GitHub Actionsille.
# Korvaa SA-avaimet (key.json) lyhytikäisillä OIDC-tokeneilla.
#
# TESTISTRATEGIA — ks. TERRAFORM_TESTING.md, Kerros 2 ja Kerros 3
#
#   terraform plan (CI, PR) validoi tämän tiedoston rakenteen ja
#   attribute_condition-syntaksin ilman state-kirjoitusta (-backend=false).
#   Live smoke-test (smoke-test.yml, manuaalinen, vain main) testaa
#   WIF-autentikoinnin toimivuuden oikeaa GCP:tä vasten.
#
# auth-test.sh POISTETTU (kaanonpäätös G-009):
#   Mock-simulaatiotestit autentikoinnille ovat kiellettyjä.
#   Mock validoi testiin kirjoitettua mallia — ei oikeaa tuotantopolkua.
#   Live smoke-test korvaa sen: oikea WIF-token, oikea Cloud Run.
#
# Issue-viittaukset:
#   #21  WIF-konfiguraatio (unit-test.sh + live-smoke-test.sh)
#   #64  CI/CD-pipeline — deploy-vaihe käyttää tätä WIF-pooliä
#
# GitHub Secrets (aseta manuaalisesti Settings → Secrets → Actions):
#   WIF_PROVIDER        = google_iam_workload_identity_pool_provider.github.name
#   WIF_SERVICE_ACCOUNT = google_service_account.backend.email

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.gcp_project
  workload_identity_pool_id = "github-pool"
  display_name              = "GitHub Actions pool"
  description               = "WIF-pooli GitHub Actions -workflowille"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.gcp_project
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-provider"
  display_name                       = "GitHub OIDC provider"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
    "attribute.workflow"   = "assertion.workflow"
  }

  # KOLMITASOINEN PÄÄSYRAJOITUS (defence-in-depth)
  #
  # Taso 1 — GitHub UI:   workflow_dispatch.branches: [main]  → estetään UI-tasolla
  # Taso 2 — Runtime:     if: github.ref == 'refs/heads/main'  → estetään jobin käynnistyksessä
  # Taso 3 — GCP (tämä): attribute_condition                  → estetään OIDC-tokenin validoinnissa
  #
  # Nämä kolme kerrosta ovat itsenäisiä: yksi voi pettää ilman että muut pettävät.
  # Taso 3 on ainoa kerros joka on organisaation ulkopuolisen hyökkääjän ulottumattomissa,
  # koska se on Terraform-hallittu eikä muutettavissa pelkillä GitHub-oikeuksilla.
  #
  # Hyväksytyt polut:
  #   1. push main-haaraan + workflow on unit-tests.yml
  #      → ainoa polku joka saa deploy-oikeuden
  #   2. workflow_dispatch smoke-test.yml:stä main-haarassa
  #      → manuaalinen live-testi, ei feature-branch-tokenia
  attribute_condition = <<-EOT
    assertion.repository == 'uutisseuranta/bq-activitystreams' &&
    assertion.ref == 'refs/heads/main' &&
    (
      assertion.workflow == '.github/workflows/unit-tests.yml' ||
      assertion.workflow == '.github/workflows/smoke-test.yml'
    )
  EOT
}

# Salli backend-SA:n personointi VAIN main-haarasta.
# attribute.ref/refs/heads/main on yhdenmukaisempi attribute_condition:in kanssa
# kuin attribute.repository, joka olisi sallinut myös feature-haarat.
resource "google_service_account_iam_member" "wif_backend" {
  service_account_id = google_service_account.backend.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.ref/refs/heads/main"
}

# Outputs — kopioi nämä GitHub Secretseihin
output "wif_provider" {
  description = "WIF_PROVIDER — kopioi GitHub Secretsiin"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "wif_service_account" {
  description = "WIF_SERVICE_ACCOUNT — kopioi GitHub Secretsiin"
  value       = google_service_account.backend.email
}

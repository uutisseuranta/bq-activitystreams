# terraform/github.tf
# GitHub-repositorion hallinta: labelit, branch protection.
#
# TESTISTRATEGIA — ks. TERRAFORM_TESTING.md, Kerros 2
#
#   terraform plan (CI, PR) validoi branch protection -asetukset
#   GitHub-provider-skeemaa vasten. Erityisesti:
#   - contexts=["unit-test"] vastaa unit-tests.yml:n job-nimeä
#     (väärä nimi estäisi PR-mergaamisen tai jättäisi checkin ohittamatta)
#   - enforce_admins=true estää admin-bypass-mergen
#     (false salliisi repojen omistajan ohittaa branch protection)
#   - allows_force_pushes=false estää historian uudelleenkirjoituksen
#
# Issue-viittaukset:
#   #28  Security Hardening – branch protection
#   #26  README ja nimeämiskonventio (labelit)
#   #65  Branch protection + required checks main-haaralle

# ── Branch protection ─────────────────────────────────────────────────────
#
# Jos branch protection on jo asetettu GitHubissa käsin ennen `terraform apply`:ta,
# aja ensin import jotta Terraform ei yritä luoda sitä uudelleen:
#
#   terraform import github_branch_protection.main "bq-activitystreams:main"
#
# Ks. myös terraform/TERRAFORM_IMPORT.md, luku 6.
resource "github_branch_protection" "main" {
  repository_id = "bq-activitystreams"
  pattern       = "main"

  required_status_checks {
    strict = true
    # "unit-test" viittaa unit-tests.yml -workflown job-nimeen (#64).
    # Väärä nimi (esim. "test") ohittaisi tarkistuksen hiljaisesti.
    contexts = ["unit-test"]
  }

  required_pull_request_reviews {
    dismiss_stale_reviews           = true
    required_approving_review_count = 1
  }

  # enforce_admins=false: admin voi tarvittaessa mergata ilman toisen henkilön hyväksyntää
  # (tarpeen yksinkehityksessä lukituksen välttämiseksi).
  enforce_admins      = false
  allows_force_pushes = false
  allows_deletions    = false
}

# ── Labels ─────────────────────────────────────────────────────────────────
# Milestone-labelit
resource "github_issue_label" "milestone_v05" {
  repository  = "bq-activitystreams"
  name        = "milestone:v0.5"
  color       = "0075ca"
  description = "Kuuluu v0.5-releaseen (feature complete)"
}

resource "github_issue_label" "milestone_v10" {
  repository  = "bq-activitystreams"
  name        = "milestone:v1.0"
  color       = "006b75"
  description = "Kuuluu v1.0-releaseen (production hardened)"
}

# Prioriteettilabelit
resource "github_issue_label" "priority_critical" {
  repository  = "bq-activitystreams"
  name        = "priority:critical"
  color       = "d93f0b"
  description = "Blokkaava ongelma – pakollinen ennen seuraavaa releasea"
}

resource "github_issue_label" "priority_high" {
  repository  = "bq-activitystreams"
  name        = "priority:high"
  color       = "e4e669"
  description = "Tärkeä ominaisuus tai bugi"
}

resource "github_issue_label" "priority_normal" {
  repository  = "bq-activitystreams"
  name        = "priority:normal"
  color       = "0e8a16"
  description = "Normaali prioriteetti"
}

# Aluetyyppilabelit
resource "github_issue_label" "area_security" {
  repository  = "bq-activitystreams"
  name        = "area:security"
  color       = "b60205"
  description = "Tietoturva, IAM, autentikointi"
}

resource "github_issue_label" "area_infra" {
  repository  = "bq-activitystreams"
  name        = "area:infra"
  color       = "5319e7"
  description = "Cloud Run, Terraform, CI/CD"
}

resource "github_issue_label" "area_data" {
  repository  = "bq-activitystreams"
  name        = "area:data"
  color       = "1d76db"
  description = "BigQuery, skeema, AS2-objektit"
}

resource "github_issue_label" "area_api" {
  repository  = "bq-activitystreams"
  name        = "area:api"
  color       = "c2e0c6"
  description = "query-api, write-api, og-scraper"
}

# Tyyppi-labelit
resource "github_issue_label" "type_bug" {
  repository  = "bq-activitystreams"
  name        = "type:bug"
  color       = "ee0701"
  description = "Virhe"
}

resource "github_issue_label" "type_feat" {
  repository  = "bq-activitystreams"
  name        = "type:feat"
  color       = "84b6eb"
  description = "Uusi ominaisuus"
}

resource "github_issue_label" "type_chore" {
  repository  = "bq-activitystreams"
  name        = "type:chore"
  color       = "cccccc"
  description = "Tekninen velka, refaktorointi"
}

resource "github_issue_label" "type_blocked" {
  repository  = "bq-activitystreams"
  name        = "type:blocked"
  color       = "e11d48"
  description = "Odottaa toisen issuen ratkaisua"
}

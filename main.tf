# terraform/main.tf
# Juuritason Terraform-konfiguraatio bq-activitystreams-projektille.
#
# Tämä tiedosto korvaa:
#   deploy/init-sa.sh     → google_service_account + google_project_iam_member
#   deploy/deploy.sh      → cloud_run_v2_service / cloud_run_v2_job -resurssit
#   deploy/*.env.yaml     → env-lohkot kussakin Cloud Run -resurssissa
#
# Issue-viittaukset:
#   #28  Security Hardening – branch protection, WIF, CORS
#   #52  CSP-otsakkeet (toteutetaan frontendin puolella, tässä IAM-pohja)

terraform {
  # ~> 1.9 sallii patch-päivitykset (1.9.x) mutta estää minor-hyppäykset
  # (1.10, 2.x). CI ajaa kiinnitetyllä versiolla 1.9.8 — tämä varmistaa
  # että paikallinen ajo ja CI käyttävät yhteensopivaa versiota.
  # Älä nosta tätä ilman että CI:n setup-terraform-versio päivitetään ensin.
  required_version = "~> 1.9"

  required_providers {
    google = {
      source = "hashicorp/google"
      # ~> 5.40 sallii patch-päivitykset 5.40.x+ mutta estää 6.x-hyppäyksen.
      # Google Provider 5.x sisältää breaking changeja eri minor-versioissa —
      # minor-pinned (5.0) on liian löysä. Nosta tätä harkiten ja tarkista
      # CHANGELOG: https://github.com/hashicorp/terraform-provider-google/blob/main/CHANGELOG.md
      version = "~> 5.40"
    }
    github = {
      source = "integrations/github"
      # ~> 6.0 — GitHub provider on stabiilimpi kuin Google; minor-pinned riittää.
      version = "~> 6.0"
    }
  }

  # GCS remote backend — state tallennetaan GCS-bucketiin paikallisen
  # terraform.tfstate-tiedoston sijaan.
  #
  # BOOTSTRAP-JÄRJESTYS (kertaluonteinen, ks. terraform/DEPLOY.md Vaihe 0–2):
  #   1. Luo GCS-bucket:  gsutil mb -l europe-north1 gs://uutisseuranta-activitystreams-tfstate
  #   2. Versioning:      gsutil versioning set on gs://uutisseuranta-activitystreams-tfstate
  #   3. Aja WIF ensin:   terraform init -backend=false && terraform apply -target=...
  #   4. Aktivoi backend: terraform init -migrate-state
  #   5. Täysi apply:     terraform apply
  #
  # Remote backend mahdollistaa CI:n terraform plan/apply ilman paikallista
  # state-tiedostoa — CI autentikoi GCP:hen WIF:llä (I-001) ja lukee/kirjoittaa
  # staten suoraan GCS:stä.
  backend "gcs" {
    bucket = "uutisseuranta-activitystreams-tfstate"
    prefix = "terraform/state"
  }
}

provider "google" {
  project = var.gcp_project
  region  = var.region
}

provider "github" {
  owner = "uutisseuranta"
  # token luetaan GITHUB_TOKEN-ympäristömuuttujasta
}

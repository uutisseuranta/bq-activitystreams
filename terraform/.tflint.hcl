# .tflint.hcl — tflint-konfiguraatio bq-activitystreams Terraform-koodille
# Dokumentaatio: https://github.com/terraform-linters/tflint
# Google-plugin: https://github.com/terraform-linters/tflint-ruleset-google
#
# TESTISTRATEGIA — ks. TERRAFORM_TESTING.md, Kerros 1 (staattinen analyysi)
#
# Miksi tflint checkovin lisäksi:
#   Checkov tarkistaa tietoturvapolitiikat (CIS Benchmark, NIST).
#   tflint täydentää sitä provider-skeema-tason tarkistuksilla:
#   - Deprecated-resurssit: google_cloud_run_service → google_cloud_run_v2_service
#   - Deprecated-argumentit: esim. poistetut kentät GCP-providerissa
#   - Puuttuvat pakolliset kentät jotka terraform validate ei havaitse
#     koska validate ei ota yhteyttä provideriin
#
# google-plugin v0.32.0 asennetaan `tflint --init`:lla CI:ssä.
# GITHUB_TOKEN välitetään init-vaiheelle GitHub API rate limitin välttämiseksi.

plugin "google" {
  enabled = true
  version = "0.32.0"
  source  = "github.com/terraform-linters/tflint-ruleset-google"
}

rule "terraform_deprecated_interpolation" {
  enabled = true
}

rule "terraform_deprecated_index" {
  enabled = true
}

rule "terraform_unused_declarations" {
  enabled = true
}

rule "terraform_comment_syntax" {
  enabled = true
}

rule "terraform_documented_outputs" {
  enabled = true
}

rule "terraform_documented_variables" {
  enabled = true
}

rule "terraform_typed_variables" {
  enabled = true
}

rule "terraform_module_pinned_source" {
  enabled = true
}

rule "terraform_naming_convention" {
  enabled = true

  variable {
    format = "snake_case"
  }

  locals {
    format = "snake_case"
  }

  output {
    format = "snake_case"
  }

  resource {
    format = "snake_case"
  }

  data {
    format = "snake_case"
  }
}

rule "terraform_required_version" {
  enabled = true
}

rule "terraform_required_providers" {
  enabled = true
}

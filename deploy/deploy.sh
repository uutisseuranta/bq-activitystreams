#!/usr/bin/env bash
# deploy/deploy.sh — manuaalinen deploy-skripti
# Rakentaa Docker-kuvat (linux/amd64), pushaa Artifact Registryyn,
# ajaa terraform apply ja asettaa GitHub Actions -secretit.
set -euo pipefail

PROJECT="uutisseuranta-activitystreams"
REGION="europe-north1"
REPO="${REGION}-docker.pkg.dev/${PROJECT}/jobs"
SRC="$(cd "$(dirname "$0")/../src" && pwd)"
TF_DIR="$(cd "$(dirname "$0")/../terraform" && pwd)"

echo "==> Kopioidaan shared/ palveluhakemistoihin..."
cp -r "${SRC}/shared" "${SRC}/og_scraper/shared"
cp -r "${SRC}/shared" "${SRC}/og_enrichment_job/shared"

cleanup() {
  echo "==> Siivotaan shared/-kopiot..."
  rm -rf "${SRC}/og_scraper/shared" "${SRC}/og_enrichment_job/shared"
}
trap cleanup EXIT

echo "==> Buildataan og-scraper (linux/amd64)..."
docker buildx build --platform linux/amd64 \
  -t "${REPO}/og-scraper:latest" \
  --push \
  "${SRC}/og_scraper"

echo "==> Buildataan og-enrichment-job (linux/amd64)..."
docker buildx build --platform linux/amd64 \
  -t "${REPO}/og-enrichment-job:latest" \
  --push \
  "${SRC}/og_enrichment_job"

echo "==> Lisätään google-client-secret Secret Manageriin..."
read -rsp "Anna Google OAuth2 Client Secret (tai Enter ohittaaksesi): " OAUTH_SECRET && echo
if [[ -n "${OAUTH_SECRET}" ]]; then
  echo -n "${OAUTH_SECRET}" | gcloud secrets versions add google-client-secret \
    --project="${PROJECT}" \
    --data-file=-
else
  echo "   Ohitettu."
fi

echo "==> Terraform apply..."
cd "${TF_DIR}"
terraform apply -auto-approve

echo "==> Asetetaan GitHub Actions -secretit..."
WIF_PROVIDER=$(terraform output -raw wif_provider)
WIF_SA=$(terraform output -raw wif_service_account)
gh secret set WIF_PROVIDER --repo uutisseuranta/bq-activitystreams --body "${WIF_PROVIDER}"
gh secret set WIF_SERVICE_ACCOUNT --repo uutisseuranta/bq-activitystreams --body "${WIF_SA}"

echo ""
echo "✅ Deploy valmis!"
echo "   WIF_PROVIDER=${WIF_PROVIDER}"
echo "   WIF_SERVICE_ACCOUNT=${WIF_SA}"

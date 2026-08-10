#!/bin/bash
# live-smoke-test.sh — tarkistaa oikean BigQuery-taulun ja Cloud Run Job -konfiguraation
# Malli: uutisseuranta.github.io/live-smoke-test.sh
# Vaatii: gcloud autentikoitu, BQ-oikeudet
set -euo pipefail

PROJECT="uutisseuranta-activitystreams"
DATASET="activitystreams"
REGION="europe-north1"

echo "=== gcs-activitystreams live smoke tests ==="

# 1. BigQuery: objects-taulu olemassa ja skeema oikein
echo "Tarkistetaan activitystreams.objects ..."
SCHEMA=$(bq show --format=prettyjson "${PROJECT}:${DATASET}.objects" 2>/dev/null) || {
    echo "VIRHE: Taulua ${DATASET}.objects ei löydy projektissa ${PROJECT}"
    exit 1
}

for FIELD in id source published updated object_json tags tags_enriched like_count deleted; do
    if ! echo "$SCHEMA" | grep -q "\"name\": \"${FIELD}\""; then
        echo "VIRHE: Sarake '${FIELD}' puuttuu objects-taulusta"
        exit 1
    fi
done
echo "  ✓ objects-taulu ja skeema OK"

# 2. BigQuery: config-taulu olemassa (autodiscovery-tulos tallennetaan tänne)
echo "Tarkistetaan activitystreams.config ..."
bq show "${PROJECT}:${DATASET}.config" > /dev/null 2>&1 || {
    echo "VIRHE: Taulua ${DATASET}.config ei löydy — autodiscovery ei voi tallentaa Valtioneuvosto-URL:ia"
    exit 1
}
echo "  ✓ config-taulu OK"

# 3. BigQuery: objects-taulu on partitionoitu published-sarakkeen mukaan
echo "Tarkistetaan partitionointi ..."
PARTITION_FIELD=$(echo "$SCHEMA" | python3 -c "
import sys, json
s = json.load(sys.stdin)
tf = s.get('timePartitioning', {})
print(tf.get('field', ''))
")
if [ "$PARTITION_FIELD" != "published" ]; then
    echo "VIRHE: objects-taulu ei ole partitionoitu 'published'-kentän mukaan (nyt: '${PARTITION_FIELD}')"
    exit 1
fi
echo "  ✓ partitionointi published-kentän mukaan OK"

# 4. Cloud Run Job: rss-fetch-job olemassa
echo "Tarkistetaan Cloud Run Job rss-fetch-job ..."
gcloud run jobs describe rss-fetch-job \
    --region="${REGION}" \
    --project="${PROJECT}" \
    --format=json > /dev/null 2>&1 || {
    echo "VIRHE: Cloud Run Job 'rss-fetch-job' ei löydy alueelta ${REGION}"
    exit 1
}
echo "  ✓ rss-fetch-job olemassa"

# 5. Cloud Scheduler: ajastus kerran tunnissa
echo "Tarkistetaan Cloud Scheduler -ajastus ..."
SCHEDULE=$(gcloud scheduler jobs describe rss-fetch-job \
    --location="${REGION}" \
    --project="${PROJECT}" \
    --format="value(schedule)" 2>/dev/null) || {
    echo "VIRHE: Cloud Scheduler -ajastusta 'rss-fetch-job' ei löydy"
    exit 1
}
if [ "$SCHEDULE" != "0 * * * *" ]; then
    echo "VIRHE: Ajastus on '${SCHEDULE}', odotettu '0 * * * *'"
    exit 1
fi
echo "  ✓ Cloud Scheduler: ${SCHEDULE} (kerran tunnissa)"

# 6. Palvelutili: olemassa ja IAM-sidokset
SA_EMAIL="backend@${PROJECT}.iam.gserviceaccount.com"
echo "Tarkistetaan palvelutili ${SA_EMAIL} ..."
gcloud iam service-accounts describe "${SA_EMAIL}" \
    --project="${PROJECT}" > /dev/null 2>&1 || {
    echo "VIRHE: Palvelutili ${SA_EMAIL} ei ole luotu — ks. tiketti #16"
    exit 1
}

IAM_POLICY=$(gcloud projects get-iam-policy "${PROJECT}" --format=json)
if ! echo "$IAM_POLICY" | grep -q "serviceAccount:${SA_EMAIL}"; then
    echo "VIRHE: Palvelutilillä ${SA_EMAIL} ei ole IAM-sidoksia projektissa"
    exit 1
fi
echo "  ✓ Palvelutili ja IAM OK"

# 7. Query API HTTP Test: POST /ap/outbox
echo "Tarkistetaan Query API HTTP-päätepiste (POST /ap/outbox) ..."
QUERY_URL=$(gcloud run services describe query-api \
    --region="${REGION}" \
    --project="${PROJECT}" \
    --format="value(status.url)" 2>/dev/null) || {
    echo "HUOMAUTUS: query-api Cloud Run -palvelua ei löytynyt, ohitetaan HTTP-savutesti."
    QUERY_URL=""
}

if [ -n "$QUERY_URL" ]; then
    HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "${QUERY_URL}/ap/outbox" \
        -H "Content-Type: application/json" \
        -d '{"n": 5, "seen_ids": []}')
    
    echo "  HTTP status: ${HTTP_STATUS}"
    if [ "$HTTP_STATUS" -ne 200 ]; then
        echo "VIRHE: POST /ap/outbox palautti virhekoodin ${HTTP_STATUS} (odotettiin 200)"
        exit 1
    fi
    echo "  ✓ query-api POST /ap/outbox HTTP-savutesti OK"
fi

echo ""
echo "Kaikki live smoke testit läpäisty ✓"

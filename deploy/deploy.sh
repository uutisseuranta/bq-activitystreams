#!/usr/bin/env bash
# deploy/deploy.sh — POISTETTU
#
# ARKKITEHTUURIPÄÄTÖS (kaanonpäätös I-004):
# Tämä skripti on korvattu suoralla `gcloud run deploy --source`
# -komennolla. Katso terraform/DEPLOY.md.
#
# Syy: Repositoriossa ei ole Dockerfileja, joten docker buildx build
# ei toimi. Cloud Run --source (buildpack) on ainoa toimiva polku
# ja myös CI:n (unit-tests.yml deploy-job) käyttämä strategia.
# Kahta rinnakkaista deploy-polkua ei ylläpidetä.
#
# Manuaalinen re-deploy yksittäiselle palvelulle:
#   gcloud run deploy <palvelu> \
#     --source src/<palvelu> \
#     --region europe-north1 \
#     --project uutisseuranta-activitystreams
#
# Bootstrap (ensimmäinen deploy / WIF-secretien asetus):
#   Katso terraform/DEPLOY.md
echo "VIRHE: deploy.sh on poistettu käytöstä. Katso kommentit tässä tiedostossa tai terraform/DEPLOY.md."
exit 1

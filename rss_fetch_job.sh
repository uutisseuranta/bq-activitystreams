#!/usr/bin/env bash
# rss_fetch_job.sh — RSS-hakuajurin käynnistin
# Vastuu: Alustaa ympäristön ja käynnistää rss_fetch_job-pääohjelman.
# Ympäristö: Paikallinen tai Cloud Run Job.
# Riippuvuudet: fetch_helpers.sh, python3, src/rss_fetch_job/main.py.

# Otetaan käyttöön keskitetty alustuslogiikka (venv ja PYTHONPATH)
# shellcheck source=fetch_helpers.sh
source "$(dirname "$0")/fetch_helpers.sh"

# Tarkistetaan pakolliset ympäristömuuttujat
check_env_vars "GCP_PROJECT" "BQ_DATASET" "RSS_FEEDS"

# Suoritetaan Python-pohjainen haku
echo "Käynnistetään rss_fetch_job..."
python3 src/rss_fetch_job/main.py

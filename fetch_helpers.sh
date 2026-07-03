#!/usr/bin/env bash
# fetch_helpers.sh — Yleiset alustusapulaiset taustajärjestelmän skripteille.
# Vastuu: Ympäristön alustus (venv, PYTHONPATH) ja yhteiset bash-rutiinit.
# Ei vastaa: Itsenäisestä suorituksesta tai liiketoimintalogiikasta.
# Riippuvuudet: python3, aktiivinen virtuaaliympäristö (venv) jos olemassa.

# Asetetaan PYTHONPATH siten, että src-kansion moduulit voidaan importata
export PYTHONPATH="${PYTHONPATH:-}:${PWD}/src"

# Aktivoidaan virtuaaliympäristö (venv) automaattisesti jos se on luotu juureen
if [ -d "venv" ]; then
  # shellcheck source=/dev/null
  source venv/bin/activate
fi

# Apufunktio: Tarkistaa vaaditut ympäristömuuttujat
# Käyttö: check_env_vars "PROJECT" "DATASET"
check_env_vars() {
  for var in "$@"; do
    if [ -z "${!var:-}" ]; then
      echo "VIRHE: Ympäristömuuttuja '$var' ei ole asetettu." >&2
      exit 1
    fi
  done
}

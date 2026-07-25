#!/bin/bash
# Asetetaan virhesuojaus
set -e

# Siirrytään skriptin suorituskansioon (gcs-activitystreams root)
cd "$(dirname "$0")"

echo "=== Asennetaan jsonschema-markdown virtuaaliympäristöön ==="
if [ -d "venv" ]; then
  venv/bin/pip install jsonschema-markdown
else
  python3 -m venv venv
  venv/bin/pip install jsonschema-markdown
fi

echo "=== Alustetaan STANDARDS.md ==="
cat << 'EOF' > STANDARDS.md
# STANDARDS.md — Uutisseuranta Backend Standardit ja Datamalli

Tämä dokumentti määrittelee uutisseuranta-projektin backend-kerroksen (`gcs-activitystreams`) noudattamat standardit sekä API- ja tietokantatason datamallit.

---

## 1. Ydinspeksit ja standardit

| Kerros | Standardi / Spesifikaatio | Viite |
|---|---|---|
| Datamalli | **ActivityStreams 2.0** (JSON-LD) | [W3C ActivityStreams 2.0](https://www.w3.org/TR/activitystreams-core/) |
| Aikaleimat | **RFC 3339** (ISO 8601 -profiili) | [RFC 3339](https://tools.ietf.org/html/rfc3339) |
| Merkistö | **UTF-8** | [Unicode Standard](https://www.unicode.org/) |

---

## 2. Automaattisesti generoidut kenttätaulukot (JSON Schema)

Seuraavat taulukot kuvaavat API-tason ActivityStreams-objektien tarkat kenttämääritykset. Ne on generoitu automaattisesti projektin JSON-schema -tiedostoista (`*.schema.json`) käyttäen `jsonschema-markdown` -työkalua.

EOF

echo "=== Generoidaan taulukko: Article ==="
echo "### Article Schema" >> STANDARDS.md
echo "Edustaa uutisartikkelia, blogipostausta tai muuta itsenäistä tekstituotetta." >> STANDARDS.md
echo "" >> STANDARDS.md
venv/bin/jsonschema-markdown article.schema.json >> STANDARDS.md
echo "" >> STANDARDS.md

echo "=== Generoidaan taulukko: Note ==="
echo "### Note Schema" >> STANDARDS.md
echo "Edustaa lyhyttä tekstikommenttia tai uutisartikkelin vastausviestiä." >> STANDARDS.md
echo "" >> STANDARDS.md
venv/bin/jsonschema-markdown note.schema.json >> STANDARDS.md
echo "" >> STANDARDS.md

echo "=== Generoidaan taulukko: OrderedCollection ==="
echo "### OrderedCollection Schema" >> STANDARDS.md
echo "Edustaa järjestettyä listaa ActivityStreams-objekteista, kuten uutisvirrasta tai outbox-endpointista." >> STANDARDS.md
echo "" >> STANDARDS.md
venv/bin/jsonschema-markdown collection.schema.json >> STANDARDS.md
echo "" >> STANDARDS.md

echo "=== Generoidaan taulukko: Hashtag ==="
echo "### Hashtag Schema" >> STANDARDS.md
echo "Edustaa artikkelille tai Note-objektille annettua aihetunnistetta tai avainsanaa." >> STANDARDS.md
echo "" >> STANDARDS.md
venv/bin/jsonschema-markdown hashtag.schema.json >> STANDARDS.md
echo "" >> STANDARDS.md

# Staattinen osio — ei generoida JSON Schemasta
cat << 'EOF' >> STANDARDS.md
---

## 3. GDPR (Tietosuoja) ja henkilötietojen käsittely
Tietokanta- ja rajapintatasolla noudatetaan seuraavia GDPR-standardeja:
- **Oikeus siirtää tiedot järjestelmästä toiseen (Data Portability)**: Järjestelmän tulee tarjota rajapinta (`GET /ap/outbox?actor={actor_id}`), jonka kautta käyttäjä voi viedä omat tuottamansa kommentit (`Note`-objektit) ja tykkäykset standardissa JSON-LD/ActivityStreams 2.0 -muodossa.
- **Oikeus tulla unohdetuksi (Right to be Forgotten)**: Profiilin poiston yhteydessä Firebase Auth -tunnisteeseen liittyvät henkilötiedot poistetaan tai anonymisoidaan pysyvästi seuraavista tallennuspaikoista:
  - **BigQuery** (`bq-activitystreams`): kommentit poistetaan tai niiden sisältö korvataan merkkijonolla `[kommentti poistettu]` ketjurakenteen säilyttämiseksi, ja tykkäykset anonymisoidaan poistamalla käyttäjätunnisteet.
  - **Firestore** (`uutisseuranta.github.io` / frontend): käyttäjän preferenssit ja asetukset poistetaan. Firestore toimii tässä järjestelmässä yksinomaan frontend-preferenssien monilaitesynkronointiin (ks. [uutisseuranta.github.io DECISION_LOG.csv L-006](https://github.com/uutisseuranta/uutisseuranta.github.io/blob/main/DECISION_LOG.csv)) — ei backend-tietovarastona.
  - **Lokijärjestelmät**: henkilötiedot anonymisoidaan pysyvästi.
EOF

echo "=== STANDARDS.md generoitu onnistuneesti! ==="

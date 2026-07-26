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
| Tiedonsiirtovapaus | **W3C Data on the Web Best Practices (DWBP)** | [W3C DWBP BP7: Use persistent URIs](https://www.w3.org/TR/dwbp/#persistentUris) |
| Web Scraping | **Robots Exclusion Protocol (REP)** | [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309) (Robots.txt, 2022) |

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

---

## 4. Kenttämäppäys: OpenGraph / Schema.org / AS2
Tämä taulukko määrittelee, miten sisällön rikastusprosessissa eri metadatastandardien kentät vastaavat toisiaan ja miten ne mäpätään W3C ActivityStreams 2.0 (AS2) -datamalliin.

| AS2-kenttä | OpenGraph (og:) | Schema.org (JSON-LD) | Huomiot ja prioriteetti |
|---|---|---|---|
| `name` | `og:title` | `headline` / `name` | Pidempi otsikko voittaa vertailussa (`longer(rss, og)`). |
| `summary` | `og:description` | `description` | `og:description` täydentää, jos RSS-syötteen kuvaus puuttuu. |
| `image` | `og:image` | `image` / `thumbnailUrl` | Ensimmäinen resolvoitava kuva käytetään. |
| `published` | `article:published_time` | `datePublished` | Prioriteetti: **JSON-LD `datePublished` → OG `article:published_time` → HTTP `Last-Modified`** |
| `updated` | `article:modified_time` | `dateModified` | Valinnainen kenttä. |
| `url` | `og:url` | `url` | Ensisijaisena lähteenä käytetään RSS-syötteen `<link>`-linkkiä. |
| `attributedTo` | `article:author` | `author.name` | Sisällöntuottajan tai median nimi. |
| `type` | `og:type` (`article`) | `@type` (`NewsArticle`) | Validoidaan tyypin vastaavuus (`og:type = article`). |
| `tag` | — | `keywords` | Voikko-morfologiset tagit muunnetaan AS2 `Hashtag`-objekteiksi. |

---

## 5. Paginaatio ja identiteettien pysyvyys
- **Paginaatiostrategia (Päätös L-009)**: Järjestelmä käyttää inkrementaalista `?n=`-parametriin perustuvaa hakumallia eikä ActivityPubin `OrderedCollectionPage` + `next`/`prev` -linkkikenttäontologiaa. Client pyytää aina top-N relevantinta artikkelia alusta alkaen — ei kursoreja eikä sivunumeroita:

  ```
  GET /ap/outbox?tag=politiikka&n=5    → top-5 (pikakatselu, sivun avaus)
  GET /ap/outbox?tag=politiikka&n=50   → top-50 (scroll-to-load)
  GET /ap/outbox?tag=politiikka&n=500  → top-500 (maksimierä, kustannustehok. ylär.)
  ```

  Client suodattaa duplikaatit selaimen muistissa `id`-kentän perusteella. Kun käyttäjä on selannut kaiken, esitetään tagipilvi uutta hakua varten (n palautuu 5:een). Tämä pitää BigQuery-kustannukset ennustettavina: kukin käyttäjäsessio tuottaa korkeintaan kolme kyselytasoa per tagi.

- **Pysyvät tunnisteet (W3C DWBP BP7 / Päätös L-010)**: AS2-objektin `id` on pysyvä, muuttumaton IRI muodossa `https://uutisseuranta.net/ap/objects/{hash}`. Se erotetaan artikkelin operatiivisesta `url`-osoitteesta, jotta toimitukselliset URL-rakenteen muutokset eivät riko olemassa olevia linkityksiä ja sosiaalisia reaktioita. Hash lasketaan kaavalla `sha256(source + url)[:16]`.

---

## 6. Hashtag-standardi ja Voikko-normalisointi

W3C ActivityStreams 2.0 ei itse määrittele `Hashtag`-tyyppiä — se on lisätty myöhemmin `as:`-nimiavaruuteen epävirallisena laajennuksena. `Hashtag` on Mastodonin vakiinnuttama de facto -standardi koko Fediverse-ekosysteemissä. Mastodonin virallinen dokumentaatio määrittelee asian eksplisiittisesti: **"`Hashtag` has a name containing the `#hashtag` microsyntax — a `#` followed by a string sequence representing a topic."** DSNP-spesifikaatio vahvistaa saman käytännön: `"name": "#dsnp"`. Tästä seuraa, että `hashtag.schema.json`-skeeman sääntö `pattern: "^#"` on **oikea ja standardin mukainen valinta** — se pakottaa täsmälleen sen muodon, jota kaikki AS2/AP-ekosysteemin toteutukset käyttävät.

### Voikko-normalisoinnin vastuu

Voikko tuottaa morfologisia hakutermejä pelkkänä sanana ilman `#`-merkkiä: `politiikka`, `eurooppa`, `ilmastonmuutos`. Voikko on suomen kielen morfologinen analysaattori, ei sosiaalisen median hashtag-generaattori — se ei tiedä eikä välitä `#`-konventiosta.

Tämä tarkoittaa, että `pattern: "^#"` paljastaa tärkeän **normalisointivastuun**: koodipolun täytyy eksplisiittisesti lisätä `#`-etuliite Voikon tuottamiin termeihin ennen kuin ne kirjoitetaan `object_json['tag']`-kenttään. Jos muunnosta ei tehdä, skeemavalidaatio hylkää koko artikkelin tallennuksen — mikä on tarkoituksellista, koska se pakottaa korjaamaan ongelman aikaisessa vaiheessa.

Voikko-job tarvitsee siis normalisointiaskeleen:

```python
# Voikko palauttaa: ["politiikka", "eurooppa"]
# Tallennetaan AS2 Hashtag -muodossa:
tags = [
    {
        "type": "Hashtag",
        "name": f"#{word}",          # <-- normalisointi: lisätään #-etuliite
        "href": f"https://uutisseuranta.net/ap/outbox?tag=%23{word}"
    }
    for word in voikko_terms
]
```

### href-kentän URL-enkoodaus

`href`-kentässä `#` täytyy enkoodata muotoon `%23`, koska `#` on URL:ssa fragmentin aloitusmerkki. Oikea muoto on `?tag=%23politiikka`, ei `?tag=#politiikka`. Tämä on erillinen johdonmukaisuusvaatimus `name`-kentän muodosta: molemmat viittaavat samaan tagiin, mutta eri konteksteissa `#`-merkki käyttäytyy eri tavalla.

| Kenttä | Muoto | Esimerkki |
|---|---|---|
| `name` | `#`-etuliite, sellaisenaan | `"#politiikka"` |
| `href` | `#` URL-enkoodattuna `%23`:ksi | `"...?tag=%23politiikka"` |

Viitteet: [W3C AS2 extensions wiki](https://www.w3.org/wiki/Activity_Streams_extensions) · [Mastodon ActivityPub spec](https://docs.joinmastodon.org/spec/activitypub/) · [DSNP Tag spec](https://spec.dsnp.org/ActivityContent/Associated/Tag.html)
EOF

echo "=== STANDARDS.md generoitu onnistuneesti! ==="

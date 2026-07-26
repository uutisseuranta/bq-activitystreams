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

### Article Schema
Edustaa uutisartikkelia, blogipostausta tai muuta itsenäistä tekstituotetta.

# ActivityStreams 2.0 Article

Edustaa uutisartikkelia, blogipostausta tai muuta itsenäistä tekstituotetta.

### Type: `object`

| Property | Type | Required | Possible values | Deprecated | Default | Description | Examples |
| -------- | ---- | -------- | --------------- | ---------- | ------- | ----------- | -------- |
| @context | `array` or `string` | ✅ | object and/or string and/or string |  |  | JSON-LD konteksti, tyypillisesti https://www.w3.org/ns/activitystreams ja uutisseuranta-nimiavaruuslaajennus |  |
| type | `string` | ✅ | string |  |  | Objektin tyyppi, arvon on oltava 'Article' |  |
| id | `string` | ✅ | string |  |  | Objektin yksikäsitteinen tunniste (IRI/URI) |  |
| name | `string` | ✅ | string |  |  | Artikkelin otsikko tai nimi |  |
| url | `string` | ✅ | string |  |  | Alkuperäisen artikkelin verkko-osoite (URL) |  |
| published | `string` | ✅ | string |  |  | Julkaisuajankohta RFC 3339 -muodossa |  |
| summary | `string` |  | string |  |  | Artikkelin yhteenveto tai lyhyt katkelma |  |
| content | `string` |  | string |  |  | Artikkelin HTML-muotoiltu pääsisältö |  |
| updated | `string` |  | string |  |  | Viimeisin päivitysajankohta RFC 3339 -muodossa |  |
| attributedTo | `string` |  | string |  |  | Artikkelin tekijä tai julkaisija (esim. uutislähteen nimi) |  |
| likes | `object` |  | object |  |  | Artikkelin tykkäysten kokoelma (AS2 Core §5.7) |  |
| likes.type | `string` | ✅ | `Collection` |  |  |  |  |
| likes.totalItems | `integer` | ✅ | integer |  |  |  |  |
| dislikes | `object` |  | object |  |  | Artikkelin dislike-reaktioiden kokoelma (projektikohtainen laajennus) |  |
| dislikes.type | `string` | ✅ | `Collection` |  |  |  |  |
| dislikes.totalItems | `integer` | ✅ | integer |  |  |  |  |
| _uutisseuranta:agreeCount | `integer` |  | integer |  |  | Yhteenlaskettu reaktiomäärä (likes + dislikes) (projektikohtainen laajennus) |  |
| tag | `array` |  | object |  |  | Artikkeliin liittyvät AS2 Hashtag-objektit (Voikko-jobin tuottamat morfologiset tagit) |  |


---

Markdown generated with [jsonschema-markdown](https://github.com/elisiariocouto/jsonschema-markdown).

### Note Schema
Edustaa lyhyttä tekstikommenttia tai uutisartikkelin vastausviestiä.

# ActivityStreams 2.0 Note

Edustaa lyhyttä tekstikommenttia tai uutisartikkelin vastausviestiä.

### Type: `object`

| Property | Type | Required | Possible values | Deprecated | Default | Description | Examples |
| -------- | ---- | -------- | --------------- | ---------- | ------- | ----------- | -------- |
| @context | `string` | ✅ | string |  |  | JSON-LD konteksti, tyypillisesti https://www.w3.org/ns/activitystreams |  |
| type | `string` | ✅ | string |  |  | Objektin tyyppi, arvon on oltava 'Note' |  |
| id | `string` | ✅ | string |  |  | Kommentin yksikäsitteinen tunniste (IRI/URI) |  |
| content | `string` | ✅ | string |  |  | Kommentin leipäteksti HTML- tai plain text -muodossa |  |
| published | `string` | ✅ | string |  |  | Julkaisuajankohta RFC 3339 -muodossa |  |
| attributedTo | `string` | ✅ | string |  |  | Kommentin kirjoittajan tunniste (esim. käyttäjän sub-id) |  |
| inReplyTo | `string` | ✅ | string |  |  | Pääartikkelin tai ylemmän kommentin tunniste (id/IRI), johon tämä viesti vastaa |  |


---

Markdown generated with [jsonschema-markdown](https://github.com/elisiariocouto/jsonschema-markdown).

### OrderedCollection Schema
Edustaa järjestettyä listaa ActivityStreams-objekteista, kuten uutisvirrasta tai outbox-endpointista.

# ActivityStreams 2.0 OrderedCollection

Edustaa järjestettyä listaa ActivityStreams-objekteista, kuten uutisvirrasta tai outbox-endpointista.

### Type: `object`

| Property | Type | Required | Possible values | Deprecated | Default | Description | Examples |
| -------- | ---- | -------- | --------------- | ---------- | ------- | ----------- | -------- |
| @context | `string` | ✅ | string |  |  | JSON-LD konteksti, tyypillisesti https://www.w3.org/ns/activitystreams |  |
| type | `string` | ✅ | string |  |  | Objektin tyyppi, arvon on oltava 'OrderedCollection' |  |
| id | `string` | ✅ | string |  |  | Kokoelman yksikäsitteinen tunniste (IRI/URI) |  |
| totalItems | `integer` | ✅ | integer |  |  | Kokoelmassa olevien objektien kokonaismäärä |  |
| orderedItems | `array` | ✅ | object |  |  | Kokoelman sisältämät objektit tai niiden ID:t järjestettynä |  |


---

Markdown generated with [jsonschema-markdown](https://github.com/elisiariocouto/jsonschema-markdown).

### Hashtag Schema
Edustaa artikkelille tai Note-objektille annettua aihetunnistetta tai avainsanaa.

# ActivityStreams 2.0 Hashtag

Edustaa artikkelille tai Note-objektille annettua aihetunnistetta tai avainsanaa.

### Type: `object`

| Property | Type | Required | Possible values | Deprecated | Default | Description | Examples |
| -------- | ---- | -------- | --------------- | ---------- | ------- | ----------- | -------- |
| type | `string` | ✅ | string |  |  | Objektin tyyppi, arvon on oltava 'Hashtag' |  |
| name | `string` | ✅ | string |  |  | Aihetunnisteen teksti (esim. '#politiikka' tai 'tekoäly') |  |
| href | `string` |  | string |  |  | Tunnisteeseen liittyvä haku- tai suodatuslinkki (URL) |  |


---

Markdown generated with [jsonschema-markdown](https://github.com/elisiariocouto/jsonschema-markdown).

---

## 3. GDPR (Tietosuoja) ja henkilötietojen käsittely
Tietokanta- ja rajapintatasolla noudatetaan seuraavia GDPR-standardeja:
- **Oikeus siirtää tiedot järjestelmästä toiseen (Data Portability)**: Järjestelmän tulee tarjota rajapinta (`GET /ap/outbox?actor={actor_id}`), jonka kautta käyttäjä voi viedä omat tuottamansa kommentit (`Note`-objektit) ja tykkäykset standardissa JSON-LD/ActivityStreams 2.0 -muodossa.
- **Oikeus tulla unohdetuksi (Right to be Forgotten)**: Profiilin poiston yhteydessä Firebase Auth -tunnisteeseen liittyvät henkilötiedot poistetaan tai anonymisoidaan pysyvästi seuraavista tallennuspaikoista:
  - **BigQuery** (`bq-activitystreams`): kommentit poistetaan tai niiden sisältö korvataan merkkijonolla `[kommentti poistettu]` ketjurakenteen säilyttämiseksi, ja tykkäykset anonymisoidaan poistamalla käyttäjätunnisteet.
  - **Firestore** (`uutisseuranta.github.io` / frontend): käyttäjän preferenssit ja asetukset poistetaan. Firestore toimii tässä järjestelmässä yksinomaan frontend-preferenssien monilaitesynkronointiin (ks. [uutisseuranta.github.io DECISION_LOG.csv L-006](https://github.com/uutisseuranta/uutisseuranta.github.io/blob/main/DECISION_LOG.csv)) — ei backend-tietovarastona.
  - **Lokijärjestelmät**: henkilötiedot anonymisoidaan pysyvästi.

---

## 4. Kenttämääpäys: OpenGraph / Schema.org / AS2
Tämä taulukko määrittelee, miten sisällön rikastusprosessissa eri metadatastandardien kentät vastaavat toisiaan ja miten ne mäpätään W3C ActivityStreams 2.0 (AS2) -datamalliin.

| AS2-kenttä | OpenGraph (og:) | Schema.org (JSON-LD) | Huomiot ja prioriteetti |
|---|---|---|---|
| `name` | `og:title` | `headline` / `name` | Pidempi otsikko voittaa vertailussa (`longer(rss, og)`). |
| `summary` | `og:description` | `description` | `og:description` täydentää, jos RSS-syötteen kuvaus puuttuu. |
| `image` | `og:image` | `image` / `thumbnailUrl` | Ensimmäinen resolvoitava kuva käytetään. |
| `published` | `article:published_time` | `datePublished` | Prioriteetti: **JSON-LD `datePublished` → OG `article:published_time` → HTTP `Last-Modified`** |
| `updated` | `article:modified_time` | `dateModified` | Valinnainen kenttä. |
| `url` | `og:url` | `url` | Ensisijaisena lähteenä käytetään RSS-syötteen ``-linkkiä. |
| `attributedTo` | `article:author` | `author.name` | Sisällöntuottajan tai median nimi. |
| `type` | `og:type` (`article`) | `@type` (`NewsArticle`) | Validoidaan tyypin vastaavuus (`og:type = article`). |
| `tag` | — | `keywords` | Voikko-morfologiset tagit muunnetaan AS2 `Hashtag`-objekteiksi. |

---

## 5. Paginaatio ja identiteettien pysyvyys
- **Paginaatiostrategia (Päätös L-009)**: Järjestelmä käyttää yksinkertaista kyselyparametreihin perustuvaa paginaatiota (`?page=`) suorituskyvyn takaamiseksi, eikä ActivityPubin monimutkaisempaa `OrderedCollectionPage` + `next`/`prev` -linkkikenttäontologiaa.
- **Pysyvät tunnisteet (W3C DWBP BP7 / Päätös L-010)**: AS2-objektin `id` on pysyvä, muuttumaton IRI muodossa `https://uutisseuranta.net/ap/objects/{hash}`. Se erotetaan artikkelin operatiivisesta `url`-osoitteesta, jotta toimitukselliset URL-rakenteen muutokset eivät riko olemassa olevia linkityksiä ja sosiaalisia reaktioita. Hash lasketaan kaavalla `sha256(source + url)[:16]`.

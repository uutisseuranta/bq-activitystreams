# TECHNICAL_DESIGN.md

## Terminologia: `principalSet` vs `principal`

Uutisseuranta-järjestelmän arkkitehtuurissa ja rajapintamalleissa erotetaan toisistaan käsitteet `principalSet` ja `principal`:

### 1. `principal`
*   **Määritelmä:** Yksittäinen tunnistettu tai tunnistamaton toimija (actor / user / client).
*   **Käyttökohteet:** Viittaa suoraan tiettyyn identiteettiin, esimerkiksi Google OIDC-tokenista johdettuun `sub`-kenttään tai yksittäiseen IP-osoitteeseen rate limit -avaimena.
*   **Esimerkki:** `uid:test-user-sub-12345`

### 2. `principalSet`
*   **Määritelmä:** Kokoelma identiteettejä, ryhmiä tai rooleja, joita käsitellään yhtenä kokonaisuutena käyttöoikeuksien tarkistuksessa (Authorization / IAM).
*   **Käyttökohteet:** Mahdollistaa useiden eri toimijaryhmien tai pääsyoikeuksien määrittelyn yhdessä säännössä (esim. usean Gmail-domainin tai usean mikropalvelun ryhmittely yhdeksi pääsyjoukoksi).
*   **Rakenne:** Mallinnetaan listoina tai joukkoina (set), jotka arvioidaan reitityksen tai tietoturvakäsittelyn yhteydessä.

## Rate Limiting (Käyttörajat ja rajoitteet)

Järjestelmä suojaa rajapintojaan ja BigQuery-hakuja (erityisesti outbox full table scan) väärinkäytöltä käyttäen FastAPI:n `slowapi`-kirjastoa.

### Rajoitteet (In-Memory Storage)
*   **Periaate:** Rate limiting hyödyntää `slowapi`:n oletusarvoista in-memory-muistitallennustilaa.
*   **Cloud Run -vaikutukset:** Cloud Run -instanssien skaalautuessa vaakasuunnassa jokainen erillinen instanssi ylläpitää ja laskee rate limit -laskureitaan itsenäisesti. Rajoitus ei ole globaalisti synkronoitu eri instanssien välillä.
*   **Hyväksyntä alpha-vaiheessa:** Rakenne on hyväksytty alpha-vaiheessa, sillä instanssien vähimmäismäärä on asetettu yhteen (`min-instances=1`). Jos globaalille rajoitukselle syntyy tarvetta myöhemmin (instanssien määrän kasvaessa), siirrytään erilliseen jaettuun välimuistiin (kuten Redis).


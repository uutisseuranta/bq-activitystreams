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

## Käyttötapaukset (Use Cases)

Tämä osio määrittelee uutisseurannan taustajärjestelmän keskeiset käyttötapaukset (UC-1 – UC-8) ja niiden toteuttavat komponentit:

| UC | Nimi | Komponentti | Issue | Kuvaus |
|---|---|---|---|---|
| UC-1 | RSS-syötteen haku ja tallennus | `rss_fetch_job.py` | #2 | Hakee uutissyötteet ja tallentaa ne AS2-objektina BigQueryyn. |
| UC-2 | pubDate-ttomien uutisten rikastus | `og_enrichment_job.py` | #14 | Siirtää päivämäärättömät uutiset pending-tauluun rikastusta varten. |
| UC-3 | OG-tagipohjainen rikastus | `og_enrichment_job.py` | #24 | Rikastaa uutisen kuvat, kuvaukset ja aikaleimat URL-osoitteesta. |
| UC-4 | Morfologinen tägäys (Voikko) | `voikko_job.py` | #6 | Tuottaa uutisille suomenkieliset morfologiset tagit AS2 Hashtag-muodossa. |
| UC-5 | Uutisvirran lukeminen (Outbox) | `query_api.py` | — | Tarjoaa uutisvirran `/ap/outbox` -endpointista standardina AS2-datana. |
| UC-6 | Tykkäykset ja reaktiot | `write_api.py` | #33 | Käsittelee käyttäjien Like- ja Dislike-reaktiot ja tallentaa ne BQ-kantaan. |
| UC-7 | GDPR: käyttäjätietojen poisto | `write_api.py` | #37 | Anonymisoi kommentit ja poistaa tykkäykset tilin poiston yhteydessä. |
| UC-8 | OG-scraper API-endpoint | `og_scraper.py` | #23 | Tarjoaa `POST /ap/scrape` -endpointin uutislinkin reaaliaikaiseen parsimiseen. |

## Scraping-etiikka ja REP-standardi

Uutissivustojen ohjelmallinen haku (scraping) rikastusprosessissa (`og_enrichment_job.py` ja `og_scraper.py`) noudattaa tiukasti standardia **Robots Exclusion Protocol (RFC 9309)**:
*   **Asetusten noudattaminen:** Ennen jokaista HTTP-hakuilmoitusta haetaan ja luetaan kohdesivuston `robots.txt`-tiedosto. Jos säännöt estävät haun (`Disallow`), haku keskeytetään heti ja virhe kirjataan `og_enriched_error`-sarakkeeseen.
*   **Välimuisti:** Palvelimen ylikuormituksen estämiseksi `robots.txt`-säännöt välimuistitetaan paikallisesti 24 tunniksi.
*   **Kapasiteetin säästö:** Ladattavaa HTML-sivun kokoa rajoitetaan siten, että response-lukeminen keskeytetään heti, kun HTML-head-alue päättyy (max ~2 MB), mikä säästää sekä palvelimen että kohteiden verkkokapasiteettia.


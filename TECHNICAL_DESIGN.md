# Technical Design: bq-activitystreams

Tämä dokumentti kokoaa kaikkien tikettien arkkitehtuuripäätökset yhdeksi luettavaksi kokonaisuudeksi. Arkkitehtuuriperiaatteet ovat [DESIGN_GUIDELINES.md](./DESIGN_GUIDELINES.md) -tiedostossa.

Repositorio on nimetty uudelleen: `gcs-activitystreams` → `bq-activitystreams` kuvaamaan paremmin BigQuery-tietovarastovalintaa GCS:n sijaan.

---

## Yleiskuva

```
https://activitystreams.uutisseuranta.net/
  └── GET /ap/outbox?tag=asuminen&n=50  ← Cloud Run (query-api) → BigQuery
  └── POST /ap/scrape { url }           ← Cloud Run (og-scraper) → BigQuery
  └── POST /ap/activities               ← Cloud Run (write-api) → BigQuery

Kirjoittajat per taulu:

  activitystreams.objects          ← jobit #2 (RSS), #3 (Ahjo), #4 (HRI), #8 (OG-scraper), kirjoituspalvelu #7 (kommentit)
                                      suora MERGE-kirjoitus, tavallinen taulu
                                      tags-sarakkeen omistaja: Voikko-job (#6)
                                      like_count-sarakkeen omistaja: likes-and-updated-job (#11/#12)

  activitystreams_social.activities ← kirjoituspalvelu (#7): käyttäjätoiminnot
                                      (kommentit, tykkäykset, käyttäjän luomat objektit)
                                      append-only event log

  activitystreams_social.likes      ← kirjoituspalvelu (#7): Like-tapahtumat
  activitystreams.config            ← jobit päivittävät last_fetched_at ja dynaamiset URL:t
```

### GCP-konfiguraatio

| Muuttuja | Arvo |
|---|---|
| **Domain** | `activitystreams.uutisseuranta.net` |
| **GCP-projekti** | `uutisseuranta-activitystreams` |
| **Julkinen BigQuery dataset** | `activitystreams` (avoin data) |
| **Yksityinen BigQuery dataset** | `activitystreams_social` (sosiaalinen data) |
| **Sijainti** | `europe-north1` |
| **GitHub-repo** | `uutisseuranta/bq-activitystreams` |

### Cloud Scheduler -ajastukset

| Job | Cron | Kellonaika (EET) |
|---|---|---|
| `rss-fetch-job` | `0 * * * *` | kerran tunnissa |
| `ahjo-fetch-job` | `0 3 * * *` | 06:00 |
| `hri-fetch-job` | `30 3 * * *` | 06:30 |
| `voikko-enrich-job` | `30 * * * *` | 30 min välein |
| `likes-and-updated-job` | `*/15 * * * *` | 15 min välein |

Huom: `likes-and-updated-job` korvaa aiemmat erilliset `likes-sync-job` ja `activity-updated-job` -ajastukset. Molemmat laskennat ajetaan samassa Cloud Run Job -suorituksessa — ks. [Tykkäyslaskuri ja updated-aikaleima](#tykkäyslaskuri-ja-updated-aikaleima-1112).

> [!NOTE]
> **Kesäaika:** Cloud Scheduler käyttää UTC:tä eikä säädä kesäaikaa automaattisesti. EET (talviaika UTC+2) ja EEST (kesäaika UTC+3) eroavat tunnin. Cron-kommentit on merkitty EET-ajassa — kesällä ajastukset lähtevät tunnin myöhemmin kuin kommentti ilmoittaa. Tämä on hyväksytty trade-off: ajastusajalla ei ole kriittistä merkitystä tässä kontekstissa.

---

## Operatiivinen malli: fetch-ikkuna, config ja retry

Tämän luvun tarkoitus on kuvata, miten jobit säilyttävät jatkuvan fetch-ikkunan ilman aukkoja ja miten virhetilanteet käsitellään niin, etteivät ne katkaise dataa.

### Config-taulu: avaimet ja cold start

Config-taulu on kriittinen komponentti: RSS-, Ahjo- ja HRI-jobien fetch-ikkunan jatkuvuus riippuu siitä kokonaan. Ilman oikeaa `last_fetched_at`-arvoa job joko hakee kaksoiskappaleet tai jättää aukon dataan.

```sql
CREATE TABLE activitystreams.config (
  key           STRING    NOT NULL,
  value         STRING    NOT NULL,
  updated_at    TIMESTAMP NOT NULL,
  updated_by    STRING    OPTIONS(description='Cloud Run Job -palvelun nimi, esim. rss-fetch-job')
);
```

#### Kaikki avain-arvo-parit

| key | Arvomuoto | Kirjoittaa | Milloin |
|---|---|---|---|
| `rss.{source}.last_fetched_at` | ISO 8601 timestamp | `rss-fetch-job` | Onnistuneen ajon lopussa |
| `ahjo.last_fetched_at` | ISO 8601 timestamp | `ahjo-fetch-job` | Onnistuneen ajon lopussa |
| `hri.last_fetched_at` | ISO 8601 timestamp | `hri-fetch-job` | Onnistuneen ajon lopussa |
| `valtioneuvosto.rss_url` | URL-merkkijono | `rss-fetch-job` | Kun autodiscovery löytää uuden URL:n |

**Lukuoikeudet:** Kaikilla fetch-jobeilla on `roles/bigquery.dataViewer` config-tauluun.

**Kirjoitusoikeudet:** Jokainen job kirjoittaa vain omia avaimiaan. Oikeus: `roles/bigquery.dataEditor` (tai hienojakoisempi rivi-tason käytäntö tarvittaessa).

#### Cold start — mitä tapahtuu kun avain puuttuu

| Tilanne | Käyttäytyminen |
|---|---|
| `last_fetched_at` puuttuu (ensimmäinen ajo) | Job hakee kiinteältä fallback-aikaikkunalta (esim. `-24h`) ja kirjoittaa arvon config-tauluun onnistuneen ajon jälkeen |
| `valtioneuvosto.rss_url` puuttuu | `rss-fetch-job` ajaa autodiscoveryn, tallentaa löydetyn URL:n ja jatkaa normaalisti |
| config-taulu on kokonaan tyhjä | Kaikki jobit käyttävät omaa fallback-ikkunaansa — data ei katkea, mutta ensimmäinen ajo saattaa hakea päällekkäistä dataa |

Config-taulun rivejä ei koskaan poisteta — vain päivitetään (`MERGE UPDATE`). Tämä varmistaa että avain on aina olemassa toisen ajon jälkeen.

### Virheenkäsittely ja retry-strategia

`config`-taulun `last_fetched_at`-arvo päivitetään **ainoastaan kun koko ajo on suoritettu onnistuneesti**. Epäonnistunut ajo ei päivitä arvoa — seuraava ajo käyttää edellistä onnistunutta ajankohtaa fetch-ikkunana, jolloin yksikään artikkeli ei jää välistä.

HTTP-virheiden retry-strategia:

| HTTP-statuskoodi | Toiminto |
|---|---|
| `2xx` | Jatketaan normaalisti |
| `429 Too Many Requests` | Odotetaan `Retry-After`-otsakkeen mukainen aika (tai 60 s), max 3 yritystä |
| `5xx` | Eksponentiaalinen backoff: 30 s, 5 min, 15 min. Max 3 yritystä. |
| `404 Not Found` | Kirjataan lokiin, jatketaan muiden lähteiden käsittelyä. Ei retryä. |
| Connection error / timeout | Sama kuin `5xx`. |

BigQuery MERGE-kirjoitusvirheissä koko batch peruutetaan, `last_fetched_at` ei päivy, virhe kirjataan lokiin, ja Cloud Run Job palauttaa exit code `1`.

---

## Jobit ja palvelut: yhteenvetotaulukko

| Job / palvelu | Tyyppi | Cron / endpoint | Kirjoittaa | Vastuu |
|---|---|---|---|---|
| rss-fetch-job | Cloud Run Job | `0 * * * *` | `activitystreams.objects` | Uutissyötteet (RSS) |
| ahjo-fetch-job | Cloud Run Job | `0 3 * * *` | `activitystreams.objects` | OpenAhjo-päätökset |
| hri-fetch-job | Cloud Run Job | `30 3 * * *` | `activitystreams.objects` | HRI-datasetit |
| voikko-enrich-job | Cloud Run Job | `30 * * * *` | `activitystreams.objects.tags*` | Lemmatisoidut tagit (Voikko) |
| likes-and-updated-job | Cloud Run Job | `*/15 * * * *` | `activitystreams.objects.like_count, updated` | Tykkäyslaskuri + updated |
| query-api | Cloud Run Service | `GET /ap/outbox` | — | AS2 outbox, lukupään API |
| og-scraper | Cloud Run Service | `POST /ap/scrape` | `activitystreams.objects` | OG-scraper valikoiduille domaineille |
| write-api | Cloud Run Service | `POST /ap/activities` | `activitystreams_social.*` | Käyttäjäaktiviteetit (kommentit, tykkäykset) |

\* Voikko-job on `tags`-sarakkeen omistaja; muut jobit eivät koskaan kirjoita `tags`-kenttää.

---

## Autentikaatio ja valtuutus

### Defense in depth -malli (#25)

Autentikaatio toteutetaan kahdella kerroksella Googlen zero trust -suositusten mukaisesti:

1. **Kerros 1 (infra):** Cloud Run IAM — `--no-allow-unauthenticated` hylkää pyynnöt ilman validia OIDC-tokenia ennen kuin ne saavuttavat sovelluskoodia
2. **Kerros 2 (sovellus):** `verify_oauth2_token()` — validoi tokenin sisällön (sub, email_verified, audience)

| Palvelu | Cloud Run IAM | Sovellustason auth | Dataset | Perustelu |
|---|---|---|---|---|
| `query-api` (`GET /ap/outbox`) | `--allow-unauthenticated` | Ei | `activitystreams` | Avoin data — sama periaate kuin RSS-syötteet |
| `og-scraper` (`POST /ap/scrape`) | `--allow-unauthenticated` | Ei | `activitystreams` | Avoin data — suojaus domain-whitelistilla ja rate limitingillä |
| `write-api` (`POST /ap/activities`) | **`--no-allow-unauthenticated`** | **Google OIDC `id_token`** | `activitystreams_social` | Käyttäjäkohtainen sosiaalinen data |

Kaikki kolme palvelua ovat erillisiä Cloud Run -palveluja. Julkiset palvelut (`query-api`, `og-scraper`) kirjoittavat vain `activitystreams`-datasettiin (avoin data). Kirjoitusoikeus BigQueryyn on Cloud Run -palvelun palvelutilillä IAM-oikeuksilla, ei kutsujalla.

`write-api` on ainoa palvelu joka kirjoittaa `activitystreams_social`-datasettiin. Se vaatii autentikaation molemmilla kerroksilla.

### Gmail SSO ja kirjoituspalvelu (#7, #19)

Kirjoituspalvelu (#7) tukee loppukäyttäjien autentikointia Google-kirjautumisella (Gmail SSO, OIDC `id_token`). Cloud IAM -rooli `roles/run.invoker` säilyy **palvelutilien** (Cloud Scheduler, Cloud Run Jobit) kontrollimekanismina. Loppukäyttäjien valtuutus tapahtuu sovellustasolla `id_token`-validoinnilla — erillistä GCP IAM -roolia ei loppukäyttäjille luoda.

#### Autentikaatiovirta

```
Selain
  └▶ Google Sign-In (OAuth2 / OIDC)
        └▶ id_token (JWT: sub, email, email_verified)
              └▶ POST /ap/activities
                    Authorization: Bearer <id_token>
                        └▶ Cloud Run (write-api)
                              └▶ google-auth-library: verify_oauth2_token()
                                    └▶ email_verified = true?
                                          └▶ sub → actor-IRI → BigQuery
```

#### Token-validointilogiikka

Tukee kahta audience-arvoa confused deputy -hyökkäyksen estämiseksi:
- `GOOGLE_CLIENT_ID`: loppukäyttäjän Sign in with Google -tokeni
- `CLOUD_RUN_SERVICE_URL`: service-to-service OIDC-tokeni (Cloud Scheduler, Cloud Run Jobit)

```python
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

ALLOWED_AUDIENCES = [GOOGLE_CLIENT_ID, CLOUD_RUN_SERVICE_URL]

def verify_auth_token(bearer_token: str) -> str:
    """
    Yrittää validoida tokenin jokaisella tuetulla audience-arvolla.
    Palauttaa: sub (str)
    Heittää: HTTPException(401) jos tokeni ei kelpaa
    """
    for audience in ALLOWED_AUDIENCES:
        try:
            info = id_token.verify_oauth2_token(
                bearer_token,
                google_requests.Request(),
                audience=audience
            )
            if not info.get("email_verified"):
                raise HTTPException(403, "email_verified = false")
            return info["sub"]
        except HTTPException:
            raise
        except Exception:
            continue
    raise HTTPException(401, "Authentication failed.")

def actor_iri(sub: str) -> str:
    return f"https://activitystreams.uutisseuranta.net/ap/users/{sub}"
```

HTTP-statuskoodit autentikaatiovirheissä:

| Tilanne | Status |
|---|---|
| `Authorization`-otsake puuttuu | `401 Unauthorized` |
| Token vanhentunut tai väärä `aud` | `401 Unauthorized` |
| `email_verified = false` | `403 Forbidden` |
| `actor` ≠ tokenin `sub` (Update/Delete) | `403 Forbidden` |

#### Sovellustason rooli: activitystreams-writer

Validoitu Google-tili (`email_verified = true`) saa sovellustason roolin **activitystreams-writer**. Tämä ei ole GCP IAM -rooli vaan kirjoituspalvelun sisäinen konsepti.

| Aktiviteetti | Sallittu | Ehto |
|---|---|---|
| `Create` | ✅ | `email_verified = true` |
| `Like` | ✅ | `email_verified = true` |
| `Update` | ✅ | `email_verified = true` + `actor`-IRI:n `sub` vastaa tokenin `sub` |
| `Delete` | ✅ | `email_verified = true` + `actor`-IRI:n `sub` vastaa tokenin `sub` |
| `Dislike`, `Announce`, `Undo` | ❌ | `400 Bad Request` — ks. [Tuetut aktiviteetit](#tuetut-aktiviteetit) |

#### `actor`-IRI ja Google `sub`

Käyttäjän pysyvä identiteetti (AS2 `actor`) sidotaan Google-tilin `sub`-kenttään, joka on vakaa tunnus sähköpostiosoitteen mahdollisista muutoksista huolimatta:

```
actor = "https://activitystreams.uutisseuranta.net/ap/users/{google-sub}"
```

Käyttäjän luomien objektien `id`-kenttä sisältää edelleen erillisen `ulid`-tunnuksen (ks. [`id`-kentän kaava lähteittäin](#id-kentän-kaava-lähteittäin)), mutta omistajuustarkistus tehdään `actor`-IRI:n `sub`-osasta.

#### Ympäristömuuttujat (täydennys #16:een)

| Muuttuja | Arvo | Kuvaus |
|---|---|---|
| `GOOGLE_CLIENT_ID` | `<OAuth2-client-id>.apps.googleusercontent.com` | Loppukäyttäjän `id_token`-validointiin (audience) |
| `CLOUD_RUN_SERVICE_URL` | `https://write-api-HASH.europe-north1.run.app` | Service-to-service `id_token`-validointiin (audience) |
| `ALLOW_MOCK_AUTH` | `false` | `true` vain kehitysympäristössä — sallii `Bearer mock-test` -tokenin |
| `ALLOWED_EMAIL_DOMAINS` | *(tyhjä = kaikki)* | Haluttaessa rajoitetaan pääsy tiettyihin sähköpostidomaineihin |

---

## BigQuery-skeema (#1)

### `activitystreams.objects` — artikkelit, päätökset, datasetit

```sql
CREATE TABLE activitystreams.objects (
  id              STRING    NOT NULL OPTIONS(description='AS2 id – domain-pohjainen IRI, primääriavain'),
  source          STRING    NOT NULL OPTIONS(description='Lähde: rss | ahjo | hri | scraped | user'),
  published       TIMESTAMP NOT NULL OPTIONS(description='AS2 published – pakollinen, taulu on partitionoitu tämän mukaan'),
  updated         TIMESTAMP          OPTIONS(description='AS2 updated – päivittyy käyttäjäaktiivisuudesta (#12)'),
  tags            ARRAY<STRING>      OPTIONS(description='Lemmatisoidut tagit (Voikko #6)'),
  tags_enriched   BOOL      NOT NULL OPTIONS(description='TRUE kun Voikko-job on käsitellyt rivin — estää ikuisen uudelleenkäsittelysilmukan tyhjän tuloksen tapauksessa'),
  like_count      INT64     NOT NULL OPTIONS(description='Tykkäysmäärä activitystreams.likes-taulusta, päivitetään likes-and-updated-jobilla (#11/#12)'),
  deleted         BOOL      NOT NULL OPTIONS(description='Pehmeä poisto'),
  object_json     JSON               OPTIONS(description='Koko AS2-objekti natiivina JSON-tyypinä')
)
PARTITION BY DATE(published)
CLUSTER BY source, published;
```

> **Oletusarvot:** `tags_enriched=FALSE`, `like_count=0`, `deleted=FALSE` asetetaan INSERT-lauseissa, ei DDL:ssä. BigQuery CREATE TABLE ei tue DEFAULT-lausekkeita.

> **Miksi `published` on NOT NULL?**
> Taulu on partitionoitu `DATE(published)`-sarakkeen mukaan. Ilman `published`-arvoa rivi ei partitionoidu oikein ja queryt hidastuvat merkittävästi. RSS-job ohittaa artikkelit joilta `<pubDate>` puuttuu (ks. #2). OG-scraper tallentaa `published = scrape-hetki` fallbackina. Poikkeustapaukset käsitellään erillisessä prosessissa (#14).

### `activitystreams_social.activities` — append-only event log

```sql
CREATE TABLE activitystreams_social.activities (
  id            STRING    NOT NULL,
  type          STRING    NOT NULL,  -- Create | Update | Delete | Add | Remove | Like
  actor         STRING    NOT NULL,
  object_id     STRING,
  object_type   STRING,
  object_json   JSON,
  target_id     STRING,
  in_reply_to   STRING,
  thread_root   STRING,
  published     TIMESTAMP NOT NULL,
  received_at   TIMESTAMP NOT NULL
)
PARTITION BY DATE(published)
CLUSTER BY type, actor;
```

### `activitystreams_social.likes` — tykkäykset

```sql
CREATE TABLE activitystreams_social.likes (
  activity_id   STRING    NOT NULL,
  actor         STRING    NOT NULL,
  object_id     STRING    NOT NULL,
  published     TIMESTAMP NOT NULL
)
PARTITION BY DATE(published)
CLUSTER BY object_id, actor;
```

### `id`-kentän kaava lähteittäin

| Lähde | `id`-kaava |
|---|---|
| RSS | `https://activitystreams.uutisseuranta.net/ap/objects/articles/{source}/{sha256(url)}` |
| Ahjo | `https://activitystreams.uutisseuranta.net/ap/objects/decisions/helsinki/{register_id}` |
| HRI-datasetti | `https://activitystreams.uutisseuranta.net/ap/objects/hri/datasets/{ckan-uuid}` |
| HRI-kategoria | `https://activitystreams.uutisseuranta.net/ap/objects/hri/groups/{group-name}` |
| OG-scrapattu | `https://activitystreams.uutisseuranta.net/ap/objects/scraped/{sha256(url)}` |
| Käyttäjän luoma objekti | `https://activitystreams.uutisseuranta.net/ap/objects/comments/{ulid}` |
| Käyttäjän identiteetti (actor) | `https://activitystreams.uutisseuranta.net/ap/users/{google-sub}` |

### `source`-sarake vs. `source`-query-parametri

`source`-sarake **on** `objects`-taulun sisäinen kenttä — se kertoo mistä objekti on peräisin (rss, ahjo, hri, scraped, user). Se ei koskaan näy API:n query-parametreina. `GET /ap/outbox?source=ahjo` palauttaa `400 Bad Request`. Client suodattaa aina tageilla, ei lähteellä.

---

## `published` ja `updated` lähteittäin (#9)

| Lähde | `published` | `updated` |
|---|---|---|
| RSS-artikkeli | `<pubDate>` — pakollinen, puuttuva artikkeli ohitetaan | `<atom:updated>` jos saatavilla, muuten `published` |
| OpenAhjo-päätös | `latest_decision_date` | API:n `modified` jos muuttunut |
| HRI-datasetti | `metadata_created` | `metadata_modified` |
| OG-scrapattu | `article:published_time` OG-tagista | `article:modified_time`, fallback scrape-hetki |
| OG-scrapattu (ei pubDate) | Scrape-hetki (fallback) | Scrape-hetki |

`published` ei koskaan muutu objektin päivityksissä. `updated` päivittyy myös kun artikkeliin kohdistuu käyttäjäaktiivisuutta (kommentti, tykkäys) — ks. #12.

---

## Cloud Run Job: RSS-syötteet (#2)

Ajastus: `0 * * * *` (kerran tunnissa). Virheenkäsittely: ks. [Virheenkäsittely ja retry-logiikka](#virheenkäsittely-ja-retry-logiikka).

**Lähteet:**

| Lähde | RSS-URL |
|---|---|
| Helsingin Sanomat | `https://www.hs.fi/rss/tuoreimmat.xml` |
| Iltalehti | `https://www.iltalehti.fi/rss/uutiset.xml` |
| Ilta-Sanomat | `https://www.is.fi/rss/tuoreimmat.xml` |
| Kauppalehti | `https://feeds.kauppalehti.fi/rss/main` |
| MTV Uutiset | `https://www.mtvuutiset.fi/rss.xml` |
| Valtioneuvosto | autodiscovery → tallennetaan `config`-tauluun |

**pubDate-vaatimus:** `<pubDate>` on pakollinen. Artikkeli jolta se puuttuu tai jota ei voi parsia ohitetaan ja merkitään lokiin. Syötteet joilta `<pubDate>` puuttuu rakenteellisesti käsitellään erillisessä prosessissa — ks. #14.

**Autodiscovery-logiikka:** Koodi lukee autodiscovery-lähteiden URL:n aina `RSS_FEEDS`-listan `autodiscover_url`-kenttästä, ei kovakoodattuna. Tämä mahdollistaa uusien autodiscover-lähteiden lisäämisen pelkällä env-muuttujapäivityksellä ilman koodimuutoksia:

```python
def get_feed_url(feed: dict, bq_client) -> str | None:
    """
    Palauttaa RSS-URL:n: lukee config-taulusta jos autodiscover,
    muuten käyttää staattista URL:a.
    `feed`-dict on yksittäinen RSS_FEEDS-listan alkio.
    """
    if not feed.get("autodiscover"):
        return feed["url"]

    config_key = f"{feed['name']}.rss_url"  # esim. "valtioneuvosto.rss_url"
    autodiscover_url = feed["autodiscover_url"]  # luettu RSS_FEEDS-listasta, ei kovakoodattu

    # Yritetään ensin config-taulusta
    row = bq_client.query(
        "SELECT value FROM activitystreams.config WHERE key = @key LIMIT 1",
        job_config=QueryJobConfig(query_parameters=[
            ScalarQueryParameter("key", "STRING", config_key)
        ])
    ).result()
    for r in row:
        return r.value

    # Ei löydy → autodiscovery
    url = discover_feed_url(autodiscover_url)
    if url:
        bq_client.query("""
            MERGE activitystreams.config T
            USING (SELECT @key AS key, @url AS value) S ON T.key = S.key
            WHEN MATCHED THEN
                UPDATE SET T.value = S.value, T.updated_at = CURRENT_TIMESTAMP(), T.updated_by = 'rss-fetch-job'
            WHEN NOT MATCHED THEN
                INSERT (key, value, updated_at, updated_by)
                VALUES (S.key, S.value, CURRENT_TIMESTAMP(), 'rss-fetch-job')
        """,
        job_config=QueryJobConfig(query_parameters=[
            ScalarQueryParameter("key", "STRING", config_key),
            ScalarQueryParameter("url", "STRING", url)
        ])).result()
    return url


# Käyttö: iterointi RSS_FEEDS-listan yli
for feed in rss_feeds:  # rss_feeds = json.loads(os.environ["RSS_FEEDS"])
    url = get_feed_url(feed, bq_client)
    if url:
        process_feed(feed["name"], url)
```

**Tallennuslogiikka:** MERGE-operaatio `id`-kentän perusteella (ks. [`id`-kentän kaava](#id-kentän-kaava-lähteittäin)). `tags`-, `tags_enriched`- ja `like_count`-sarakkeet jätetään `WHEN MATCHED` -haaran ulkopuolelle — Voikko-job (#6) ja likes-and-updated-job (#11/#12) omistavat ne. `WHEN NOT MATCHED`: `tags = []`, `tags_enriched = FALSE`, `like_count = 0`.

```sql
MERGE activitystreams.objects T
USING activitystreams.objects_temp S ON T.id = S.id
WHEN MATCHED AND S.updated > T.updated THEN
    UPDATE SET
        T.object_json = S.object_json,
        T.published   = S.published,
        T.updated     = S.updated,
        T.source      = S.source
        -- tags, tags_enriched, like_count ja deleted jätetään tarkoituksella pois
WHEN NOT MATCHED THEN
    INSERT (id, source, published, updated, tags, tags_enriched, like_count, deleted, object_json)
    VALUES (S.id, S.source, S.published, S.updated, [], FALSE, 0, FALSE, S.object_json)
```

**Kuva-prioriteetti:** (1) `<media:thumbnail>`, (2) `<enclosure type="image/*">`, (3) `<image>` kanavan tasolla.

---

## Cloud Run Job: OpenAhjo-päätökset (#3)

Ajastus: `0 3 * * *` (06:00 EET). Virheenkäsittely: ks. [Virheenkäsittely ja retry-logiikka](#virheenkäsittely-ja-retry-logiikka). Base URL: `http://dev.hel.fi/openahjo/v1`.

**Fetch-ikkuna:** Ei kiinteää `-24h`. Käytetään `config`-taulun `ahjo.last_fetched_at`-arvoa — päivitetään vasta onnistuneen ajon jälkeen.

**Kenttäkartoitus:**

| AS2-kenttä | OpenAhjo-kenttä | Huomio |
|---|---|---|
| `id` | `register_id` (esim. `HEL-2026-012345`) | Muunnetaan IRI-muotoon: `…/decisions/helsinki/{register_id}` |
| `name` | `subject` | |
| `summary` | `agenda_item.content` (resolution) | |
| `published` | `latest_decision_date` | |
| `attributedTo.name` | `policymaker.name` | |

---

## Cloud Run Job: HRI-datasetit (#4)

Ajastus: `30 3 * * *` (06:30 EET). Virheenkäsittely: ks. [Virheenkäsittely ja retry-logiikka](#virheenkäsittely-ja-retry-logiikka). Base URL: `https://hri.fi/data/api/3/action/`.

Datasetit tallennetaan AS2 `Document`-objekteina, kategoriat `OrderedCollection`-objekteina. Kaksikieliset metatiedot: `nameMap.fi` / `nameMap.sv`, `summaryMap.fi` / `summaryMap.sv`.

**Kenttäkartoitus:**

| AS2-kenttä | CKAN-kenttä |
|---|---|
| `published` | `metadata_created` |
| `updated` | `metadata_modified` |
| `nameMap.fi` | `result.title` |
| `nameMap.sv` | `result.title_translated.sv` |

`actor` asetetaan aina organisaatiotasolle (HRI), ei yksittäiselle CKAN-käyttäjälle.

---

## Cloud Run Job: Voikko-tagienrikastus (#6)

Ajastus: `30 * * * *` (30 min välein). Virheenkäsittely: ks. [Virheenkäsittely ja retry-logiikka](#virheenkäsittely-ja-retry-logiikka). Käsittelee erissä (100 kpl) objektit joilla `tags_enriched = FALSE`.

**Analyysi:** `libvoikko` palauttaa sanan `BASEFORM`-lemman. Käytetyt sanaluokat: `nimisana`, `erisnimi`, `paikannimi`, `paikannimi_ulkomaat`, `teonsana`, `määrite`. Hyjätään: `suhdesana`, `sidesana`, `kieltosana`, `asemosana`, `partikkeli`.

Top-16 yleisintä lemmaa tallennetaan `tags`-sarakkeeseen `MERGE UPDATE` -operaatiolla. Voikko-job on `tags`-sarakkeen ainoa kirjoittaja. Job asettaa aina `tags_enriched = TRUE` käsittelyn jälkeen — myös silloin kun tulos on tyhjä lista. Tämä estää rivin jäämisen ikuiseen uudelleenkäsittelysilmukkaan.

```sql
MERGE activitystreams.objects T
USING enriched_batch S ON T.id = S.id
WHEN MATCHED THEN
    UPDATE SET
        T.tags          = S.tags,
        T.tags_enriched = TRUE
```

---

## Cloud Run endpoint: OG-scraper (#8)

`POST /ap/scrape { "url": "https://..." }` — palauttaa AS2 `Article`-objektin ja tallentaa sen `objects`-tauluun.

Endpoint on julkinen (ks. [Autentikaatio ja valtuutus](#autentikaatio-ja-valtuutus)). Kirjoitusoikeus BigQueryyn on Cloud Run -palvelun palvelutilillä IAM-oikeuksilla, ei kutsujalla.

**Domain-whitelist:** Scraper hyväksyy vain manuaalisesti ylläpidetyn domain-listan URL:t. Uusi domain lisätään listaan harkiten ja testataan ennen käyttöönottoa. Tuntemattomat domainit palauttavat `403 Forbidden`. Tämä estää SSRF-hyväksikäytöt ja tahattoman ulkopuolisten palvelinten kuormittamisen.

**Duplikaattipyynnöt:** Sama URL kahdesti tuottaa saman `id`:n (`sha256(url)`), joten MERGE-operaatio hoitaa duplikaatin hiljaisesti.

**Kenttäkartoitus** (ks. myös [`id`-kentän kaava](#id-kentän-kaava-lähteittäin)):

| AS2-kenttä | OG-lähde | Fallback |
|---|---|---|
| `name` | `og:title` | `<title>` |
| `summary` | `og:description` | `<meta name=description>` |
| `image` | `og:image` | — |
| `published` | `article:published_time` | Scrape-hetki |
| `updated` | `article:modified_time` | Scrape-hetki |

`published` käyttää scrape-hetkeä fallbackina (toisin kuin RSS-job, joka ohittaa artikkelin). Tämä on hyväksyttää koska OG-scraper on käyttäjän manuaalisesti käynnistämä toiminto.

---

## Cloud Run: kirjoituspalvelu (#7)

Vastaanottaa AS2-aktiviteetteja JSON:na. Validoi tyypin ja pakolliset kentät. Kirjoittaa `activities`- ja tarvittaessa `likes`-tauluun BigQuery Storage Write API:lla.

Autentikaatio: ks. [Autentikaatio ja valtuutus — Gmail SSO](#gmail-sso-ja-kirjoituspalvelu-7-19).

### Tuetut aktiviteetit

| Aktiviteetti | Kirjoitetaan |
|---|---|
| `Create` | `activities` (kommentti, vastaus, käyttäjän artikkeli) |
| `Update` | `activities` (päivitetty objekti) |
| `Delete` | `activities` + `objects.deleted = TRUE` |
| `Add` | `activities` (tagi-operaatio) |
| `Remove` | `activities` (tagi-operaatio) |
| `Like` | `activities` + `likes` |
| `Dislike` | ❌ 400 Bad Request |
| `Announce` | ❌ 400 Bad Request |
| `Undo` | ❌ 400 Bad Request — ks. alla |

**Miksi `Undo` ei ole tuettu?** Tykkäys tallennetaan anonymisoituna: `likes`-taulussa ei ole käyttäjätunnistetta jolla tykkäyksen voisi yksilöidä jälkikäteen. Kun data on anonymisoitu, käyttäjä ei enää omista sitä eikä voi siksi perua toimintoa. Tämä on tietoinen arkkitehtuuripäätös, ei puuttuva toteutus (katso [DECISION_LOG.csv](file:///Users/jaakkokorhonen/uutisseuranta/bq-activitystreams/DECISION_LOG.csv) -> L-001).

### Kommenttiketjun syvyysvalidointi

```
in_reply_to kohde on Article → luo kommentti (taso 1)
in_reply_to kohde on Comment → luo vastaus (taso 2)
in_reply_to kohde on Note/vastaus → 400 Bad Request
```

`thread_root` täydennetään automaattisesti palvelimella.

### Delete-semantiikka

Delete ei poista tietokannasta. `activities`-tauluun kirjataan `Delete`-tapahtuma ja `objects.deleted` asetetaan `TRUE`:ksi. Poistettu kommentti näytetään paikkamerkkinä `[kommentti poistettu]` jos sillä on vastauksia.

### Update-semantiikka

Käyttäjä voi päivittää **vain oman kommenttinsa** sisällön. Organisaatioiden julkaisemia objekteja (RSS, Ahjo, HRI, OG-scraped) ei voi päivittää `Update`-aktiviteetilla — ne ovat jobien omistamia.

- `actor` validoidaan: `Update`-pyynnön `actor`-IRI:n `sub`-osa täytyy vastata alkuperäisen `Create`-aktiviteetin `actor`-IRI:n `sub`-osaa. Jos ne eivät täsmää, palautetaan `403 Forbidden`.
- `published` ei muutu — vain `object_json` ja `updated` päivittyvät.
- `Update`-aktiviteetti kirjoitetaan `activities`-tauluun ja `objects.object_json` päivitetään MERGE-operaatiolla.
- `thread_root`-artikkelin `updated` päivittyy muokatusta kommentista (ks. [Tykkäyslaskuri ja updated-aikaleima](#tykkäyslaskuri-ja-updated-aikaleima-1112)).

---

## Cloud Run: outbox-endpoint (#10)

`GET /ap/outbox?tag=asuminen&n=50`

### Paginaatiomalli

Client pyytää aina alusta `n` kappaletta. Ei kursoreja, ei sivunumeroita. Yli 500:n `n`-arvo palauttaa `400 Bad Request`.

```
GET /ap/outbox?tag=asuminen&n=5     → top-5
GET /ap/outbox?tag=asuminen&n=50    → top-50 (sisältää edellisen 5, client suodattaa)
GET /ap/outbox?tag=asuminen&n=500   → top-500 (maksimi)
```

Kun `totalItems > 500`, UI näyttää tagipilven josta voi tehdä uuden haun ([uutisseuranta.github.io #16](https://github.com/uutisseuranta/uutisseuranta.github.io/issues/16)).

### AS2-rakenne

```json
{
  "@context": "https://www.w3.org/ns/activitystreams",
  "type": "OrderedCollection",
  "id": "https://activitystreams.uutisseuranta.net/ap/outbox?tag=asuminen&n=50",
  "totalItems": 4821,
  "orderedItems": [ ]
}
```

Ei `next`, ei `prev`, ei `first`. `totalItems` kertoo clientille paljonko pyytää maksimissaan.

### Järjestys

```sql
ORDER BY
  relevance     DESC,            -- osuvien hakutagien määrä
  like_count    DESC,            -- suorat tykkäykset objects-taulusta
  updated       DESC,            -- viimeisin aktiivisuus
  published     DESC NULLS LAST, -- alkuperäinen julkaisu
  id            ASC
```

### Query-parametrit

| Parametri | Kuvaus | Oletus | Maksimi |
|---|---|---|---|
| `tag` | Toistuva. Pakollinen — `400` jos puuttuu. | — | — |
| `n` | Palautettavien määrä. Yli 500 → `400`. | 50 | 500 |

### `totalItems`-caching

`COUNT(*)`-kysely cachetetaan Cloud Run -muistissa 5 minuutiksi tag-kombinaatiota kohden. Cache on instanssikohtainen — `totalItems` on approksimatiivinen arvo. Caching vähentää merkittävästi BigQuery-kustannuksia: COUNT(*) ei aja per pyynto, vain 5 minuutin välein.

---

## Tykkäyslaskuri ja updated-aikaleima (#11/#12)

`likes-and-updated-job` ajaa 15 minuutin välein kaksi laskentaa peräkkäin samassa Cloud Run Job -suorituksessa. Nämä olivat aiemmin erilliset jobit; ne on yhdistetty koska molemmat kirjoittavat samaan `objects`-tauluun samalla 15 min rytmillä. Virheenkäsittely: ks. [Virheenkäsittely ja retry-logiikka](#virheenkäsittely-ja-retry-logiikka).

### Vaihe 1: like_count-päivitys

`like_count INT64 NOT NULL DEFAULT 0` on suoraan `activitystreams.objects`-taulussa.

```sql
MERGE activitystreams.objects T
USING (
  SELECT object_id, COUNT(*) AS cnt
  FROM activitystreams.likes
  GROUP BY object_id
) S ON T.id = S.object_id
WHEN MATCHED AND T.deleted = FALSE
  THEN UPDATE SET T.like_count = S.cnt
```

- Laskuri voi vain **kasvaa** — `Undo Like` ei ole mahdollinen (ks. [Miksi `Undo` ei ole tuettu?](#tuetut-aktiviteetit))
- Laskuri on anonyymi — ei tietoa kuka tykkäsi
- `deleted = FALSE` -ehto estää poistettujen objektien päivittymisen

### Vaihe 2: updated-aikaleiman päivitys

Kun artikkeliin kohdistuu käyttäjäaktiivisuutta, **thread_root-artikkelin** `updated` päivittyy. Poistetut aktiviteetit suodatetaan `object_id`:n perusteella: jos `activities`-taulussa on `type = 'Delete'` samalla `object_id`:llä, kyseinen aktiviteetti jätetään huomiotta.

| Tapahtuma | Päivittää `updated` |
|---|---|
| Artikkelin sisältö muuttuu | ✅ |
| Käyttäjä kommentoi artikkelia | ✅ |
| Käyttäjä tykkää artikkelista | ✅ |
| Käyttäjä kommentoi kommenttia | ✅ (thread_root) |
| Käyttäjä tykkää kommentista | ✅ (thread_root) |

```sql
SELECT COALESCE(thread_root, object_id) AS root_url,
       MAX(published) AS last_activity_at
FROM activitystreams.activities a
WHERE type IN ('Like', 'Create')
  AND NOT EXISTS (
    SELECT 1 FROM activitystreams.activities d
    WHERE d.type = 'Delete' AND d.object_id = a.object_id
  )
GROUP BY root_url
```

`COALESCE(thread_root, object_id)` varmistaa että artikkeliin suoraan kohdistuvat tykkäykset (joilla `thread_root = NULL`) käsitellään oikein. `updated` ei koskaan kulje taaksepäin (`AND @last_activity_at > updated`).

> [!NOTE]
> **Poistetun artikkelin kommentit:** Jos artikkeli poistetaan mutta sen kommenteille ei kirjata omaa `Delete`-aktiviteettia, näiden kommenttien `Like`-tapahtumat voivat edelleen kasvattaa `updated`-aikaleimaa. Kommentit voivat jäädä elämään omaa elämäänsä poistetun artikkelin jälkeen. Tämä on hyväksytty trade-off: vaikutus on kosmeettinen, koska poistettu artikkeli ei palaudu outbox-hauissa (`WHERE deleted = FALSE`).

Avoimen datan BigQueryyn kirjoitetaan vain aikaleima — ei tietoa kuka kommentoi tai tykkäsi.

### `likes`-kenttä AS2-vastauksessa

Query-API upottaa `like_count`-arvon palautushetkellä suoraan AS2-objektiin:

```json
{
  "type": "Article",
  "id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/hs/abc123",
  "name": "Artikkelin otsikko",
  "likes": 42
}
```

`object_json`-sarakkeeseen ei tallenneta `likes`-kenttää pysyvästi — se lasketaan lennossa.

---

## RSS-syötteet ilman pubDate (#14)

RSS-syötteet joilta `<pubDate>` puuttuu rakenteellisesti jäävät nykyisellä logiikailla indeksoimatta. Ratkaisu (toteutetaan tarvittaessa — ks. #18):

1. RSS-job tallentaa nämä artikkelit väliaikaiseen varastoon (`activitystreams.objects_pending`)
2. Erillinen rikastusjob hakee `published`-arvon muista lähteistä: OG-scraper (#8), HTTP `Last-Modified`, JSON-LD `datePublished`
3. Kun `published` on selvitetty, artikkeli siirretään normaaliin `objects`-tauluun
4. Jos `published` ei selviy 7 päivässä, artikkeli hävitetään välivarastosta

Prioriteetti: **matala** — toteutetaan vasta kun jokin RSS-lähde oikeasti aiheuttaa ongelman. Skeema: ks. #18.

---

## Logging ja monitoring (#17)

Kaikki Cloud Run -palvelut ja jobit logittavat tapahtumansa Google Cloud Loggingiin (structured logging).

- **Logitasot:** `INFO` normaaleille suorituksille, `WARNING` toipuville virheille (onnistunut retry), `ERROR` pysyville virheille (ajo epäonnistuu, exit code ≠ 0).
- **Jobien seuranta:** jokainen Cloud Run Job -ajo kirjoittaa jobin nimen, ajon tunnisteen ja lopputuloksen (`success` / `failure`) lokiin. Virheolosuhteissa lokirivi sisältää myös `last_fetched_at`-arvon ja HTTP-/BigQuery-virhekoodin.
- **Alertit:** Cloud Monitoring -hälytykset asetetaan seuraaviin tilanteisiin:
  - sama job epäonnistuu vähintään N kertaa peräkkäin (esim. `rss-fetch-job` 3× exit code `1`),
  - HTTP 5xx -virheiden määrä ylittää konfiguroidun rajan tietylle palvelulle (esim. `query-api`),
  - latenssi kasvaa merkittävästi normaalista (p95:n kasvu).

Lokien avulla voidaan jäljittää yksittäisten virheiden juurisyy ja vahvistaa, että `last_fetched_at` ei ole päivittynyt epäonnistuneen ajon aikana.

---

## Kustannusarvio

| Resurssi | Arvio | Hinta |
|---|---|---|
| BigQuery kyselyt (100k riviä, n=500) | ~110 MB/pyynto | ~$0.0007/pyynto |
| Ilmainen 1 TB/kk -kiintiö | ~9 000 pyyntoa 100k riville | $0/kk |
| `totalItems` COUNT(*) | Cachetettu 5 min — ei aja per pyynto | Merkittävä säästö |
| Cloud Run Job -suoritukset | <30s/ajo | $0/kk (ilmainen taso) |
| Cloud Run palvelu | ~1000 pyyntoa/kk | $0/kk (ilmainen taso) |

---

## Deployment ja konfiguraation päivityskäytännöt

Kaikki sovellukset (`query-api`, `write-api`) ja jobit (`rss-fetch-job` jne.) lukevat asetuksensa (kuten syötelistat ja API-avaimet) ympäristömuuttujista.

### Nopea konfiguraation päivitys (ilman konttikäännöstä)

Kun muutetaan pelkkiä konfiguraatioita (esim. lisätään tai poistetaan RSS-syöte `deploy/rss-fetch-job.env.yaml` -tiedostosta), **ei tule ajaa täyttä konttikäännöstä** (`deploy.sh`), koska koodi säilyy samana.

Ympäristömuuttujat voidaan päivittää olemassa oleville Cloud Run -resursseille sekunneissa suoraan komentoriviltä:

**Cloud Run Jobit (esim. rss-fetch-job):**
```bash
gcloud run jobs update rss-fetch-job \
  --env-vars-file deploy/rss-fetch-job.env.yaml \
  --region europe-north1 \
  --project uutisseuranta-activitystreams
```

**Cloud Run Servicet (esim. write-api):**
```bash
gcloud run services update write-api \
  --env-vars-file deploy/write-api.env.yaml \
  --region europe-north1 \
  --project uutisseuranta-activitystreams
```

Tämä säästää huomattavasti Cloud Build -käännösaikaa sekä välttää turhien Docker-kerrosten tallentamista rekisteriin.

---

## Release-prosessi ja automaatio

Kaikki backend-ympäristön muutokset, konttien kääntäminen ja käyttöönotot (deployments) viedään läpi täysin automatisoidun julkaisuprosessin (release-prosessi) kautta. Ylläpito (ops) ei tee manuaalisia muutoksia tai asetusten ylikirjoituksia GCP Consolessa.

### CI/CD-työnkulku (GitHub Actions)

1. **Testaus kehitysvaiheessa:**
   - Muutokset integroidaan `main`-haaraan pull requestien kautta. CI-prosessi ajaa automaattiset testit (`unit-test.sh`).
2. **Kasaaminen ja kontitus (Build & Package):**
   - Push `main`-haaraan laukaisee automaattisen julkaisuputken (GitHub Actions).
   - Työnkulku tunnistautuu Google Cloudiin hyödyntäen **Workload Identity Federation (WIF)** -autentikointia ilman tallennettuja pysyviä avaimia.
   - GitHub Actions rakentaa Docker-imaget (`query-api`, `write-api`, `og-scraper` jne.) ja tallentaa ne GCP Artifact Registryyn (`europe-north1-docker.pkg.dev/uutisseuranta-activitystreams/services/...`).
3. **Käyttöönotto (Deploy):**
   - Työnkulku päivittää Cloud Run -palvelut ja jobit käyttäen `gcloud run` -komentoja ja `deploy/` -kansiossa määriteltyjä ympäristökohtaisia `.env.yaml` -muuttujatiedostoja.
4. **Savutestit (Post-deploy smoke tests):**
   - Heti onnistuneen käyttöönoton jälkeen pipeline ajaa `live-smoke-test.sh` -skriptin, joka lähettää todellisia pyyntöjä uusiin Cloud Run -endpointteihin ja varmentaa niiden toimivuuden (200 OK, JSON-LD -validisuus).

## Suunnittelu- ja kehityskäytännöt

### Teknologiavalintojen ensisijaisuusperiaate
Kehityksessä noudatetaan ensisijaisuusperiaetetta riippuvuuksien minimoimiseksi ja järjestelmän pitkäikäisyyden takaamiseksi:
1. **Ensisijaisesti:** Avoimet standardit (kuten ActivityStreams 2.0, JSON Schema).
2. **Toissijaisesti:** Standardoidut, de facto standardoidut tai puhtaat "vanilla"-teknologiat (kuten standardit Python-kirjastot, natiivit Docker-kontit, BigQuery SQL).

### Activity Streams 2.0 standardinmukaisuus
Kaikessa tietomallinnuksessa ja rajapintatiedonsiirrossa käytetään W3C:n määrittelemiä Activity Streams 2.0 -kenttiä ja schemaa.
- Kanoninen spesifikaatio: [W3C Activity Streams 2.0 Core](https://www.w3.org/TR/activitystreams-core/) ja [Vocabulary](https://www.w3.org/TR/activitystreams-vocabulary/)
- Kaikki JSON-LD `@context`-tunnisteet ja objektien ominaisuudet noudattavat suoraan standardissa sovittuja nimiä ja tyyppejä (kuten `Article` uutisille ja `Like`/`Dislike` reaktioille).
- Koodissa käytetään aina standardinmukaista Activity Streams 2.0 -nimitystä rajapintakommunikaatiossa (esim. `Like`/`Dislike`), vaikka käyttöliittymän näyttöniminä (displayname) käytetään `Samaa mieltä` / `Eri mieltä` (tai `Agree`/`Disagree`).

### Avoimen datan agnostisuusperiaate

Kun käsitellään avointa dataa (kuten RSS-syötteitä tai ulkoisia datasettejä), noudatetaan datan suhteen agnostista periaatetta.
- Datan laatua, puutteita tai virheitä ei yritetä korjata tai hylätä ingestion (fetch) -vaiheessa, vaan sisäänluku pidetään mahdollisimman sallivana.
- Mahdolliset datan laatuongelmat, suodatukset ja korjaukset suoritetaan prosessin lopussa — joko lukupäässä (API) tai erillisellä rikastus-jobilla.

---

### Luonnos-Pull Requestit (Draft PR) ja alkudeploy (Skeletal Deploy)
Monimutkaiset tai uudet ominaisuudet aloitetaan viemällä kevyt runko, tyhjät API-rajapinnat ja koodikommentit (Draft PR tai alkudeploy) arvioitavaksi ennen varsinaisen toiminnallisuuden toteutusta.
- Tämä mahdollistaa arkkitehtuurin, arkkitehtuurirajojen ja koodin kommentoinnin katselmoinnin aikaisessa vaiheessa ennen laajempaa koodaamista.
- Avoimet arkkitehtuurikysymykset jätetään Pull Requestin kommenteiksi koodikontekstiin, jolloin niistä keskusteleminen ja päättäminen on luontevaa suoraan GitHubissa.

### Koodin laadun ja tietoturvan valvonta (Ruff)
Projektissa käytetään **Ruff**-työkalua automaattiseen koodin laadun, tyylin (isort, pycodestyle) ja tietoturvan (bandit) tarkistamiseen.
- Ruff on konfiguroitu juuritason `pyproject.toml`-tiedostossa.
- Se ajaa tietoturvatestit (`flake8-bandit` S-säännöt) ja tyylitestit osana CI-pipelinea (`.github/workflows/unit-tests.yml`) jokaisen Pull Requestin ja push-tapahtuman yhteydessä.
- CI-askel käyttää `--output-format=github`-lippua, jolloin Ruffin löydöt näkyvät PR:ssä GitHub-natiiveina annotaatioina suoraan muuttuneilla koodiriveillä — erillinen lokiselaus ei ole tarpeen.

### `pyproject.toml`-päätökset
- `unit-test.sh` on shell-skripti eikä Python-tiedosto — Ruff ei koskaan tarkista sitä, joten `per-file-ignores`-sääntö sille oli harhaanjohtava ja on poistettu.
- `ANN` (type annotations) on jätetty pois `select`-listasta tarkoituksella — lisätään iteraation myöhemmässä vaiheessa kun koodipohja on vakiintunut.

### Versionumerointi ja julkaisut (Release)
Projektissa noudatetaan yhtenäistä versionumerointi- ja julkaisukäytäntöä kaikkien repositorioiden välillä:
- **SemVer (Semantic Versioning):** Versionumerot noudattavat muotoa `vX.Y.Z` (esim. `v0.1.0`).
- **Tagien luominen:** Uusi julkaisu luodaan tekemällä vastaava Git-tagi (`vX.Y.Z`) ja julkaisemalla se GitHub Releases -palvelussa.
- **Julkaisuvastuu:** Jokaisesta tuotantoon viedystä merkittävästä välitavoitteesta (kuten Iteraatioiden valmistumisesta) luodaan virallinen SemVer-julkaisu.

### Yhteiset käytännöt useassa repossa
"Teknologiavalintojen ensisijaisuusperiaate", "Draft PR -käytäntö" ja "SemVer-versionumerointi" ovat identtiset kaikissa kolmessa repossa (`bq-activitystreams`, `uutisseuranta.github.io`, `patterns`). Tämä on tietoinen päätös: käytännöt toistuvat tarkoituksella koska yhteistä `organization-level CONTRIBUTING.md` -tiedostoa ei ole. Jos periaatteet muuttuvat, ne tulee päivittää kaikkiin kolmeen repoon.

---

## Liittyy

- [DESIGN_GUIDELINES.md](./DESIGN_GUIDELINES.md) — arkkitehtuuriperiaatteet
- #1 AS2-arkkitehtuuri + BigQuery-skeema
- #2 RSS-jobi
- #3 Ahjo-jobi
- #4 HRI-jobi
- #5 Firestore-vaihtoehto (BigQuery valittu)
- #6 Voikko-tagienrikastus
- #7 Kirjoituspalvelu
- #8 OG-scraper
- #9 published/updated-kenttien logiikka
- #10 Outbox-endpoint
- #11 Tykkäyslaskuri
- #12 `updated`-aikaleima
- #14 RSS-syötteet ilman pubDate
- #15 Lisensointimerkintä
- #16 Cloud Run ympäristömuuttujat
- #17 Logging ja monitoring
- #18 objects_pending-skeema
- #19 Gmail SSO → IAM-rooli -mappaus kirjoituspalvelulle

---

## Iteraatiot

### Iteraatio 3 — Scope

#### Teema 2: Käyttäjävuorovaikutus (Like / Dislike & Agree / Disagree)

| # | Tiketti | Kuvaus |
|---|---|---|
| 4 | [#33](https://github.com/uutisseuranta/bq-activitystreams/issues/33) | feat: vastaanota Like/Dislike-aktiviteetit BigQueryhin ja laske Agree+Disagree-summalaskurit per artikkeli (query-api suorittaa aggregointilaskennan BigQueryssa ja palauttaa valmiit laskurit JSON-vasteessa) |
 
 #### Teema 3: Laadunvalvonta, testaus ja vakauttaminen (QA & Refactoring)
 
 | # | Tiketti | Kuvaus |
 |---|---|---|
| 9  | [#27](https://github.com/uutisseuranta/bq-activitystreams/issues/27) | Testing: poistetaan apufunktioiden duplikaatio unit-test.sh-tiedostosta ja importataan ne suoraan `src.rss_fetch_job.main`-moduulista inline-python-testeihin. |
| 10 | [#28](https://github.com/uutisseuranta/bq-activitystreams/issues/28) | Testing: Laajenna write-api:n yksikkötestejä — kattaa happy path (Create, Like, Update) ja virhetilanteet (duplikaatti-Like 409, puuttuva actor 400, luvaton kirjoitus 403, tunnistautumaton pyyntö 401) |
 | 11 | [#29](https://github.com/uutisseuranta/bq-activitystreams/issues/29) | Testing: Lisää yksikkötestit query-api -lukurajapinnalle — suodatus actor/object_id:llä, aikarajaus, sivutus, tyhjä tulos ([]), full-table scan -esto (pakollinen aikaväli-/partitiotarkistus) |
 | 12 | [#30](https://github.com/uutisseuranta/bq-activitystreams/issues/30) | Testing: Lisää yksikkötestit og-scraperille ja og-enrichment-jobille — käytetään `unittest.mock.patch` HTTP-kutsujen mockaukseen, ja SQL MERGE -lausekkeen syntaksitestaus BigQuery Clientin `dry_run=True` -parametria hyödyntäen |
 
 Koko Iteraatio 3 scope (kaikki kolme repoa): ks. [uutisseuranta.github.io PR #32](https://github.com/uutisseuranta/uutisseuranta.github.io/pull/32)

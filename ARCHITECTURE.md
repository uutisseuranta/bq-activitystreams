# Arkkitehtuuri ja toteutussuunnitelma — bq-activitystreams

Tämä dokumentti kokoaa teknisen suunnittelun (`TECHNICAL_DESIGN.md`) ja toteutussuunnitelman (`IMPLEMENTATION_PLAN.md`) yhdeksi lähteeksi. Arkkitehtuuriperiaatteet ovat [DESIGN_GUIDELINES.md](./DESIGN_GUIDELINES.md) -tiedostossa.

Repositorio on nimetty uudelleen: `gcs-activitystreams` → `bq-activitystreams` kuvaamaan paremmin BigQuery-tietovarastovalintaa GCS:n sijaan.

---

## Sisällysluettelo

1. [Yleiskuva](#yleiskuva)
2. [Jobit ja palvelut](#jobit-ja-palvelut-yhteenvetotaulukko)
3. [Operatiivinen malli](#operatiivinen-malli-fetch-ikkuna-config-ja-retry)
4. [Autentikaatio ja valtuutus](#autentikaatio-ja-valtuutus)
5. [BigQuery-skeema](#bigquery-skeema-1)
6. [Palvelukohtaiset kuvaukset](#cloud-run-job-rss-syötteet-2)
7. [Logging ja monitoring](#logging-ja-monitoring-17)
8. [Kustannusarvio](#kustannusarvio)
9. [Deployment ja CI/CD](#deployment-ja-konfiguraation-päivityskäytännöt)
10. [Suunnittelu- ja kehityskäytännöt](#suunnittelu--ja-kehityskäytännöt)
11. [Toteutussuunnitelma ja PR-järjestys](#toteutussuunnitelma-ja-pr-järjestys)
12. [Release-tägijärjestys](#release--tägijärjestys-ja-gate-kriteerit)

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

| Job | Cron | Kellonaika (Europe/Helsinki) |
|---|---|---|
| `rss-fetch-job` | `7,22,37,52 * * * *` | Joka 15. minuutti (:07, :22, :37, :52) |
| `og-enrichment-job` | `14 * * * *` | Kerran tunnissa (:14) |
| `voikko-job` | `19 * * * *` | Kerran tunnissa (:19) |
| `likes-and-updated-job` | `21 */2 * * *` | Joka toinen tunti (:21) |

Huom: `likes-and-updated-job` korvaa aiemmat erilliset `likes-sync-job` ja `activity-updated-job` -ajastukset. Molemmat laskennat ajetaan samassa Cloud Run Job -suorituksessa — ks. [Tykkäyslaskuri ja updated-aikaleima](#tykkäyslaskuri-ja-updated-aikaleima-1112).

> [!NOTE]
> **Kesäaika:** Cloud Scheduler käyttää Europe/Helsinki -aikavyöhykettä.

---

## Jobit ja palvelut: yhteenvetotaulukko

| Job / palvelu | Tyyppi | Cron / endpoint | Kirjoittaa | Vastuu |
|---|---|---|---|---|
| rss-fetch-job | Cloud Run Job | `7,22,37,52 * * * *` | `activitystreams.objects` | Uutissyötteet (RSS) |
| og-enrichment-job | Cloud Run Job | `14 * * * *` | `activitystreams.objects` | OG-metatietojen rikastus |
| voikko-job | Cloud Run Job | `19 * * * *` | `activitystreams.objects.tags*` | Lemmatisoidut tagit (Voikko) |
| likes-and-updated-job | Cloud Run Job | `21 */2 * * *` | `activitystreams.objects.like_count, updated` | Tykkäyslaskuri + updated |
| query-api | Cloud Run Service | `GET /ap/outbox` | — | AS2 outbox, lukupään API |
| og-scraper | Cloud Run Service | `POST /ap/scrape` | `activitystreams.objects` | OG-scraper valikoiduille domaineille |
| write-api | Cloud Run Service | `POST /ap/activities` | `activitystreams_social.*` | Käyttäjäaktiviteetit (kommentit, tykkäykset) |

\* Voikko-job on `tags`-sarakkeen omistaja; muut jobit eivät koskaan kirjoita `tags`-kenttää.

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

**Kirjoitusoikeudet:** Jokainen job kirjoittaa vain omia avaimiaan. Oikeus: `roles/bigquery.dataEditor`.

#### Cold start — mitä tapahtuu kun avain puuttuu

| Tilanne | Käyttäytyminen |
|---|---|
| `last_fetched_at` puuttuu (ensimmäinen ajo) | Job hakee kiinteältä fallback-aikaikkunalta (esim. `-24h`) ja kirjoittaa arvon config-tauluun onnistuneen ajon jälkeen |
| `valtioneuvosto.rss_url` puuttuu | `rss-fetch-job` ajaa autodiscoveryn, tallentaa löydetyn URL:n ja jatkaa normaalisti |
| config-taulu on kokonaan tyhjä | Kaikki jobit käyttävät omaa fallback-ikkunaansa — data ei katkea, mutta ensimmäinen ajo saattaa hakea päällekkäistä dataa |

Config-taulun rivejä ei koskaan poisteta — vain päivitetään (`MERGE UPDATE`).

### Virheenkäsittely ja retry-strategia

`config`-taulun `last_fetched_at`-arvo päivitetään **ainoastaan kun koko ajo on suoritettu onnistuneesti**. Epäonnistunut ajo ei päivitä arvoa — seuraava ajo käyttää edellistä onnistunutta ajankohtaa fetch-ikkunana.

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

### Gmail SSO ja kirjoituspalvelu (#7, #19)

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

| Aktiviteetti | Sallittu | Ehto |
|---|---|---|
| `Create` | ✅ | `email_verified = true` |
| `Like` | ✅ | `email_verified = true` |
| `Update` | ✅ | `email_verified = true` + `actor`-IRI:n `sub` vastaa tokenin `sub` |
| `Delete` | ✅ | `email_verified = true` + `actor`-IRI:n `sub` vastaa tokenin `sub` |
| `Dislike`, `Announce`, `Undo` | ❌ | `400 Bad Request` |

#### Ympäristömuuttujat

| Muuttuja | Arvo | Kuvaus |
|---|---|---|
| `GOOGLE_CLIENT_ID` | `<OAuth2-client-id>.apps.googleusercontent.com` | Loppukäyttäjän `id_token`-validointiin |
| `CLOUD_RUN_SERVICE_URL` | `https://write-api-HASH.europe-north1.run.app` | Service-to-service -validointiin |
| `ALLOW_MOCK_AUTH` | `false` | `true` vain kehitysympäristössä |
| `ALLOWED_EMAIL_DOMAINS` | *(tyhjä = kaikki)* | Rajoittaa pääsyn tiettyihin domaineihin |

### Rate limiting (sovellustason suojaus #59)

API-rajapintojen kuormitusta ja BigQuery-kustannuksia suojataan FastAPI-sovelluksissa `slowapi`-kirjaston avulla.

#### Rajoituspolitiikka ja rajat:
- **`GET /ap/outbox` (query-api):** Dynaaminen rajoitus. Anonyymit pyynnöt on rajoitettu 60 pyyntöön minuutissa per IP-osoite. Autentikoiduille käyttäjille (OIDC JWT) raja on korotettu arvoon 120 pyyntöä minuutissa per käyttäjä (UID). Tarkistus suoritetaan sovelluksessa *ennen* BigQuery-kyselyn tekemistä, jotta estetään kyselykustannusten hallitsematon kasvu.
- **`POST /ap/activities` (write-api):** Rajoitettu 30 pyyntöön minuutissa per käyttäjä (UID).
- **`POST /ap/scrape` (og-scraper):** Rajoitettu 60 pyyntöön minuutissa per IP-osoite.
- **Ylitystilanne:** API palauttaa HTTP-statuskoodin `429 Too Many Requests` ja `Retry-After`-otsakkeen, joka ilmaisee odotusajan sekunteina.

> [!NOTE]
> `slowapi` käyttää in-memory storagea, joten rajat lasketaan per Cloud Run -instanssi (ei globaalisti). Tämä on todettu alpha-vaiheessa riittäväksi ratkaisuksi.

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
  tags_enriched   BOOL      NOT NULL OPTIONS(description='TRUE kun Voikko-job on käsitellyt rivin'),
  like_count      INT64     NOT NULL OPTIONS(description='Tykkäysmäärä, päivitetään likes-and-updated-jobilla'),
  dislike_count   INT64     NOT NULL OPTIONS(description='Dislike-määrä, päivitetään likes-and-updated-jobilla'),
  deleted         BOOL      NOT NULL OPTIONS(description='Pehmeä poisto'),
  object_json     JSON               OPTIONS(description='Koko AS2-objekti natiivina JSON-tyypinä')
)
PARTITION BY DATE(published)
CLUSTER BY source, published;
```

> **Oletusarvot:** `tags_enriched=FALSE`, `like_count=0`, `deleted=FALSE` asetetaan INSERT-lauseissa, ei DDL:ssä.

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

### `published` ja `updated` lähteittäin (#9)

| Lähde | `published` | `updated` |
|---|---|---|
| RSS-artikkeli | `<pubDate>` — pakollinen | `<atom:updated>` jos saatavilla, muuten `published` |
| OpenAhjo-päätös | `latest_decision_date` | API:n `modified` jos muuttunut |
| HRI-datasetti | `metadata_created` | `metadata_modified` |
| OG-scrapattu | `article:published_time` OG-tagista | `article:modified_time`, fallback scrape-hetki |

---

## Cloud Run Job: RSS-syötteet (#2)

Ajastus: `0 * * * *`. Lähteet:

| Lähde | RSS-URL |
|---|---|
| Helsingin Sanomat | `https://www.hs.fi/rss/tuoreimmat.xml` |
| Iltalehti | `https://www.iltalehti.fi/rss/uutiset.xml` |
| Ilta-Sanomat | `https://www.is.fi/rss/tuoreimmat.xml` |
| Kauppalehti | `https://feeds.kauppalehti.fi/rss/main` |
| MTV Uutiset | `https://www.mtvuutiset.fi/rss.xml` |
| Valtioneuvosto | autodiscovery → tallennetaan `config`-tauluun |

**Tallennuslogiikka (MERGE):**

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

---

## Cloud Run Job: OpenAhjo-päätökset (#3)

Ajastus: `0 3 * * *`. Base URL: `http://dev.hel.fi/openahjo/v1`.

| AS2-kenttä | OpenAhjo-kenttä |
|---|---|
| `id` | `register_id` → IRI-muotoon |
| `name` | `subject` |
| `summary` | `agenda_item.content` |
| `published` | `latest_decision_date` |

---

## Cloud Run Job: HRI-datasetit (#4)

Ajastus: `30 3 * * *`. Base URL: `https://hri.fi/data/api/3/action/`. Datasetit tallennetaan AS2 `Document`-objekteina, kategoriat `OrderedCollection`-objekteina.

---

## Cloud Run Job: Voikko-tagienrikastus (#6)

Ajastus: `30 * * * *`. Käsittelee erissä (100 kpl) objektit joilla `tags_enriched = FALSE`. Top-16 lemmaa tallennetaan `tags`-sarakkeeseen. Job asettaa aina `tags_enriched = TRUE` — myös tyhjän tuloksen tapauksessa.

---

## Cloud Run endpoint: OG-scraper (#8)

`POST /ap/scrape { "url": "https://..." }`. Domain-whitelist estää SSRF-hyväksikäytöt. Duplikaattipyynnöt: sama URL → sama `id` (`sha256(url)`) → MERGE hoitaa hiljaisesti.

---

## Cloud Run: kirjoituspalvelu (#7)

### Tuetut aktiviteetit

| Aktiviteetti | Kirjoitetaan |
|---|---|
| `Create` | `activities` |
| `Update` | `activities` |
| `Delete` | `activities` + `objects.deleted = TRUE` |
| `Add` | `activities` |
| `Remove` | `activities` |
| `Like` | `activities` + `likes` |
| `Dislike` | ❌ 400 Bad Request |
| `Announce` | ❌ 400 Bad Request |
| `Undo` | ❌ 400 Bad Request |

**Miksi `Undo` ei ole tuettu?** `likes`-taulussa ei ole käyttäjätunnistetta. Data on anonymisoitu — käyttäjä ei enää omista sitä eikä voi perua toimintoa. Tietoinen arkkitehtuuripäätös (ks. [DECISION_LOG.csv](./DECISION_LOG.csv)).

### Kommenttiketjun syvyysvalidointi

```
in_reply_to kohde on Article → luo kommentti (taso 1)
in_reply_to kohde on Comment → luo vastaus (taso 2)
in_reply_to kohde on Note/vastaus → 400 Bad Request
```

---

## Cloud Run: outbox-endpoint (#10)

`GET /ap/outbox?tag=asuminen&n=50`

Järjestys:

```sql
ORDER BY
  relevance     DESC,
  like_count    DESC,
  updated       DESC,
  published     DESC NULLS LAST,
  id            ASC
```

| Parametri | Kuvaus | Oletus | Maksimi |
|---|---|---|---|
| `tag` | Toistuva. Pakollinen. | — | — |
| `n` | Palautettavien määrä. Yli 500 → `400`. | 50 | 500 |

`totalItems` cachetetaan Cloud Run -muistissa 5 minuutiksi tag-kombinaatiota kohden.

---

## Tykkäyslaskuri ja updated-aikaleima (#11/#12)

`likes-and-updated-job` ajaa 15 min välein kaksi laskentaa:

**Vaihe 1: like_count-päivitys**

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

**Vaihe 2: updated-aikaleiman päivitys**

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

---

## Logging ja monitoring (#17)

- **Logitasot:** `INFO` normaaleille suorituksille, `WARNING` toipuville virheille, `ERROR` pysyville virheille.
- **Alertit:** sama job epäonnistuu N kertaa peräkkäin, HTTP 5xx ylittää rajan, latenssi kasvaa merkittävästi (p95).

### Jaettu structured logging -moduuli (#60)

Kaikki Cloud Run -palvelut ja -jobit käyttävät yhtenäistä structured logging -moduulia (`gcp_logging.py`), joka muuntaa lokit JSON-muotoisiksi.
- **Severity-kenttä:** GCP Cloud Logging tunnistaa automaattisesti logitason (`INFO`, `WARNING`, `ERROR`, `CRITICAL`) lokirivistä.
- **Trace-korrelaatio:** Jos `CLOUD_TRACE_CONTEXT`-ympäristömuuttuja on saatavilla, lokiriveihin injektoidaan `"logging.googleapis.com/trace"`-kenttä Cloud Trace -integraatiota varten.

---

## RSS-lähteiden käyttöehdot ja lisenssipolitiikka (#62)

Uutisseuranta noutaa uutisartikkeleiden otsikoita ja kuvauksia kolmansien osapuolten julkisista RSS-syötteistä. RSS-lähteiden käyttöoikeudet on tarkistettu v0.5.0-julkaisua varten:

| Uutislähde | RSS-syöte / API | Käyttöoikeusstatus | Tarkistusmetodi |
|---|---|---|---|
| **Helsingin Sanomat** | `https://www.hs.fi/rss/` | Sallittu anonyymiin hakuun | Käyttöehdot luettu (attribuutio vaaditaan) |
| **Ilta-Sanomat** | `https://www.is.fi/rss/` | Sallittu anonyymiin hakuun | Käyttöehdot luettu (attribuutio vaaditaan) |
| **Iltalehti** | `https://www.iltalehti.fi/rss/` | Sallittu anonyymiin hakuun | Käyttöehdot luettu (attribuutio vaaditaan) |
| **Kauppalehti** | `https://www.kauppalehti.fi/rss/` | Sallittu anonyymiin hakuun | Käyttöehdot luettu (attribuutio vaaditaan) |
| **MTV Uutiset** | `https://www.mtvuutiset.fi/rss/` | Sallittu anonyymiin hakuun | Käyttöehdot luettu (attribuutio vaaditaan) |
| **Yleisradio** | `https://feeds.yle.fi/uutiset/` | Sallittu (Creative Commons / avoin data) | Ylen avoimen datan käyttöehdot luettu |

#### Lisenssi- ja attribuutiovaatimukset:
- Kaikki RSS-artikkelit tallennetaan tietokantaan vain hakutoimintoja ja sosiaalisia tykkäyksiä varten.
- Alkuperäinen lähde (esim. "Helsingin Sanomat") ja linkki alkuperäiseen uutiseen näytetään aina käyttöliittymässä (attribuutio).
- Lisenssitiedot ja tarkistajat on kirjattu [LICENSES.md](./LICENSES.md) -tiedostoon.

---

## Kustannusarvio

| Resurssi | Arvio | Hinta |
|---|---|---|
| BigQuery kyselyt (100k riviä, n=500) | ~110 MB/pyynto | ~$0.0007/pyynto |
| Ilmainen 1 TB/kk -kiintiö | ~9 000 pyyntoa 100k riville | $0/kk |
| Cloud Run Job -suoritukset | <30s/ajo | $0/kk |
| Cloud Run palvelu | ~1000 pyyntoa/kk | $0/kk |

---

## Deployment ja konfiguraation päivityskäytännöt

### Nopea konfiguraation päivitys (ilman konttikäännöstä)

```bash
# Cloud Run Job
gcloud run jobs update rss-fetch-job \
  --env-vars-file deploy/rss-fetch-job.env.yaml \
  --region europe-north1 \
  --project uutisseuranta-activitystreams

# Cloud Run Service
gcloud run services update write-api \
  --env-vars-file deploy/write-api.env.yaml \
  --region europe-north1 \
  --project uutisseuranta-activitystreams
```

### CI/CD-työnkulku (GitHub Actions)

1. PR → CI ajaa `unit-test.sh` ja Terraform plan -tarkistukset.
2. Push `main`-haaraan → GitHub Actions käynnistyy.
3. WIF-autentikaatio GCP:hen (ei pysyviä avaimia).
4. **Rinnakkainen matriisijulkaisu (matrix-deploy)**:
   - Jokainen Cloud Run -palvelu (`query-api`, `write-api`, `og-scraper`) buildataan ja deployataan omana rinnakkaisena jobinaan ilman välitöntä tuotantoliikennettä (`--no-traffic`).
5. **Post-deploy tagattu healthcheck**:
   - Testataan uuden revision toiminta suoraan sen omalla URL-osoitteella käyttäen reittejä `/health` ja `/ready` (z-loppuiset polut, kuten `/healthz`, on poistettu GFE-tason 404-kaappausongelman vuoksi).
6. **Liikenteenohjaus tai rollback**:
   - Jos healthcheck läpäistään, ohjataan 100% liikenne uuteen revisioon.
   - Jos healthcheck epäonnistuu, palautetaan liikenne aiempaan toimineeseen revisioon itsenäisesti vaikuttamatta muihin palveluihin.


---

## Suunnittelu- ja kehityskäytännöt

### Teknologiavalintojen ensisijaisuusperiaate
1. Avoimet standardit (ActivityStreams 2.0, JSON Schema)
2. Standardoidut / vanilla-teknologiat (stdlib Python, natiivit Docker-kontit, BigQuery SQL)

### Activity Streams 2.0 standardinmukaisuus
Kaikessa tietomallinnuksessa käytetään W3C AS2-kenttiä. Kanoninen spesifikaatio: [W3C Activity Streams 2.0 Core](https://www.w3.org/TR/activitystreams-core/).

### Avoimen datan agnostisuusperiaate
Datan laatu, puutteet tai virheet eivät johda hylkäykseen ingestion-vaiheessa. Suodatukset ja korjaukset tapahtuvat lukupäässä tai rikastusjobissa.

### Luonnos-Pull Requestit (Draft PR)
Monimutkaiset ominaisuudet aloitetaan kevyellä rungolla (Draft PR) ennen varsinaista toteutusta.

### Koodin laadun valvonta (Ruff)
Ruff konfiguroitu `pyproject.toml`-tiedostossa. Ajaa tietoturvatestit (`flake8-bandit` S-säännöt) ja tyylitestit CI-pipelinessa `--output-format=github` -lipulla.

### `pyproject.toml`-päätökset
- `unit-test.sh` on shell-skripti — Ruff ei tarkista sitä
- `ANN` (type annotations) jätetty pois `select`-listasta — lisätään myöhemmässä iteraatiossa

### Versionumerointi (SemVer)
Versionumerot noudattavat muotoa `vX.Y.Z`. Tagit luodaan jokaisen merkittävän välitavoitteen jälkeen.

### Yhteiset käytännöt useassa repossa
"Teknologiavalintojen ensisijaisuusperiaate", "Draft PR" ja "SemVer" ovat identtiset kaikissa kolmessa repossa. Jos periaatteet muuttuvat, ne päivitetään kaikkiin kolmeen.

---

## Toteutussuunnitelma ja PR-järjestys

Kukin label vastaa yhtä PR:ää. Merkintä `→` tarkoittaa riippuvuutta.

### Label: `0-sprint` — Välitön

| Issue | Otsikko |
|---|---|
| [#21](https://github.com/uutisseuranta/bq-activitystreams/issues/21) | Testien käyttöönotto: unit-test.sh + smoke-test.yml |
| [#43](https://github.com/uutisseuranta/bq-activitystreams/issues/43) | chore: uudelleennimeä gcs-activitystreams → bq-activitystreams |

- PR `0-sprint/ci-pipeline` — issue #21
- PR `0-sprint/rename-repo-refs` — issue #43

### Label: `mvp` — Alpha-julkaisun ydinominaisuudet

| Issue | Otsikko | Riippuu |
|---|---|---|
| [#17](https://github.com/uutisseuranta/bq-activitystreams/issues/17) | Cloud Run: structured logging + liveness/readiness-probet | — |
| [#50](https://github.com/uutisseuranta/bq-activitystreams/issues/50) | chore: katselmoi ja yhtenäistä HTTP-virhekoodikäytännöt | — |
| [#32](https://github.com/uutisseuranta/bq-activitystreams/issues/32) | infra: Monivaiheinen Dockerfile libvoikko-tuella | — |
| [#31](https://github.com/uutisseuranta/bq-activitystreams/issues/31) | Arkkitehtuuri: OpenAhjo API korvaaminen uudella Ahjo REST API:lla | tehdään ennen #3 |
| [#3](https://github.com/uutisseuranta/bq-activitystreams/issues/3) | Cloud Run Job: Ahjo-päätökset AS2-objekteina BigQueryhyn | → #31 |
| [#4](https://github.com/uutisseuranta/bq-activitystreams/issues/4) | Cloud Run Job: HRI avoimen datan metatiedot CKAN API:sta | → #3 |
| [#13](https://github.com/uutisseuranta/bq-activitystreams/issues/13) | Cloud Run: Delete-aktiviteetti — kommenttien poisto | → write-api toimii |

- PR `mvp/logging-probes` — #17 + #50
- PR `mvp/dockerfile-voikko` — #32
- PR `mvp/ahjo-api-migrate` — #31
- PR `mvp/ahjo-job` — #3
- PR `mvp/hri-job` — #4
- PR `mvp/delete-activity` — #13

### Label: `gdpr` — GDPR-vaatimukset

| Issue | Otsikko | Riippuu |
|---|---|---|
| [#37](https://github.com/uutisseuranta/bq-activitystreams/issues/37) | feat: GDPR — käyttäjän sosiaalisen datan poisto ja anonymisointi | → uutisseuranta.github.io #49 + #50 |

- PR `gdpr/user-data-deletion` — #37

### Label: `hardened` — Tietoturvakovennukset

| Issue | Otsikko |
|---|---|
| [#59](https://github.com/uutisseuranta/bq-activitystreams/issues/59) | sec: rate limiting — /ap/outbox + /ap/activities + /ap/scrape |
| [#41](https://github.com/uutisseuranta/bq-activitystreams/issues/41) | sec: DevSecOps-pipelinejen suunnittelu ja käyttöönotto |
| [#45](https://github.com/uutisseuranta/bq-activitystreams/issues/45) | sec: lisää Dependabot Python-riippuvuuksille |

- PR `hardened/rate-limiting` — #59
- PR `hardened/devsecops` — #41 + #45

### Label: `AS2` — ActivityStreams 2.0 -yhteensopivuus

| Issue | Otsikko | Riippuu |
|---|---|---|
| [#35](https://github.com/uutisseuranta/bq-activitystreams/issues/35) | feat: Content Negotiation | — |
| [#48](https://github.com/uutisseuranta/bq-activitystreams/issues/48) | feat: BigQuery-migraatio Dislike-aktiviteeteille | tehdään ennen #33 |
| [#33](https://github.com/uutisseuranta/bq-activitystreams/issues/33) | feat: vastaanota Like/Dislike | → #48 |
| [#54](https://github.com/uutisseuranta/bq-activitystreams/issues/54) | Meta: Cross-repo AS2 contract | koordinoi patterns + frontend |
| [#53](https://github.com/uutisseuranta/bq-activitystreams/issues/53) | Testing: AS2 cross-repo compatibility test harness | → #54 |

- PR `as2/content-negotiation` — #35
- PR `as2/dislike-migration` — #48
- PR `as2/like-dislike-handlers` — #33
- PR `as2/contract-meta` — #54
- PR `as2/contract-tests` — #53

### Label: `testing` — Testikattavuus

| Issue | Otsikko | Tehdään yhdessä |
|---|---|---|
| [#28](https://github.com/uutisseuranta/bq-activitystreams/issues/28) | Testing: laajenna write-api:n testejä | `as2/like-dislike-handlers` + `mvp/delete-activity` |
| [#29](https://github.com/uutisseuranta/bq-activitystreams/issues/29) | Testing: yksikkötestit query-api:lle | `as2/content-negotiation` |
| [#27](https://github.com/uutisseuranta/bq-activitystreams/issues/27) | Testing: poista koodiduplikaatio unit-test.sh:sta | `0-sprint/ci-pipeline` |
| [#30](https://github.com/uutisseuranta/bq-activitystreams/issues/30) | Testing: testit og-scraperille ja og-enrichment-jobille | erillinen PR |

### Label: `enhancement` — Post-alpha

| Issue | Otsikko | Riippuu |
|---|---|---|
| [#56](https://github.com/uutisseuranta/bq-activitystreams/issues/56) | perf: BigQuery-kuluoptimointi — materialisoitujen näkymien hyödyntäminen | — |
| [#55](https://github.com/uutisseuranta/bq-activitystreams/issues/55) | feat: BigQuery-käyttäjätilastorajapinta | → #48 |
| [#36](https://github.com/uutisseuranta/bq-activitystreams/issues/36) | feat: /ap/users/{id}/stats | → #33 |
| [#26](https://github.com/uutisseuranta/bq-activitystreams/issues/26) | feat: Wayback Machine SPN2 | → write-api toimii |
| [#24](https://github.com/uutisseuranta/bq-activitystreams/issues/24) | feat: OG-rikastus RSS-artikkeleille | — |
| [#18](https://github.com/uutisseuranta/bq-activitystreams/issues/18) | feat: objects_pending-taulu skeema + rikastusjob | → #24 |

### Label: `documentation`

| Issue | Otsikko |
|---|---|
| [#52](https://github.com/uutisseuranta/bq-activitystreams/issues/52) | Meta: Jira–GitHub-integraation päätökset |
| [#46](https://github.com/uutisseuranta/bq-activitystreams/issues/46) | chore: populoi LICENSES.md |
| [#15](https://github.com/uutisseuranta/bq-activitystreams/issues/15) | Lisensointimerkintä: avoimen datan käyttöehdot API-vastauksiin |

### PR-järjestys (koko putki)

```
0-sprint/ci-pipeline
0-sprint/rename-repo-refs

mvp/logging-probes
mvp/dockerfile-voikko
mvp/ahjo-api-migrate
  → mvp/ahjo-job
      → mvp/hri-job
mvp/delete-activity

gdpr/user-data-deletion     (rinnakkain mvp-työn kanssa)

as2/content-negotiation     (rinnakkain mvp-työn kanssa)
as2/dislike-migration
  → as2/like-dislike-handlers
as2/contract-meta
  → as2/contract-tests

hardened/rate-limiting      (mvp valmis ensin)
hardened/devsecops          (mvp valmis ensin)

perf/bq-materialized-views  (alpha stabiili ensin)
feat/user-stats             (alpha + #33 valmis)
feat/wayback-archive
feat/og-enrichment
feat/objects-pending

docs/*                      (missä vaiheessa tahansa)
```

### Puuttuvat issuet — avattava ennen toteutusta

| Aihe | Label | Mihin PR |
|---|---|---|
| Structured logging jaettu moduuli kaikille jobeille | `mvp` | `mvp/logging-probes` |
| WCAG AA -vaatimukset API-virheviestien ihmisluettavuudelle | `hardened` | `hardened/rate-limiting` |
| Lisenssitarkistus: RSS-lähteiden käyttöehdot | `documentation` | `docs/licenses` |

---

## Release — tägijärjestys ja gate-kriteerit

Tägiketju: **patterns → bq-activitystreams → uutisseuranta.github.io**.

### v0.1.0 — "CI toimii"

**Gate:** kaikki `0-sprint`-issuet kiinni, CI läpimäissä `main`-haarassa.

```bash
git tag -a v0.1.0 -m "Release v0.1.0: 0-sprint valmis, CI toimii"
git push origin v0.1.0
```

### v0.5.0 — "MVP alpha"

**Gate:** kaikki `mvp`- ja `gdpr`-issuet kiinni, patterns `v0.3.0` tagittu.

```bash
gh issue list --label mvp --state open --repo uutisseuranta/bq-activitystreams
gh issue list --label gdpr --state open --repo uutisseuranta/bq-activitystreams
gh release view v0.3.0 --repo uutisseuranta/patterns

git tag -a v0.5.0 -m "Release v0.5.0: MVP alpha — /ap/outbox toimii, GDPR kunnossa"
git push origin v0.5.0
```

### v1.0.0 — "Production hardened"

**Gate:** kaikki `hardened`- ja `testing`-issuet kiinni.

```bash
gh issue list --label hardened --state open --repo uutisseuranta/bq-activitystreams

git tag -a v1.0.0 -m "Release v1.0.0: tuotantovalmis — rate limiting, DevSecOps, AS2 contract"
git push origin v1.0.0
```

### Terraform-infrastruktuuri

Repolabelit ja branch protection hallitaan Terraformilla:
[`terraform/github/backend/labels.tf`](../terraform/github/backend/labels.tf)

```hcl
resource "github_branch_protection" "main" {
  repository_id = github_repository.backend.node_id
  pattern       = "main"

  required_status_checks {
    strict   = true
    contexts = ["ci / test"]
  }

  required_pull_request_reviews {
    required_approving_review_count = 1
  }
}
```

```bash
export GITHUB_TOKEN="ghp_..."
cd terraform/github
terraform init && terraform plan && terraform apply
```

### AS2-skeemaversio per release

| Release | AS2-skeemaversio | Muutokset |
|---|---|---|
| `v0.5.0` | schema-v1 | Article, Note, Collection, Hashtag peruskentät, `/ap/outbox` |
| `v1.0.0` | schema-v2 | Content negotiation, Like/Dislike-togglet, `_uutisseuranta:*`-laajennukset |

---

## Liittyy

- [DESIGN_GUIDELINES.md](./DESIGN_GUIDELINES.md) — arkkitehtuuriperiaatteet
- [DECISION_LOG.csv](./DECISION_LOG.csv) — arkkitehtuuripäätösten loki
- #1 AS2-arkkitehtuuri + BigQuery-skeema
- #2 RSS-jobi · #3 Ahjo-jobi · #4 HRI-jobi · #6 Voikko · #7 Kirjoituspalvelu · #8 OG-scraper
- #9 published/updated · #10 Outbox · #11/#12 Tykkäyslaskuri · #14 pubDate-puutteet
- #15 Lisensointimerkintä · #16 Cloud Run env · #17 Logging · #18 objects_pending · #19 Gmail SSO

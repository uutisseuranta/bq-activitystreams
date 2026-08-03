# Arkkitehtuuri ja toteutussuunnitelma — bq-activitystreams

Tämä dokumentti kokoaa teknisen suunnittelun (`TECHNICAL_DESIGN.md`) ja toteutussuunnitelman yhdeksi lähteeksi. Arkkitehtuuriperiaatteet ovat [DESIGN_GUIDELINES.md](./DESIGN_GUIDELINES.md) -tiedostossa.

Repositorio on nimetty uudelleen: `gcs-activitystreams` → `bq-activitystreams` kuvaamaan paremmin BigQuery-tietovarastovalintaa. GCS toimii bronze-tierin raakadatavälimuistina, ei päätallennuspaikkana.

---

## Sisällysluettelo

1. [Yleiskuva](#yleiskuva)
2. [Jobit ja palvelut](#jobit-ja-palvelut-yhteenvetotaulukko)
3. [Operatiivinen malli](#operatiivinen-malli-fetch-ikkuna-config-ja-retry)
4. [Autentikaatio ja valtuutus](#autentikaatio-ja-valtuutus)
5. [BigQuery-skeema](#bigquery-skeema-1)
6. [Bronze tier](#bronze-tier)
7. [Palvelukohtaiset kuvaukset](#cloud-run-job-rss-syötteet-2-165)
8. [Logging ja monitoring](#logging-ja-monitoring-17)
9. [Kustannusarvio](#kustannusarvio)
10. [Deployment ja CI/CD](#deployment-ja-konfiguraation-päivityskäytännöt)
11. [Suunnittelu- ja kehityskäytännöt](#suunnittelu--ja-kehityskäytännöt)
12. [Toteutussuunnitelma ja PR-järjestys](#toteutussuunnitelma-ja-pr-järjestys)
13. [Release-tägijärjestys](#release--tägijärjestys-ja-gate-kriteerit)

---

## Yleiskuva

```
https://activitystreams.uutisseuranta.net/
  └── GET /ap/outbox?tag=asuminen&n=50  ← Cloud Run (query-api) → BigQuery
  └── POST /ap/activities               ← Cloud Run (write-api) → BigQuery

Kirjoittajat per taulu:

  activitystreams.objects          ← og-enrichment-job (rikastetut artikkelit, päätökset, datasetit),
                                       kirjoituspalvelu #7 (kommentit)
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
| `ahjo-fetch-job` | `0 3 * * *` | Päivittäin 06:00 (Europe/Helsinki) |
| `hri-fetch-job` | `30 3 * * *` | Päivittäin 06:30 (Europe/Helsinki) |
| `lausuntopalvelu-fetch-job` | `0 4 * * *` | Päivittäin 07:00 (Europe/Helsinki) |

Huom: `likes-and-updated-job` korvaa aiemmat erilliset `likes-sync-job` ja `activity-updated-job` -ajastukset.

Ahjo-, HRI- ja Lausuntopalvelu-jobit ovat toisistaan riippumattomia. Ajastusjärjestys on operatiivinen eikä semanttinen vaatimus. Cron-offset tarkistetaan ja tarvittaessa säädetään kun kaikki kolme jobia on deployattu ja todelliset ajoajat mitattu.

> [!NOTE]
> **Kesäaika:** Cloud Scheduler käyttää Europe/Helsinki -aikavyöhykettä.

---

## Jobit ja palvelut: yhteenvetotaulukko

| Job / palvelu | Tyyppi | Cron / endpoint | Kirjoittaa | Vastuu |
|---|---|---|---|---|
| `rss-fetch-job` | Cloud Run Job | `7,22,37,52 * * * *` | `activitystreams.objects_pending` + GCS bronze | Uutissyötteet (RSS/Atom) |
| `ahjo-fetch-job` | Cloud Run Job | `0 3 * * *` | `activitystreams.objects_pending` + GCS bronze | Helsingin päätökset — Ahjo REST API |
| `hri-fetch-job` | Cloud Run Job | `30 3 * * *` | `activitystreams.objects_pending` + GCS bronze | HRI CKAN datasetit ja kategoriat |
| `lausuntopalvelu-fetch-job` | Cloud Run Job | `0 4 * * *` | `activitystreams.objects_pending` + GCS bronze | Lausuntopalvelun lausuntopyynnöt |
| `og-enrichment-job` | Cloud Run Job | `14 * * * *` | `activitystreams.objects` | OG-metatietojen rikastus ja siirto pending-taulusta |
| `voikko-job` | Cloud Run Job | `19 * * * *` | `activitystreams.objects.tags*` | Lemmatisoidut tagit (Voikko) |
| `likes-and-updated-job` | Cloud Run Job | `21 */2 * * *` | `activitystreams.objects.like_count, updated` | Tykkäyslaskuri + updated |
| `query-api` | Cloud Run Service | `GET /ap/outbox` | — | AS2 outbox, lukupään API |
| `write-api` | Cloud Run Service | `POST /ap/activities` | `activitystreams_social.*` | Käyttäjäaktiviteetit (kommentit, tykkäykset) + `objects_pending` (käyttäjän linkit) |

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
| `lausuntopalvelu.last_fetched_at` | ISO 8601 timestamp | `lausuntopalvelu-fetch-job` | Onnistuneen ajon lopussa |
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
- **Ylitystilanne:** API palauttaa HTTP-statuskoodin `429 Too Many Requests` ja `Retry-After`-otsakkeen, joka ilmaisee odotusajan sekunteina.

> [!NOTE]
> `slowapi` käyttää in-memory storagea, joten rajat lasketaan per Cloud Run -instanssi (ei globaalisti). Tämä on todettu alpha-vaiheessa riittäväksi ratkaisuksi.

---

## BigQuery-skeema (#1)

### `activitystreams.objects` — artikkelit, päätökset, datasetit

```sql
CREATE TABLE activitystreams.objects (
  id              STRING    NOT NULL OPTIONS(description='AS2 id – domain-pohjainen IRI, primääriavain'),
  source          STRING    NOT NULL OPTIONS(description='Lähde: rss | ahjo | hri | lausuntopalvelu | scraped | user'),
  published       TIMESTAMP NOT NULL OPTIONS(description='AS2 published – pakollinen, taulu on partitionoitu tämän mukaan'),
  updated         TIMESTAMP          OPTIONS(description='AS2 updated – päivittyy käyttäjäaktiivisuudesta (#12)'),
  tags            ARRAY<STRING>      OPTIONS(description='Lemmatisoidut tagit (Voikko #6)'),
  tags_enriched   BOOL      NOT NULL OPTIONS(description='TRUE kun Voikko-job on käsitellyt rivin'),
  like_count      INT64     NOT NULL OPTIONS(description='Tykkäysmäärä, päivitetään likes-and-updated-jobilla'),
  dislike_count   INT64     NOT NULL OPTIONS(descr
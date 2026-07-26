# Design Guidelines: bq-activitystreams

Nämä periaatteet ohjaavat kaikkia arkkitehtuuripäätöksiä. Viittaa tähän dokumenttiin tikettejä kirjoittaessasi.

---

## Zero Trust — always check, always verify

Kaikki kirjoitusoperaatiot vaativat autentikaation riippumatta siitä mistä pyyntö tulee. Ei luoteta verkkorajoihin, IP-osoitteisiin eikä aiempaan sessioon. Jokainen pyyntö validoidaan erikseen.

| Endpoint | Autentikaatio | Perustelu |
|---|---|---|
| `GET /ap/outbox` | Ei — julkinen avoin data | Lukuoperaatio, ei kirjoitusta |
| `POST /ap/scrape` | ✅ Google `id_token` pakollinen | Kirjoittaa `activitystreams.objects`-tauluun |
| `POST /ap/activities` | ✅ Google `id_token` pakollinen | Kirjoittaa `activitystreams_social`-datasettiin |

Backend validoi tokenin **aina** — se ei luota siihen että UI on jo tarkistanut kirjautumisen. UI:n vastuulla on näyttää kirjautumiskehotus ennen pyyntöä, mutta backend hylkää pyynnön ilman kelvollista tokenia riippumatta UI:n tilasta.

```
401 Unauthorized  — Authorization-otsake puuttuu tai token ei kelpaa
403 Forbidden     — token kelvollinen mutta ei oikeutta operaatioon
```

---

## Asiat riitelevät, ei ihmiset

Tässä palvelussa sisältö on toimijana, ei henkilö.

AS2-spesifikaatio tukee `actor`-kentässä `Person`-tyyppiä, mutta tässä projektissa actoreita ei käytetä käyttäjäidentiteetteinä. Kaikki `actor`-arvot ovat joko:

- `Organization` — julkaisija (Helsingin Sanomat, Helsingin kaupunki, HRI)
- `Service` — automaattipalvelu (Voikko-enrichment, RSS-jobi, OG-scraper)

**Käyttäjä voi kopioida sisältöä ja julkaista sen omanaan** `Create`-aktiviteetilla omalla actorillaan. Käyttäjä tunnistetaan Gmail SSO:lla — actor-URI johdetaan Google-tilin tunnistuksesta. Palvelu itse ei mallinna käyttäjäidentiteettejä eikä sosiaaligraafeja. Ei `followers`, ei `following`, ei `Person`-actoreita backendin omissa objekteissa.

**ActivityPub-federointi**: Tämä on tarkoituksella read-heavy-arkkitehtuuri. Server-to-server-protokollaa (Mastodon-yhteensopivuus) ei tavoitella. `outbox` on julkinen luettava syöte, ei federoitu inbox. WebFinger-endpointia ei toteuteta. Jos Actor-resursseja toteutetaan myöhemmin, se on erillinen päätös ja erillinen tiketti.

---

## Reaktiot: Like, Dislike ja Undo

Kun asiat riitelevät eikä ihmiset, reaktiot ovat sisällön kiinnostavuuden mittareita — eivät sosiaalisen hyväksynnän tai hylkimisen signaaleja. `Like` (Agree) ja `Dislike` (Disagree) muodostavat Agree/Disagree-togglen: ne mittaavat suhdetta *sisältöön*, eivät muihin ihmisiin.

| Aktiviteetti | Tila | Perustelu |
|---|---|---|
| `Like` | ✅ Toteutetaan | Käyttäjä merkitsee artikkelin/asian kiinnostavaksi (Agree) |
| `Dislike` | ✅ Toteutetaan | Käyttäjä merkitsee artikkelin/asian kiistanalaiseksi (Disagree) — vastine Likelle, ei henkilökohtainen hyökkäys |
| `Undo Like` / toggle | ✅ Toteutetaan mekaniikalla | Käyttäjä voi vaihtaa reaktionsa: vanha poistetaan kannasta, uusi kirjataan. `Undo`-aktiviteettia **ei kirjata lokiin** — anonymisoinnin säilyttämiseksi (ks. alla) |
| `Announce` | ❌ Ei toteuteta | Käyttäjä julkaisee sisältöä `Create`-aktiviteetilla, ei uudelleenjakamisella |
| `Update` | ✅ Toteutetaan | Client-sovellus kirjoittaa `Update`-aktiviteetin muokatusta sisällöstä |
| `Delete` | ✅ Toteutetaan | Käyttäjä voi poistaa oman kommenttinsa tai artikkelinsa |

### Toggle-mekaniikka ja anonymisointi (Päätös DECISION_LOG L-009 / AS2_CONTRACT.md §4)

Kun käyttäjä vaihtaa `Like` → `Dislike` tai `Dislike` → `Like`:

1. Vanha reaktio poistetaan `activitystreams_social.likes`-taulusta
2. Uusi reaktio kirjataan tilalle
3. AS2 `Undo`-aktiviteettia **ei luoda** append-only-lokiin

Perustelu: `Undo`-aktiviteetin kirjaaminen paljastaisi reaktiohistorian, mikä on ristiriidassa anonymisoinnin kanssa. Historiallinen tieto siitä, kuka on milloin vaihtanut reaktionsa, ei kuulu avoimeen dataan.

### `_uutisseuranta:reactionCount`

API palauttaa jokaisessa Article-objektissa yhteenlasketun reaktiomäärän (`likes.totalItems + dislikes.totalItems`) projektikohtaisessa `_uutisseuranta:reactionCount`-kentässä. Nimitys on neutraali: se ei implikoi yksimielisyyttä.

```json
{
  "likes":   { "type": "Collection", "totalItems": 42 },
  "dislikes": { "type": "Collection", "totalItems": 7 },
  "_uutisseuranta:reactionCount": 49
}
```

---

## Delete ei poista historiaa

`Delete` merkitsee objektin poistetuksi — se ei koskaan poista tietoja tietokannasta. Tämä on tarkoituksellinen päätös:

- **Kommentti jolla on vastauksia** näytetään paikkamerkkinä `[kommentti poistettu]`. Vastausten konteksti säilyy, ketjun rakenne pysyy ehjänä.
- **Kommentti ilman vastauksia** voidaan piilottaa kokonaan näkymästä, mutta rivi pysyy `activities`-taulussa `deleted=TRUE`-merkinnällä.
- **Artikkeli** merkitään `deleted=TRUE` `objects`-taulussa. Se ei enää palaudu outbox-hauissa (`WHERE deleted = FALSE`).
- Palvelin kirjoittaa `Delete`-aktiviteetin `activities`-tauluun append-only-lokin mukaisesti. Materialized view johtaa nykyisen tilan eventtilokin pohjalta.

Historian säilyttäminen mahdollistaa moderoinnin jälkikäteen ja pitää ketjurakenteen luettavana.

---

## `published` = alkuperäinen julkaisuhetki julkaisijan palvelussa

AS2-speksin mukaan `published` on objektin luontihetki ja `updated` on muokkaushetki. `published` ei koskaan muutu objektin päivityksissä — se on historiallinen tosiasia.

| Lähde | `published` | `updated` |
|---|---|---|
| RSS-artikkeli | `<pubDate>` tai `<dc:date>` syötteessä | `<atom:updated>` jos saatavilla |
| OpenAhjo-päätös | Päätöksen alkuperäinen julkaisupäivä julkaisijan sivuilla | `metadata_modified` jos muuttunut |
| HRI-datasetti | `metadata_created` CKAN-vastauksessa | `metadata_modified` |
| OG-scrapattu sivu | `article:published_time` OG-tagista (prioriteetti: JSON-LD `datePublished` → OG `article:published_time` → HTTP `Last-Modified`) | `article:modified_time` |
| Fallback (ei metatietoa) | `null` — merkitään epätarkkuudeksi lokiin | Scrape-hetki |

> [!NOTE]
> Fallback-tapauksessa `published` jätetään `null`:ksi koska julkaisuhetkeä ei tiedetä. `updated` asetetaan scrape-hetkeen, jolloin objekti näkyy haussa mutta epätarkkuus on jäljitettävissä logista. `published = null` -objektit järjestetään relevanssijärjestyksen loppuun (`published DESC NULLS LAST`).

---

## Ei Actor-resursseja tässä backendissä

ActivityPub edellyttää, että `actor`-URL palauttaa täydellisen Actor-objektin (`inbox`, `outbox`, `followers`, `following`). Tässä projektissa `actor`-kentät viittaavat organisaatioihin ja palveluihin, joilla ei ole omaa AS2-endpointia. WebFinger-endpointia ei toteuteta. Fediverse-yhteensopivuus ei ole tavoite.

---

## Kommenttiketjun syvyysrajoitus

Sallittu syvyys on tasan **kaksi tasoa**:

```
Article                        (taso 0 – thread_root)
  └── Comment                  (taso 1 – in_reply_to = Article)
       └── Reply               (taso 2 – in_reply_to = Comment)
```

**Vastaukseen ei voi vastata.** Kirjoituspalvelu hylkää yrityksen `400 Bad Request` -vastauksella. Tämä rajoitus on tarkoituksellinen: syvät ketjut hajottavat kontekstin ja vaikeuttavat lukemista. Kaksi tasoa riittää asian käsittelyyn — lisää tasoja ei tarvita, koska asiat eivät tarvitse omaa alaketjuaan.

`thread_root` täydennetään aina automaattisesti palvelimella — client ei koskaan aseta sitä itse.

---

## Tykkäyslaskuri: `likes:N`-tagi

Tykkäysmäärä julkaistaan objektin `tags`-taulukossa tagina muotoa `likes:N`. Tagi on **anonyymi** — se kertoo vain lukumäärän, ei kuka on tykkännyt. Laskentajob päivittää tagin säännöllisesti sosiaalisesta BigQuerystä avoimen datan BigQueryyn.

- **Duplikaattisuojaus**: Tykkäys tapahtuu Gmail SSO -tunnistuksen kautta. Sama Google-tili voi kirjata `Like`- tai `Dislike`-aktiviteetin objektille vain kerran — backend hylkää duplikaatin.
- **Bottisuojaus**: Cloudflare suojaa endpointit automatisoitujen pyyntöjen volyymilta. Vahvistamaton pyyntö ei pääse kirjoittamaan reaktioaktiviteetteja.
- **GDPR**: Reaktiolaskurit ovat anonyymejä — avoimeen dataan siirtyy pelkkä lukumäärä, ei tunnistetta. Koska tieto ei ole henkilöön yhdistettävissä, GDPR:n tiedon poisto-oikeus ei koske sitä.
- Tieto siitä kuka on reagoinut ei koskaan siirry avoimeen dataan.
- Yksi `likes:`-tagi per objekti — päivitys korvaa edellisen.

```
tags: ["asuminen", "helsinki", "päätös", "likes:42"]
```

---

## Ei `source`-parametria

Client ei päätä mistä lähteestä dataa haetaan — vain tagit ratkaisevat. `source`-kenttää ei ole dokumenttirakenteessa eikä query-parametreissa.

```
✓ GET /ap/outbox?tag=asuminen&tag=helsinki
✗ GET /ap/outbox?tag=asuminen&source=ahjo   ← 400 Bad Request
```

Lähde (RSS, Ahjo, HRI, OG-scrape) on sisäinen toteutusyksityiskohta, ei julkinen filtteri.

---

## Outbox-sivutus: tagirelevanssi, ei kursoreja

### Järjestys

Outbox palauttaa objektit **tagirelevanssijärjestyksessä**, ei aikajärjestyksessä. Relevanssi on yksinkertainen osumien laskenta: kuinka monta clientin pyytämistä tageista löytyy objektin `tags`-kentästä. Tasatilanteessa järjestetään `like_count DESC, updated DESC, published DESC NULLS LAST, id ASC`.

Aikajärjestystä ei käytetä pääjärjestyksenä, koska saman aiheen uutiset eri lähteistä ovat tasavertaisia riippumatta siitä, kuka julkaisi ensin.

### Malli

Client pyytää aina alusta `n` kappaletta. Ei kursoreja, ei sivunumeroita. Client huolehtii itse duplikaattisuodatuksesta `id`-kentän perusteella — jos client haluaa enemmän tuloksia, se pyytää suuremmalla `n`:llä.

```
GET /ap/outbox?tag=asuminen&n=5    → top-5
GET /ap/outbox?tag=asuminen&n=50   → top-50 (sisältää edellisen 5, client suodattaa)
GET /ap/outbox?tag=asuminen&n=500  → top-500 (maksimi)
```

`OrderedCollectionPage`-tasoa ei tarvita — pelkkä `OrderedCollection` riittää (Päätös L-009).

### n=500 on katto — hinta ja suorituskyky

Maksimi `n=500` on valittu kahdesta syystä:

1. **Hinta**: BigQuery laskuttaa skannatun datan mukaan. 500 riviä 100 000 rivin taulusta skannaa ~110 MB (~$0.0007/pyyntö). Suurempi `n` kasvattaa kustannuksia lineaarisesti. Ilmainen 1 TB/kk -kiintiö kattaa ~9 000 pyyntöä — rajoite pitää kustannukset ennustettavina.
2. **Suorituskyky**: BigQuery-kysely 500+ rivillä alkaa hidastua client-puolella JSON-deserialisoinnissa ja DOM-renderöinnissä. 500 tulosta yhdellä hakusanalla on enemmän kuin kukaan ehtii lukea — jos tuloksia on enemmän kuin 500, oikea ratkaisu on tarkentaa hakua, ei kasvattaa `n`:ää.

Kun `totalItems > 500`, UI näyttää tagipilven josta käyttäjä voi tarkentaa hakua. Tämä on parempi UX kuin sivuttaa läpi tuhansia tuloksia.

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

Ei `next`, ei `prev`, ei `cursor`, ei `first`. `totalItems` kertoo clientille paljonko pyytää maksimissaan.

### Query-parametrit

| Parametri | Kuvaus |
|---|---|
| `tag` | Yksi tai useampi tagi (toistuva). Pakollinen — `400` jos puuttuu. |
| `n` | Haettavien objektien määrä. Oletus 50, maksimi 500. |

`source`-parametria ei ole: client ei päätä mistä lähteestä dataa haetaan, vain mitä tageja seurataan.

---

## Liittyy

- Kaikki muut tiketit — nämä periaatteet ohjaavat kaikkia arkkitehtuuripäätöksiä
- #10 Outbox-endpoint
- #11 Tykkäyslaskuri (`likes:N`-tagi)
- #13 Delete-toiminto
- #19 Gmail SSO → id_token-validointi
- #23 OG-scraper
- #33 Like/Dislike-togglet ja `_uutisseuranta:reactionCount`
- [AS2_CONTRACT.md](./AS2_CONTRACT.md) — rajapintasopimus, §4 hallitut poikkeamat
- [DECISION_LOG.csv](./DECISION_LOG.csv) — L-009 (paginaatio), L-015 (reactionCount-uudelleennimeäminen)

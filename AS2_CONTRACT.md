# AS2 Contract (v0.5.0)

Tämä dokumentti määrittelee `uutisseuranta`-järjestelmän käyttämän rajapintasopimuksen, joka pohjautuu W3C ActivityStreams 2.0 (AS2) -spesifikaatioon.

## 1. Yhteensopivuus ja ydinontologia
Kaikki rajapintavastaukset noudattavat [W3C ActivityStreams 2.0](https://www.w3.org/TR/activitystreams-core/) -spesifikaatiota.
API-vastausten Content-Type on oltava `application/activity+json; charset=utf-8`.

## 2. Nimiavaruuslaajennukset (Custom Extensions)
Järjestelmä käyttää `https://uutisseuranta.net/ns#` -kontekstia projektikohtaisille laajennuksille.
Kontekstiimäärittely `@context` on oltava rajapinnan palauttamissa Article-objekteissa muodossa:
```json
"@context": [
  "https://www.w3.org/ns/activitystreams",
  {
    "_uutisseuranta": "https://uutisseuranta.net/ns#",
    "dislikes": "_uutisseuranta:dislikes"
  }
]
```

### Tuetut laajennuskentät:
1. **`dislikes`**:
   - Tyyppi: `Collection`
   - Kuvaus: Artikkelin saamat dislike-reaktiot (vastine likes-kentälle). Aliaksen `"dislikes": "_uutisseuranta:dislikes"` ansiosta kenttä on JSON-LD 1.1 -yhteensopiva ja ulkopuolisten prosessorien luettavissa.
   - Esimerkki: `{"type": "Collection", "totalItems": 5}`
2. **`_uutisseuranta:reactionCount`**:
   - Tyyppi: `Integer`
   - Kuvaus: Yhteenlaskettu tykkäys- ja dislike-määrä (`likes.totalItems + dislikes.totalItems`). Neutraali nimitys: sisältää sekä Agree (Like) että Disagree (Dislike) -reaktiot.

## 3. Minimikenttäjoukot (v0.5.0)

### Article (Uutisartikkeli)
- **Pakolliset kentät:**
  * `@context`
  * `type` (Article)
  * `id`: Pysyvä persistentti IRI muodossa `https://uutisseuranta.net/ap/objects/{hash}` (W3C DWBP BP7 -suositus)
  * `name`
  * `url` (Alkuperäinen uutisen URL toimituksen sivulla)
  * `published`
- **Valinnaiset kentät:** `summary`, `content`, `updated`, `attributedTo`, `likes`, `dislikes`, `_uutisseuranta:reactionCount`, `tag` (morfologiset tagit `Hashtag`-objekteina)

### Note (Kommentti)
- **Pakolliset kentät:** `@context`, `type` (Note), `id`, `content`, `published`, `attributedTo`, `inReplyTo`
- **Valinnaiset kentät:** `updated`, `thread_root`

### OrderedCollection (Outbox)
- **Pakolliset kentät:** `@context`, `type` (OrderedCollection), `id`, `totalItems`, `orderedItems`

## 4. Hallitut poikkeamat standardista
- **Undo Like -poikkeama:** Järjestelmä toteuttaa toggle-reaktiologiikan (Like ↔ Dislike) poistamalla vanhan reaktion tietokannasta ja lisäämällä uuden. Tästä ei luoda erillistä AS2 `Undo`-aktiviteettia lokiin historiatiedon anonymisoinnin säilyttämiseksi.
- **OrderedCollectionPage -poikkeama (Päätös L-009):** `GET /ap/outbox` käyttää omaa hakumallia eikä AS2 `OrderedCollectionPage` + `next`/`prev` -linkkikentтиä. Tämä on tehty yksinkertaisuuden ja BigQuery-pohjaisen suorituskyvyn vuoksi, koska järjestelmä ei federoidu ulkoisiin AP-palvelimiin.

  **Miten paginaatio toimii käytännössä:** Client pyytää artikkeleita `?tag=` + `?n=`-parametreilla ilman kursoreja tai sivunumeroita. Vastauksen `totalItems` kertoo koko kokoelman koon, mutta `orderedItems`-taulukossa palautetaan vain pyydetty määrä (n kpl). AS2-yhteensopiva client, joka odottaa `next`/`prev`-linkkejä, ei voi sivuttaa kokoelmaa näiden kautta — paginaatio tapahtuu kutsumalla API:a uudelleen suuremmalla `n`-arvolla:

  ```
  GET /ap/outbox?tag=politiikka&n=5    → top-5 (pikaesäkatselyun)
  GET /ap/outbox?tag=politiikka&n=50   → top-50 (scroll-to-load)
  GET /ap/outbox?tag=politiikka&n=500  → top-500 (maksimierä)
  ```

  Client suodattaa duplikaatit selaimen muistissa `id`-kentän perusteella. Tämä malli sopii BigQuery-pohjaiseen relevanssilajitteluun eikä vaadi OFFSET-lauseita. `totalItems` on yhteensopiva AS2 `Collection`-tyypin kanssa mutta ei `OrderedCollectionPage`-käytännön kanssa — poikkeama on täysin perusteltu, koska järjestelmä ei federoidu ulkopuolisiin ActivityPub-palvelimiin.

## 5. Automaattinen skeemavalidointi (CI/CD)
Rajapintasopimuksen koneluettavat ja viralliset JSON Schema -määritelmät sijaitsevat repositorion juuressa:
- [article.schema.json](file:///Users/jaakkokorhonen/uutisseuranta/bq-activitystreams/article.schema.json)
- [note.schema.json](file:///Users/jaakkokorhonen/uutisseuranta/bq-activitystreams/note.schema.json)
- [collection.schema.json](file:///Users/jaakkokorhonen/uutisseuranta/bq-activitystreams/collection.schema.json)
- [hashtag.schema.json](file:///Users/jaakkokorhonen/uutisseuranta/bq-activitystreams/hashtag.schema.json)

Näitä skeemoja käytetään automaattisesti yksikkötesteissä (`test_as2_contract.py`) varmistamaan, että rajapinta tuottaa sopimuksen mukaista dataa.

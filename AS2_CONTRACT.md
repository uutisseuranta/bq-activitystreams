# AS2 Contract (v0.6.0)

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
    "dislikes": "_uutissenschaft:dislikes"
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

## 3. Minimikenttäjoukot (v0.6.0)

### Article (Uutisartikkeli)
- **Pakolliset kentät:**
  * `@context`
  * `type` (Article)
  * `id`: Pysyvä persistentti IRI muodossa `https://uutisseuranta.net/ap/objects/{hash}` (W3C DWBP BP7 -suositus)
  * `name`
  * `url` (Alkuperäinen uutisen URL toimituksen sivulla)
  * `published`
- **Valinnaiset kentät:** `summary`, `content`, `updated`, `attributedTo`, `likes`, `dislikes`, `_uutisseuranta:reactionCount`, `tag` (morfologiset tagit `Hashtag`-objekteina Jaccard-samankaltaisuusvertailua varten), `isAccessibleForFree`, `url_archive`

> [!NOTE]
> **Maksumuurimerkinnät**: Maksumuurin takana olevat uutiset (`isAccessibleForFree = false`) palautetaan rajapinnasta normaalisti, vaikka niillä ei olisi vielä arkistolinkkiä (`url_archive` puuttuu). Käyttöliittymä näyttää näille uutisille lukkokuvakkeen ja ohjaa lukijan arkistolinkkiin vain, jos sellainen on olemassa.

### Note (Kommentti)
- **Pakolliset kentät:** `@context`, `type` (Note), `id`, `content`, `published`, `attributedTo`, `inReplyTo`
- **Valinnaiset kentät:** `updated`, `thread_root`

### OrderedCollection (Outbox)
- **Pakolliset kentät:** `@context`, `type` (OrderedCollection), `id`, `totalItems`, `orderedItems`

## 4. Aktiviteetit ja kirjoitusrajapinta (Write API)

### Tunnisteen (tagi) lisääminen uutiselle (`Add`-aktiviteetti)
Uuden aihetunnisteen (Hashtag) lisäämiseksi uutisartikkelille lähetetään `POST` `/ap/inbox`-osoitteeseen seuraavan muotoinen JSON-dokumentti:
```json
{
  "@context": "https://www.w3.org/ns/activitystreams",
  "type": "Add",
  "actor": "https://uutisseuranta.net/users/{uid}",
  "object": {
    "type": "Hashtag",
    "name": "#tiede"
  },
  "target": {
    "type": "Article",
    "id": "https://uutisseuranta.net/ap/objects/{hash}"
  }
}
```

### Uutisten merkitseminen luetuiksi (`Read`-aktiviteetti)
Kun uutinen luetaan, lähetetään `POST` `/ap/inbox`-osoitteeseen `Read`-aktiviteetti. Rajapinta tukee erien lähettämistä (batching) suorituskyvyn parantamiseksi:
```json
{
  "@context": "https://www.w3.org/ns/activitystreams",
  "type": "Read",
  "actor": "https://uutisseuranta.net/users/{uid}",
  "object": [
    "https://uutisseuranta.net/ap/objects/id1",
    "https://uutisseuranta.net/ap/objects/id2"
  ]
}
```
* **Normalisointi**: Kirjoitusrajapinta hyväksyy `object`-kentässä sekä yksittäisen merkkijonon (yksi ID) että listan (useita ID-osoitteita).
* **Rajoitukset**:
  - `object`-lista voi sisältää enintään **500 ID:tä** per kutsu. Tämän ylitys palauttaa `400 Bad Request`.
  - Jokaisen ID:n on oltava ei-tyhjä merkkijono ja enintään **2048 merkkiä** pitkä. Virheelliset ID-arvot palauttavat `422 Unprocessable Entity`.
  - Jos taulussa havaitaan duplikaatteja (esim. verkon retryistä johtuen), ne käsitellään append-only -semantiikalla ja poistetaan kyselyvaiheessa `MAX(received_at)` -aggregaatiolla.

## 5. Hallitut poikkeamat standardista
- **POST /ap/outbox -kyselypoikkeama**: W3C ActivityPub -spesifikaation [§6.1](https://www.w3.org/TR/activitypub/#client-to-server-interactions) mukaan `POST` outboxiin edustaa uuden aktiviteetin luomista (client-to-server submission). Tässä toteutuksessa `POST /ap/outbox` on kuitenkin **puhdas kyselyoperaatio (client-to-server query)**, jolla haetaan uutisia siten että käyttäjän jo lukemat uutiset suodatetaan pois tietokantatasolla ilman pitkien URL-listojen lähettämistä GET-parametreissa.
  - Vastaus sisältää aina `Cache-Control: private, no-store` -otsikkeen estämään selaimen välimuistituksen personalisoidulle uutisvirralle.
  - Autentikoiduilla käyttäjillä kysely ohittaa body-datassa annetun `seen_ids`-listan varoituksella (tietokannan palvelinpuolen tila on autoritatiivinen) ja palauttaa `"meta": {"filtered_by": "token", "seen_ids_ignored": true}`.
  - Anonyymeillä käyttäjillä body-datan `seen_ids`-listaa käytetään suodattamiseen. Lista on rajoitettu enintään **10 000** alkioon; jos raja ylittyy, lista leikataan hiljaa uusimpiin ja vastaukseen lisätään `"meta": {"seen_ids_applied": 10000, "seen_ids_received": <n>}`.

- **Undo Like -poikkeama:** Järjestelmä toteuttaa toggle-reaktiologiikan (Like ↔ Dislike) poistamalla vanhan reaktion tietokannasta ja lisäämällä uuden. Tästä ei luoda erillistä AS2 `Undo`-aktiviteettia lokiin historiatiedon anonymisoinnin säilyttämiseksi.
- **OrderedCollectionPage -poikkeama (Päätös L-009):** `GET /ap/outbox` käyttää omaa hakumallia eikä AS2 `OrderedCollectionPage` + `next`/`prev` -linkkikenttiä. Tämä on tehty yksinkertaisuuden ja BigQuery-pohjaisen suorituskyvyn vuoksi, koska järjestelmä ei federoidu ulkoisiin AP-palvelimiin.

  **Miten paginaatio toimii käytännössä:** Client pyytää artikkeleita `?tag=` + `?n=`-parametreilla ilman kursoreja tai sivunumeroita. Vastauksen `totalItems` kertoo koko kokoelman koon, mutta `orderedItems`-taulukossa palautetaan vain pyydetty määrä (n kpl). AS2-yhteensopiva client, joka odottaa `next`/`prev`-linkkejä, ei voi sivuttaa kokoelmaa näiden kautta — paginaatio tapahtuu kutsumalla API:a uudelleen suuremmalla `n`-arvolla:

  ```
  GET /ap/outbox?tag=politiikka&n=5    → top-5 (pikaesikatseluun)
  GET /ap/outbox?tag=politiikka&n=50   → top-50 (scroll-to-load)
  GET /ap/outbox?tag=politiikka&n=500  → top-500 (maksimierä)
  ```

  Client suodattaa duplikaatit selaimen muistissa `id`-kentän perusteella. Tämä malli sopii BigQuery-pohjaiseen relevanssilajitteluun eikä vaadi OFFSET-lauseita. `totalItems` on yhteensopiva AS2 `Collection`-tyypin kanssa mutta ei `OrderedCollectionPage`-käytännön kanssa — poikkeama on täysin perusteltu, koska järjestelmä ei federoidu ulkopuolisiin ActivityPub-palvelimiin.

## 6. Automaattinen skeemavalidointi (CI/CD)
Rajapintasopimuksen koneluettavat ja viralliset JSON Schema -määritelmät sijaitsevat repositorion juuressa:
- [article.schema.json](file:///Users/jaakkokorhonen/uutisseuranta/bq-activitystreams/article.schema.json)
- [note.schema.json](file:///Users/jaakkokorhonen/uutisseuranta/bq-activitystreams/note.schema.json)
- [collection.schema.json](file:///Users/jaakkokorhonen/uutisseuranta/bq-activitystreams/collection.schema.json)
- [hashtag.schema.json](file:///Users/jaakkokorhonen/uutisseuranta/bq-activitystreams/hashtag.schema.json)

Näitä skeemoja käytetään automaattisesti yksikkötesteissä (`test_as2_contract.py`) varmistamaan, että rajapinta tuottaa sopimuksen mukaista dataa.

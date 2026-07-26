# AS2 Contract (v0.5.0)

Tämä dokumentti määrittelee `uutisseuranta`-järjestelmän käyttämän rajapintasopimuksen, joka pohjautuu W3C ActivityStreams 2.0 (AS2) -spesifikaatioon.

## 1. Yhteensopivuus ja ydinontologia
Kaikki rajapintavastaukset noudattavat [W3C ActivityStreams 2.0](https://www.w3.org/TR/activitystreams-core/) -spesifikaatiota.
API-vastausten Content-Type on oltava `application/activity+json; charset=utf-8`.

## 2. Nimiavaruuslaajennukset (Custom Extensions)
Järjestelmä käyttää `https://uutisseuranta.net/ns#` -kontekstia projektikohtaisille laajennuksille.
Kontekstimäärittely `@context` on oltava rajapinnan palauttamissa Article-objekteissa muodossa:
```json
"@context": [
  "https://www.w3.org/ns/activitystreams",
  { "_uutisseuranta": "https://uutisseuranta.net/ns#" }
]
```

### Tuetut laajennuskentät:
1. **`dislikes`**:
   - Tyyppi: `Collection`
   - Kuvaus: Artikkelin saamat dislike-reaktiot (vastine likes-kentälle).
   - Esimerkki: `{"type": "Collection", "totalItems": 5}`
   - *Huomio (review-löydös):* Kenttä ei käytä nimiavaruutta (kuten `_uutisseuranta:dislikes`), mikä poikkeaa puhtaasta JSON-LD-spesifikaatiosta. Tämä on tehty tietoisesti frontend-yhteensopivuuden helpottamiseksi.
2. **`_uutisseuranta:agreeCount`**:
   - Tyyppi: `Integer`
   - Kuvaus: Yhteenlaskettu tykkäys- ja dislike-määrä (`likes.totalItems + dislikes.totalItems`).

## 3. Minimikenttäjoukot (v0.5.0)

### Article (Uutisartikkeli)
- **Pakolliset kentät:** `@context`, `type` (Article), `id`, `name`, `url`, `published`
- **Valinnaiset kentät:** `summary`, `content`, `updated`, `attributedTo`, `likes`, `dislikes`, `_uutisseuranta:agreeCount`

### Note (Kommentti)
- **Pakolliset kentät:** `@context`, `type` (Note), `id`, `content`, `published`, `attributedTo`, `inReplyTo`
- **Valinnaiset kentät:** `updated`, `thread_root`

### OrderedCollection (Outbox)
- **Pakolliset kentät:** `@context`, `type` (OrderedCollection), `id`, `totalItems`, `orderedItems`

## 4. Hallitut poikkeamat standardista
- **Undo Like -poikkeama:** Järjestelmä toteuttaa toggle-reaktiologiikan (Like ↔ Dislike) poistamalla vanhan reaktion tietokannasta ja lisäämällä uuden. Tästä ei luoda erillistä AS2 `Undo`-aktiviteettia lokiin historiatiedon anonymisoinnin säilyttämiseksi.

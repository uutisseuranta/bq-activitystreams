# LICENSES.md — Avointen komponenttien lisenssit ja ylläpito (bq-activitystreams)

Tämä tiedosto kuvaa **bq-activitystreams**-repositoriossa käytetyt avoimen lähdekoodin kolmannen osapuolen ohjelmistokomponentit, niiden lisenssit, ylläpidon tilan, vastuutahot ja ylläpitäjien ensisijaiset toimintamaat.

| Komponentti | Lisenssi | Ylläpidon tila | Vastuutaho | Ylläpitäjän maa |
| :--- | :--- | :--- | :--- | :--- |
| **FastAPI** | MIT | Erittäin aktiivinen | Sebastián Ramírez (tiangolo) / Core-tiimi | 🇩🇪 Saksa / 🇨🇴 Kolumbia |
| **Uvicorn** | BSD-3-Clause | Erittäin aktiivinen | Encode / Tom Christie & tiimi | 🇬🇧 Iso-Britannia |
| **google-cloud-bigquery** | Apache-2.0 | Erittäin aktiivinen | Google LLC / Google Cloud | 🇺🇸 Yhdysvallat |
| **google-auth** | Apache-2.0 | Erittäin aktiivinen | Google LLC / Google Cloud | 🇺🇸 Yhdysvallat |
| **libvoikko** | GPL-3.0-or-later | Vakaa (ylläpitotila) | Harri Pitkänen & Voikko-yhteisö | 🇫🇮 Suomi |
| **beautifulsoup4** | MIT | Aktiivinen ylläpito | Leonard Richardson | 🇺🇸 Yhdysvallat |
| **httpx** | BSD-3-Clause | Erittäin aktiivinen | Encode / Tom Christie & tiimi | 🇬🇧 Iso-Britannia |
| **lxml** | BSD-3-Clause / GPL | Vakaa ylläpito | lxml-projekti / Stefan Behnel | 🇩🇪 Saksa |
| **requests** | Apache-2.0 | Aktiivinen ylläpito | Kenneth Reitz / Python Software Foundation | 🇺🇸 Yhdysvallat / Globaali |
| **ulid-py** | MIT | Ylläpitotila | Andrew Hawker | 🇺🇸 Yhdysvallat |

---

## RSS-lähteiden lisenssipolitiikka

Projekti hakee RSS-syötteitä suomalaisista uutismedioista. Tarkistuksen tila per lähde (viite: #62).

| Lähde | RSS-osoite | Tarkistettu | Tarkistusmetodi | Tarkistaja | Tulos |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Yle | https://feeds.yle.fi/uutiset/v1/majorHeadlines/YLE_UUTISET.rss | ✅ 2026-07-24 | Luettu käyttöehdot: https://yle.fi/aihe/artikkeli/2011/03/07/ylen-sisaltojen-kayttoehdot | @jaakkokorhonen | Sallittu: ei-kaupallinen aggregointi, attribuutio vaaditaan ("Lähde: Yle") |
| Helsingin Sanomat | https://www.hs.fi/rss/... | ⏳ Odottaa | Käyttöehdot kirjautumisseinän takana — sähköpostikysely lähetetään | | Odottaa |
| Ilta-Sanomat | https://www.is.fi/rss/... | ⏳ Odottaa | Sähköpostikysely lähetetään | | Odottaa |
| Iltalehti | https://www.iltalehti.fi/rss/... | ⏳ Odottaa | Sähköpostikysely lähetetään | | Odottaa |
| Kauppalehti | https://www.kauppalehti.fi/rss/... | ⏳ Odottaa | Sähköpostikysely lähetetään | | Odottaa |
| MTV Uutiset | https://www.mtvuutiset.fi/rss/... | ⏳ Odottaa | Sähköpostikysely lähetetään | | Odottaa |

**Attribuutiovaatimus (Yle):** Käyttöliittymässä on näytettävä teksti "Lähde: Yle" jokaisen Yle-artikkelin yhteydessä. Tämä koskee `uutisseuranta.github.io #1` (lähteiden aktiivisuus-widget).

---

## Komponenttien yksityiskohtainen kuvaus

### 1. FastAPI / Uvicorn / httpx / requests
- **Rooli:** API-rajapinnan rakentaminen, asynkroninen web-palvelin ja HTTP-pyynnöt ulkoisiin lähteisiin (esim. RSS-syötteet ja Open Graph -haku).
- **Elinvoima:** Erittäin vahva. FastAPI ja Uvicorn ovat nykyaikaisen Python-webkehityksen ytimessä.
- **Maantiede:** Pääylläpitäjä Sebastián Ramírez asuu Saksassa (syntyjään Kolumbiasta). Encode-ryhmä toimii pääosin Isossa-Britanniassa.

### 2. Google Cloud SDK (BigQuery & Auth)
- **Rooli:** BigQuery-integraatiot, tietojen tallennus ja OIDC-tunnisteiden varmennus kirjoitusoikeuksia varten.
- **Elinvoima:** Erittäin vahva. Google ylläpitää ja kehittää SDK-kirjastoja jatkuvasti osana GCP-ekosysteemiä.
- **Maantiede:** Yhdysvallat.

### 3. libvoikko
- **Rooli:** Suomen kielen sanojen morfologinen analyysi ja hakusanojen jäsennys dataputkessa (`voikko_job`).
- **Elinvoima:** Kypsä ja vakaa. Päivityksiä tulee harvoin, mutta kirjasto on ainoa laatuaan suomen kielen avoimessa käsittelyssä.
- **Maantiede:** Suomi.

### 4. lxml / BeautifulSoup4
- **Rooli:** HTML/XML-syötteiden jäsennys ja uutisobjektien tietojen (esim. RSS ja Open Graph) kaavinta.
- **Elinvoima:** Vakaa ja vakiintunut. Molemmat kirjastot ovat olleet Python-ekosysteemin peruskiviä jo yli kymmenen vuotta.
- **Maantiede:** Saksa / Yhdysvallat.

---

## Standardikirjasto (CPython / PSF)

Projektissa käytetään laajalti Pythonin standardikirjastoa (`logging`, `json`, `datetime`, `os`, `sys`, `hashlib` jne.). Nämä ovat osa CPython-toteutusta eivätkä ole erillisiä `pip`-riippuvuuksia.

| | |
|---|---|
| **Lisenssi** | PSF License 2.0 — permissiivinen, BSD-yhteensopiva |
| **Ylläpitäjä** | Python Software Foundation (PSF) |
| **Maa** | 🇺🇸 Yhdysvallat |

`JsonFormatter`-lokitusluokka (käytössä lokitusjärjestelmässä `gcp_logging.py`) rakentuu yksinomaan standardikirjaston `logging`-, `json`- ja `datetime`-moduulien päälle — ei kolmannen osapuolen riippuvuuksia. Päätös on kirjattu `TECHNICAL_DESIGN.md`:n Lokitus-osiossa.

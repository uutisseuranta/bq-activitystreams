# bq-activitystreams — Projektin yleiskatsaus

Google Cloud Services -pohjainen ActivityStreams 2.0 -backend, joka tuottaa
uutisseuranta.fi-sovellukselle Article-, Note- ja Collection-objekteja.
Ei täyttä ActivityPub-implementaatiota — tavoitteena AS2-yhteensopivuus.

Lähde: [uutisseuranta/bq-activitystreams](https://github.com/uutisseuranta/bq-activitystreams)
· Haara: `feat/rss-og-voikko-pipeline`

---

## Mikä tämä projekti on

`bq-activitystreams` on Google Cloud Services -pohjainen ActivityStreams 2.0 -backend, joka tuottaa uutisseuranta.fi-sovellukselle Article-, Note- ja Collection-tyyppisiä objekteja. Projekti ei toteuta täyttä ActivityPub-protokollaa — tavoitteena on riittävä AS2-yhteensopivuus. Nimi kuvaa tietovarastovalinnan: repositorio nimettiin aiemmin `gcs-activitystreams`, mutta uudelleennimeäminen `bq-activitystreams`:ksi kertoo, että tietovarasto on BigQuery eikä Google Cloud Storage.

Koko GCP-infrastruktuuri — Cloud Run -palvelut ja -jobit, Cloud Scheduler, IAM-oikeudet, BigQuery-taulut ja Secret Manager — hallitaan koodina Terraformilla. Tuotantodeploy tapahtuu automaattisesti CI:n kautta jokaisella `main`-haaran pushilla, ja CI käyttää `gcloud run deploy --source` -komentoa, joka rakentaa palvelun Cloud Buildin buildpackeilla suoraan lähdekoodista ilman erillisiä Dockerfileja. Arvioidut infrakustannukset ovat noin 0,10–2 dollaria kuukaudessa normaalikuormalla.

---

## Arkkitehtuuri — miten järjestelmä toimii

Julkinen API-domain on `activitystreams.uutisseuranta.net`, GCP-projekti on `uutisseuranta-activitystreams` ja sijainti on `europe-north1`. BigQuery on jaettu kahteen datasettiin: julkinen `activitystreams`-datasetti sisältää avointa dataa, kun taas yksityinen `activitystreams_social`-datasetti pitää sosiaalisen datan — kommentit, tykkäykset ja käyttäjätoiminnot — erillään.

Järjestelmä koostuu neljästä ajastetusti ajettavasta Cloud Run Jobista ja kolmesta jatkuvasti käynnissä olevasta Cloud Run Servicestä:

| Job tai palvelu | Tyyppi | Ajastus | Vastuu |
|---|---|---|---|
| `rss-fetch-job` | Cloud Run Job | Joka 15. min (:07, :22, :37, :52) | Uutissyötteet RSS-lähteistä |
| `og-enrichment-job` | Cloud Run Job | Kerran tunnissa (:14) | OpenGraph-metatietojen haku ja rikastus |
| `voikko-job` | Cloud Run Job | Kerran tunnissa (:19) | Morfologiset tagit ja lemmatisointi (Voikko) |
| `likes-and-updated-job` | Cloud Run Job | Joka 2. tunti (:21) | Tykkäyslaskuri ja `updated`-aikaleima |
| `query-api` | Cloud Run Service | `GET /ap/outbox` | AS2 outbox, lukupään API |
| `og-scraper` | Cloud Run Service | `POST /ap/scrape` | OG-metatietojen haku URL:sta |
| `write-api` | Cloud Run Service | `POST /ap/activities` | Käyttäjäaktiviteetit |

Jokainen fetch-job kirjoittaa onnistuneen ajon jälkeen `last_fetched_at`-aikaleiman `activitystreams.config`-tauluun. Epäonnistunut ajo ei koskaan päivitä tätä arvoa, joten seuraava ajo käyttää edellistä onnistunutta ajankohtaa fetch-ikkunanaan — tämä takaa datan jatkuvuuden ilman aukkoja. Jos avain puuttuu kokonaan (ensimmäinen ajo tai cold start), job hakee kiinteältä fallback-aikaikkunalta (esim. viimeiset 24 tuntia) ja kirjoittaa arvon kantaan onnistuneen ajon jälkeen.

HTTP-virheissä noudatetaan selkeää retry-strategiaa: 429-vastaus odottaa `Retry-After`-otsakkeen ajan tai 60 sekuntia, 5xx-virheissä käytetään eksponentiaalista backoffia (30 s, 5 min, 15 min), ja 404-virhe kirjataan lokiin mutta ei aiheuta retryä. BigQuery MERGE -kirjoitusvirhe peruuttaa koko batchin ja palauttaa exit code `1`.

---

## Autentikaatio ja zero trust

Järjestelmässä sovelletaan zero trust -periaatetta: kaikki kirjoitusoperaatiot vaativat autentikaation riippumatta siitä, mistä pyyntö tulee. Lukupääte `GET /ap/outbox` on julkinen avoin data eikä vaadi autentikointia, mutta `POST /ap/scrape` ja `POST /ap/activities` vaativat molemmat kelvollisen Google OIDC `id_token`-tokenin. Backend validoi tokenin aina itse — se ei luota siihen, että käyttöliittymä olisi jo tarkistanut kirjautumisen.

Autentikaatio on rakennettu kahdelle kerrokselle. Ensimmäinen kerros on Cloud Run IAM -tasolla: palvelu on konfiguroitu `--no-allow-unauthenticated`-lipulla, jolloin Google Cloud itsessään hylkää pyynnöt ilman kelvollista Identity Platform -tokenia jo ennen kuin ne pääsevät sovelluskoodin suoritettavaksi. Toinen kerros on sovellustasolla: FastAPI-koodi validoi tokenin allekirjoituksen, voimassaolon ja `aud`-kentän, ja hylkää `401 Unauthorized` -vastauksella pyynnöt, joissa nämä eivät täsmää.

---

## Tietomallin filosofia — asiat riitelevät, ei ihmiset

Tässä projektissa sisältö on toimijana, ei henkilö. AS2-spesifikaatio sallii `actor`-kentässä `Person`-tyypin, mutta `bq-activitystreams` ei käytä actoreita käyttäjäidentiteetteinä. Kaikki actorit ovat joko `Organization` (julkaisija kuten Helsingin Sanomat tai Helsingin kaupunki) tai `Service` (automaattipalvelu kuten Voikko-enrichment tai RSS-jobi).

Tästä filosofiasta seuraa tietoisesti rajalliset aktiviteettityypit. `Like` toteutetaan sisällön kiinnostavuuden mittarina ja `Dislike` on sen vastine — ne muodostavat yhdessä Agree/Disagree-togglen. Jos käyttäjä vaihtaa reaktionsa `Like`:sta `Dislike`:ksi (tai toisinpäin), vanha reaktio poistetaan tietokannasta ja uusi kirjataan tilalle. Tästä vaihdosta ei luoda erillistä AS2 `Undo`-aktiviteettia lokiin, jotta historiatiedon anonymisointi säilyy. `Undo Like` erillisenä aktiviteettina, `Announce` ja server-to-server ActivityPub-federointi jätetään toteuttamatta — `outbox` on julkinen luettava syöte, ei federoitu inbox.

---

## ActivityStreams 2.0 -datamalli

Järjestelmä tuottaa kolmea objektityyppiä:

**Article** edustaa uutisartikkelia tai blogipostausta. Pakollisia kenttiä ovat `@context`, `type` (arvo `"Article"`), `id`, `name`, `url` ja `published`. Objektin `id` on pysyvä IRI muodossa `https://uutisseuranta.net/ap/objects/{hash}` — se erotetaan artikkelin operatiivisesta `url`-osoitteesta, jotta toimitukselliset URL-rakenteen muutokset eivät riko olemassa olevia linkityksiä. Valinnaisia kenttiä ovat `summary`, `content`, `updated`, `attributedTo`, `likes`-kokoelma, `dislikes`-kokoelma, projektikohtainen `_uutisseuranta:reactionCount` sekä `tag`-kenttä Voikko-jobin tuottamille morfologisille tageille AS2 Hashtag-objekteina.

**Note** edustaa lyhyttä tekstikommenttia tai artikkelin vastausviestiä. Se vaatii `content`-, `attributedTo`- ja `inReplyTo`-kentät, joista viimeksi mainittu sitoo kommentin pääartikkeliin tai ylempään kommenttiin. Kommenttiketjun syvyys on rajoitettu kahteen tasoon — vastaukseen ei voi vastata.

**OrderedCollection** on järjestetty lista objekteista ja kuvaa esimerkiksi uutisvirta tai outbox-endpointin vastausta. Järjestelmä käyttää inkrementaalista `?n=`-parametripohjaista hakumallia eikä AS2:n `OrderedCollectionPage` + `next`/`prev` -linkkikenttiä, koska järjestelmä ei federoidu ulkoisiin ActivityPub-palvelimiin (päätös L-009). Client pyytää aina top-N relevantinta artikkelia alusta alkaen (n=5/50/500) — ei kursoreja eikä sivunumeroita.

---

## Scraping-etiikka ja REP-standardi

Uutissivustojen ohjelmallinen haku noudattaa tiukasti **Robots Exclusion Protocol** -standardia (RFC 9309). Ennen jokaista HTTP-hakua kohdedomain haetaan `robots.txt`-tiedosto, ja jos säännöt estävät haun `Disallow`-direktiivillä, haku keskeytetään välittömästi ja virhe kirjataan `og_enriched_error`-sarakkeeseen. Palvelimen ylikuormittamisen ehkäisemiseksi `robots.txt`-säännöt välimuistitetaan paikallisesti 24 tunniksi. Lisäksi ladattavan HTML-sivun kokoa rajoitetaan niin, että response-lukeminen keskeytetään heti HTML-head-alueen päätyttyä (enintään ~2 MB), mikä säästää sekä palvelimen että kohdedomain verkkokapasiteettia.

---

## Standardit ja normatiiviset vaatimukset

Projekti noudattaa seuraavia standardeja:

| Kerros | Standardi | Viite |
|---|---|---|
| Datamalli | ActivityStreams 2.0 (JSON-LD) | W3C AS2 |
| Aikaleimat | RFC 3339 (ISO 8601 -profiili) | RFC 3339 |
| Merkistö | UTF-8 | Unicode Standard |
| Tiedonsiirtovapaus | W3C DWBP BP7 (persistent URIs) | W3C DWBP |
| Web Scraping | Robots Exclusion Protocol | RFC 9309 |

Rate limiting toteutetaan FastAPI:n `slowapi`-kirjastolla. Cloud Runin vaakasuuntainen skaalaus tarkoittaa, että jokainen instanssi ylläpitää laskureitaan itsenäisesti — globaali synkronointi ei ole käytössä alpha-vaiheessa, koska `min-instances=1`-asetus rajoittaa instanssien määrän yhteen. Jos tarvetta kasvaa, siirrytään jaettuun Redis-välimuistiin.

---

## Koodikonventiot — yhteinen sopimus kaikkien repojen välillä

`CODE_CONVENTIONS.md` on yhteinen kaikille uutisseuranta-repositorioille. Kaikki tiedostot sijoitetaan repositorion juureen ilman alikansioita — `.github/`-hakemisto on ainoa poikkeus ja se on varattu yksinomaan GitHubin omalle infrastruktuurille. Tätä perustelee se, että projekteissa on vähän tiedostoja ja hakemistorakenne lisäisi navigaatiokulua ilman todellista hyötyä.

Tiedostojen nimeämisessä sovelletaan kolmea käytäntöä: normatiiviset sopimusdokumentit käyttävät `SCREAMING_SNAKE_CASE`-muotoa (kuten `TECHNICAL_DESIGN.md` ja `STANDARDS.md`), MML-skill-konfiguraatiot nimetään muodossa `skill-(käyttötarkoitus).yml`, ja kaikki muu lähdekoodi käyttää `kebab-case`-muotoa. `README.md` ja `LICENSE` ovat poikkeuksia ja kirjoitetaan isolla GitHub-konvention mukaisesti.

Kommentoinnin pääperiaate on selittää **miksi**, ei **mitä** — koodi selittää itse toimintansa, mutta hyvä kommentti kertoo päätöksen taustan tai ei-ilmeisen reunaehdon. Kaikki arkkitehtuuripäätökset kirjataan repokohtaiseen `DECISION_LOG.csv`-tiedostoon auditointikelpoisina merkintöinä. Cross-repo-linkit kirjoitetaan aina absoluuttisina GitHub-URL:eina.

---

## Terraform-infrastruktuurin deploy-prosessi

Bootstrap täytyy tehdä kerran paikallisesti ennen kuin CI voi ottaa hallinnan — tämä johtuu chicken-and-egg-ongelmasta: CI tarvitsee Workload Identity Federation (WIF) -resursseja autentikoidakseen GCP:hen, mutta nämä resurssit luodaan vasta ensimmäisessä `terraform apply`:ssa.

Deploy tapahtuu kuudessa vaiheessa. Ensin haetaan oikea branch ja luodaan GCS state-bucket, jota tarvitaan Terraform remote backendille — paikallinen `terraform.tfstate` estäisi CI:n toimimasta. Seuraavaksi autentikoidutaan paikallisesti `gcloud auth application-default login` -komennolla. Bootstrappaus tehdään kahdessa osavaiheessa: ensin ajetaan pelkästään WIF-resurssit ilman backendia (`-backend=false`), sitten aktivoidaan GCS-backend ja migroidaan state sinne (`terraform init -migrate-state`), ja lopuksi ajetaan täysi `terraform apply` lopulle infralle. Terraform-outputeista otetaan talteen `wif_provider`- ja `wif_service_account`-arvot, jotka asetetaan GitHub Secrets -ympäristömuuttujiksi `gh secret set` -komennolla ennen seuraavaa push-to-main-tapahtumaa.

---

## Terraform-testistrategia — IaC-testauskolmio

Terraform-konfiguraatio on suoritettavaa koodia siinä missä Python tai Go, ja sen virheet voivat tarkoittaa IAM-ylilyöntejä, avoimia portteja tai datahäviötä. Testausstrategia on rakennettu kolmikerroksisesti IaC-testauskolmion (Testing Pyramid) periaatteen mukaan.

Alimmalla kerroksella on staattinen analyysi, joka ajetaan jokaisella pull requestilla ilman GCP-yhteyttä: `terraform fmt -check` varmistaa kanonikaalisen muotoilun, `terraform validate` tarkistaa syntaksin, `tflint` havaitsee deprecated-syntaksin ja virheelliset provider-argumentit, ja Checkov 3.x suorittaa tietoturva-analyysin tasolla `--check-level HIGH`. Keskimmäisellä kerroksella on `terraform plan`, joka ajetaan CI:ssä jokaisella `main`-mergetyllä commitilla WIF-autentikoinnilla mutta ilman provisiointia. Ylimmällä kerroksella on live smoke-test (`live-smoke-test.sh`), joka ajetaan harvoin ja manuaalisesti oikeaa tuotantoympäristöä vasten.

Kaikki GitHub Actions -actionit on pinnattu täydellä commit SHA:lla — ei versionumerolla. Tämä supply chain -turvallisuuskäytäntö estää mutatoituvan tagin tuomasta uutta, potentiaalisesti haitallista koodia ilman eksplisiittistä päätöstä.

---

## Lisenssit ja attribuutiot

Projekti itse on lisensoitu MIT-lisenssillä. Merkittävimmät riippuvuudet ovat Voikko-kirjasto suomen kielen morfologiseen analyysiin, FastAPI web-frameworkille sekä Google Cloud Python -asiakaskirjastot BigQuery- ja Secret Manager -integraatioihin. Täydellinen lista löytyy tiedostosta [LICENSES.md](./LICENSES.md).

---

## Dokumentaatio ja ristiin-linkit

- [ARCHITECTURE.md](./ARCHITECTURE.md) — täydellinen arkkitehtuuridokumentaatio ja muutoshistoria
- [STANDARDS.md](./STANDARDS.md) — normatiiviset vaatimukset (AS2, RFC 3339, GDPR)
- [DESIGN_GUIDELINES.md](./DESIGN_GUIDELINES.md) — backend-spesifiset arkkitehtuuriperiaatteet
- [AS2_CONTRACT.md](./AS2_CONTRACT.md) — rajapintasopimus ja kenttämäärittelyt
- [TECHNICAL_DESIGN.md](./TECHNICAL_DESIGN.md) — käyttötapaukset ja tekninen suunnittelu
- [CODE_CONVENTIONS.md](./CODE_CONVENTIONS.md) — koodikonventiot kaikille uutisseuranta-repositorioille
- [LICENSES.md](./LICENSES.md) — lisenssit ja attribuutiot
- [patterns/STANDARDS.md](https://github.com/uutisseuranta/patterns/blob/main/STANDARDS.md) — frontend AS2-kenttäkartta
- [uutisseuranta/uutisseuranta.github.io](https://github.com/uutisseuranta/uutisseuranta.github.io) — frontend-sovellus

# CODE_CONVENTIONS.md

> Tämä dokumentti on yhteinen kaikille uutisseuranta-repositorioille.
> Sama tiedosto sijaitsee jokaisen repon juuressa.
>
> **Repos:**
> - [uutisseuranta/uutisseuranta.github.io](https://github.com/uutisseuranta/uutisseuranta.github.io)
> - [uutisseuranta/patterns](https://github.com/uutisseuranta/patterns)
> - [uutisseuranta/jira-github-integration](https://github.com/uutisseuranta/jira-github-integration)

---

## Tiedostorakenne

**Kaikki tiedostot sijaitsevat repositorion juuressa. Alikansioita ei käytetä.**

Poikkeus: `.github/`-hakemisto on sallittu **GitHubin omaa infrastruktuuria** varten (Actions-workflowt, issue-templatet, Dependabot-konfiguraatio jne.). `.github/` ei ole tarkoitettu repon omille tiedostoille — sinne ei sijoiteta dokumentaatiota, skriptejä, konfiguraatioita eikä muita projektin tiedostoja.

```
repo/
├── .github/               ← VAIN GitHub-infrastruktuuri (Actions, issue-templatet)
│   └── workflows/
│       └── *.yml
├── skill-(käyttötarkoitus).yml  ← MML-skill-konfiguraatiot, juuressa
├── SOPIMUSDOKUMENTTI.md   ← SCREAMING_SNAKE_CASE-dokumentit juuressa
├── tiedosto.ext           ← muu kebab-case-lähdekoodisto juuressa
└── ...
```

Perustelu (D-009): Projekteissa on vähän tiedostoja. Hakemistorakenne ei tuo lisäarvoa — se lisää navigaatiokulua ilman hyötyä. Kaikki tiedostot löytyvät yhdestä paikasta.

---

## Tiedostojen nimeäminen

### Sopimusdokumentit — `SCREAMING_SNAKE_CASE.md`

Kaikki normatiiviset sopimusdokumentit nimetään `SCREAMING_SNAKE_CASE`-muodossa:

```
TECHNICAL_DESIGN.md
DECISION_LOG.csv
CODE_CONVENTIONS.md
STANDARDS.md
DESIGN_GUIDELINES.md
PATTERNS_CATALOG.md
USER_PATHS.md
```

Perustelu (D-002, uutisseuranta.github.io): Erottaa sopimukset ja normatiiviset dokumentit ops-tiedostoista ja lähdekoodista. Yhtenäinen nimeäminen kaikkien repojen välillä.

### Skill-tiedostot — `skill-(käyttötarkoitus).yml`

MML-skill-konfiguraatiot nimetään muodossa `skill-(käyttötarkoitus).yml` ja sijoitetaan **repositorion juureen**.

```
skill-decision-log.yml
skill-jira-sync.yml
skill-label-manager.yml
```

Nimeämissäännöt:
- Prefix `skill-` on aina läsnä — erottaa skill-tiedostot muusta konfiguraatiosta
- Käyttötarkoitusosa: `lowercase`, sanojen välissä väliviiva (`kebab-case`)
- Vain pienet kirjaimet ja väliviivoilla — ei alaviivoja, välilyöntejä eikä erikoismerkkejä
- Enintään 64 merkkiä (koko tiedostonimi)
- Formaatti: `.yml` (ei `.yaml`)

Perustelu: Claudelint-spesifikaation skill-nimikäytäntö (lowercase-with-hyphens, max 64 merkkiä) sovellettuna tähän projektiin. `skill-`-prefix tekee tiedostot tunnistettaviksi hakemistolistauksessa ilman hakemistorakennetta. Skillit ovat repokohtaisia YAML-konfiguraatioita, eivät sopimusdokumentteja — siksi ne eivät käytä `SCREAMING_SNAKE_CASE`-muotoa.

### Lähdekooditiedostot — `kebab-case`

Kaikki muut tiedostot (HTML, CSS, JS, JSON, Bash): `kebab-case`.

```
index.html
style.css
app.js
prefs.js
profile.js
patterns.js
live-smoke-test.sh
firebase.json
jira-webhook-relay.yml
mock-article-wayback-machine.json
```

Fontit ja binäärit noudattavat samaa konventiota:
```
satoshi-regular.woff2
cabinet-grotesk-bold.woff2
```

### README ja LICENSE — standardi

`README.md` ja `LICENSE` kirjoitetaan isolla — GitHub-konventio.

---

## Cross-repo-linkit

Kaikki viittaukset toisiin repositorioihin kirjoitetaan **absoluuttisina GitHub-URL:eina**.

```markdown
<!-- ✅ Oikein -->
[TECHNICAL_DESIGN.md](https://github.com/uutisseuranta/jira-github-integration/blob/main/TECHNICAL_DESIGN.md)

<!-- ❌ Väärin -->
[TECHNICAL_DESIGN.md](../jira-github-integration/TECHNICAL_DESIGN.md)
```

Perustelu (D-002, uutisseuranta.github.io): Relatiiviset polut eivät toimi GitHubissa cross-repo-viittauksissa.

---

## Versionumerointi

Kaikki repositoriot käyttävät **SemVer**-versionumerointia muodossa `vX.Y.Z`.

```
v1.0.0   ← ensimmäinen vakaa julkaisu
v1.2.3   ← patch
v2.0.0   ← breaking change
```

Git-tagit luodaan tällä muodolla. GitHub Releases käyttää samaa tunnistetta.

Perustelu (D-002, uutisseuranta.github.io): Yhtenäiset julkaisukäytännöt kaikkien repojen välillä.

---

## Koodin kommentointi

### Periaate

Kommentit selittävät **miksi**, eivät **mitä**. Koodi selittää itse mitä se tekee — hyvä kommentti kertoo päätöksen taustan tai ei-ilmeisen reunaehdon.

```js
// ❌ Turha — koodi sanoo saman
const user = getUser(); // haetaan käyttäjä

// ✅ Hyödyllinen — kertoo miksi
// Firebase palauttaa null ennen auth-tilan latautumista;
// näytetään skeleton eikä login-näkymää välttääksemme vilkkumisen
if (user === null) return renderSkeleton();
```

### JavaScript

```js
// Yksiriviset kommentit: //
// Monirivinen selitys:
// Rivi 1
// Rivi 2

// Funktiotason JSDoc vain julkisille API-funktioille:
/**
 * Palauttaa käyttäjän preferenssit tai oletukset jos ei ole tallennettu.
 * @returns {Object}
 */
export function getPrefs() { ... }
```

### CSS

```css
/* Yksiriviset: */
.component { color: var(--color-text); }

/* Osion otsikko: */
/* =============================================================
   Typografia
   ============================================================= */

/* Selitys miksi, ei mitä: */
/* clamp() estää tekstin skaalautumisen alle 12px — iOS-zoom-esto */
font-size: clamp(0.75rem, ...);
```

### HTML

```html
<!-- Yksiriviset: -->
<!-- Monirivinen:
  Tässä on käytetty aria-live koska screen reader
  ei muuten huomaa dynaamista päivitystä.
-->
```

### Bash

```bash
# Yksiriviset: #
# Skriptin yläosan kuvaus:
# live-smoke-test.sh — testaa deployatun sivuston HTTP-vastaukset
# Käyttö: ./live-smoke-test.sh https://uutisseuranta.fi
```

### YAML (GitHub Actions & skill-tiedostot)

```yaml
# Yksiriviset: #
# Selitä miksi step on olemassa, ei mitä se tekee:
# toJson() muuntaa GitHub event -objektin merkkijonoksi;
# curl lukee sen stdin:stä --data-binary @- välttääkseen
# shellin erikoismerkkien tulkinnan
```

### Python

```python
# Yksiriviset: #
# Funktiotason docstring julkisille funktioille:
def get_prefs(uid: str) -> dict:
    """Palauttaa käyttäjän preferenssit tai oletusarvot."""
    ...
```

---

## Päätösloki

Kaikki arkkitehtuuripäätökset kirjataan repokohtaiseen `DECISION_LOG.csv`-tiedostoon.

```
id,date,timestamp,title,decision,rationale,affects_issues
G-001,2026-07-04,2026-07-04 12:00:00,Flat directory structure,...
L-001,2026-07-05,2026-07-05 12:00:00,Ignore Ruff S608 (SQL injection on f-strings),...
L-002,2026-07-05,2026-07-05 12:00:00,Ignore Ruff S105 (Hardcoded password checks),...
L-003,2026-07-05,2026-07-05 12:00:00,Ignore Ruff B008 (Function call in default arguments),...
L-004,2026-07-05,2026-07-05 12:00:00,Ignore Ruff S110 (try-except-pass block),...
L-005,2026-07-05,2026-07-05 12:00:00,Ignore Ruff B904 (Exception chaining),...
L-006,2026-07-05,2026-07-05 12:00:00,Ignore Ruff E501 (Line too long),...
```

Jira-integraation päätösloki:
[jira-github-integration/DECISION_LOG.csv](https://github.com/uutisseuranta/jira-github-integration/blob/main/DECISION_LOG.csv)

---

## GCP-first kehitys- ja testausstrategia (Päätös G-017)

Paikalliset testit ja emulaattorit eivät vastaa täysin GCP:n tuotantoympäristön oikeuksia, verkkoreitityksiä ja Cloud Run -revisioiden liikennejakoja. Tämän vuoksi noudatetaan seuraavaa kehitysmallia:

1. **Julkaisuun tähtäävä kehitys PR-vetoisesti**:
   - Älä koskaan kehitä tai tee julkaisuun tähtääviä commit-ketjuja suoraan `main`-haaraan.
   - Avaa PR heti kehityksen alussa, jotta koodi deployataan ja sitä testataan tuotannon kaltaisessa aidoissa GCP-ympäristöissä ennen mergeä.
2. **Liikennejakojen (traffic routing) ja healthcheckien hallinta**:
   - Varmista, ettei Cloud Run -palveluiden liikenne jää jumiin vanhoihin revisioihin. Rollback- ja liikenteenohjauskomennot täytyy testata ja ajaa livenä.
3. **IAM-palvelutilien (Service Account) testaus**:
   - Varmista, että sovelluksen vaatimat BigQuery-, Firestore- ja Secret Manager -oikeudet on myönnetty kontin suorittavalle Service Accountille eikä vain kehittäjän omalle profiilille.
4. **Skeemojen ja dokumenttien synkronointi**:
   - Kaikki datamallimuutokset skeemoissa (`*.schema.json`) on ajettava dokumentaatioon (`STANDARDS.md`) `generate-standards.sh` -skriptillä saman commit-ketjun aikana ennen PR:n luontia, jotta CI-putken validointi ei kaadu.


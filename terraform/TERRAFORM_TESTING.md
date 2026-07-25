# Terraform-testistrategia — bq-activitystreams

Tämä dokumentti kuvaa bq-activitystreams-repon Terraform-konfiguraation testausstrategian,
perustelee jokaisen testauskerroksen olemassaolon ja antaa konkreettiset ohjeet toteutukseen.
Strategia noudattaa IaC-testauskolmiota (Testing Pyramid), joka on yleisesti hyväksytty
best practice Terraform-projekteille.

---

## Miksi Terraform-koodia testataan

Terraform-konfiguraatio on suoritettavaa koodia siinä missä Python tai Go — se provisonoi
oikean infrastruktuurin oikeaan pilveen, ja virheet siinä voivat tarkoittaa IAM-ylilyöntejä,
avoimia portteja tai datahäviötä. Ero sovelluskoodiin on se, että Terraform-virheitä ei löydä
yksikkötesti joka ajaa mockeja: infrastruktuuri on verifioitava joko suunnitelma-tasolla tai
oikeassa pilvessä.

Tästä syystä testistrategia on kerrostettu. Halvat, nopeat tarkistukset ajetaan jokaisella
PR:llä; kalliit, oikeaa infraa deploaavat testit ajetaan vain `main`-haaraan mergetyn koodin
jälkeen tai manuaalisesti.

---

## Testauskolmio

```
          ┌──────────────────────────────────┐
          │   E2E / Live smoke-test           │  ← harva, manuaalinen
          │   live-smoke-test.sh + WIF        │
          ├──────────────────────────────────┤
          │   Integraatio                     │  ← PR → main push
          │   terraform plan (CI)             │
          ├──────────────────────────────────┤
          │   Staattinen analyysi             │  ← jokainen PR
          │   fmt · validate · tflint         │
          │   checkov                         │
          └──────────────────────────────────┘
```

Mitä alempana kerros on, sitä nopeampi, halvempi ja useammin ajettava se on. Mitä ylempänä,
sitä realistisempi — mutta myös hitaampi ja kalliimpi. Molemmat ääripäät ovat välttämättömiä:
pelkkä staattinen analyysi ei havaitse resurssien välisiä IAM-ristiriitoja, ja pelkkä live-testi
on liian hidas ja kallis jokaista PR-committia varten.

---

## Kerros 1: Staattinen analyysi (PR-tarkistukset)

### Mitä testataan

Statinen analyysi ei provisoi mitään. Se tarkistaa koodin rakennetta ja
turvallisuuspolitiikkoja ilman GCP-yhteyttä.

**`terraform fmt -check`** — varmistaa että koodi on canonically formatoitu. Hashicorp
määrittelee kanonikaalisen muotoilun, ja CI hylkää formattaamattoman koodin. Tämä estää
diff-melu-ongelmat (whitespace-muutokset peittävät todelliset muutokset).

**`terraform validate`** — tarkistaa syntaksin ja moduuliviittaukset ilman backend-yhteyttä.
Havaitsee kirjoitusvirheet muuttujanimissä ja puuttuvat pakolliset argumentit ennen kuin koodi
pääsee plan-vaiheeseen.

**`tflint`** — Terraform-linter joka havaitsee deprecated-syntaksin, virheelliset
provider-argumentit (esim. väärä `google_cloud_run_service`-kenttä) ja käyttämättömät
muuttujat. Pelkkä `validate` ei tunne provider-skeemoja. **Toteutettu:** `terraform-lint`-job
`unit-tests.yml`:ssä (`setup-tflint v4.1.1`, `tflint-ruleset-google` v0.55.0).

**Checkov (Checkov 3.x)** — staattinen tietoturva-analyysi Terraform-konfiguraatiolle.
Tarkistaa esim. onko Cloud Run -palveluissa `--ingress internal-only` asetettu, onko
IAM-rooleissa vähimmäisoikeus-periaate, onko secrets hallittu oikein. Projektin CI ajaa
`--check-level HIGH` ja skipittaa kaksi tarkistusta perustellusti (`CKV_GCP_82`,
`CKV_GCP_34`).

### Miksi checkov eikä tfsec

Checkov 3.x kattaa GCP-resurssit laajemmin kuin tfsec ja on aktiivisemmin ylläpidetty
GCP-providerille. Tfsec on yhä validi valinta mutta se voidaan lisätä checkovin rinnalle
jos halutaan toinen tarkastusajuri.

### Toteutus (unit-tests.yml — terraform-lint job)

```yaml
- name: Checkov – Terraform staattinen analyysi
  run: |
    checkov -d terraform/ \\
      --framework terraform \\
      --output github_failed_only \\
      --check-level HIGH \\
      --skip-check CKV_GCP_82,CKV_GCP_34
```

Skipatut tarkistukset:
- **CKV_GCP_82** (`google_compute_ssl_policy`) — ei sovelleta tähän arkkitehtuuriin (ei
  load balanceria suoraan TF:ssä)
- **CKV_GCP_34** (`google_project_iam_member` project-level) — kaanonpäätös G-009 poisti
  projekti-tason `secret_accessor`; `bigquery.dataEditor` ja `bigquery.user` ovat pakollisia
  projekti-tason rooleja eikä datasetti-tason IAM kata niitä. Resurssitason
  `#checkov:skip`-annotaatiot löytyvät `terraform/iam.tf`:stä auditointikelpoisina perusteluina.

### SHA-pinnaus

Kaikki GitHub Actions -actionit on pinnattu full commit SHA:han (`actions/checkout@11bd71...`
eikä `@v4`). SHA-pinnaus estää supply chain -hyökkäykset joissa tag siirretään osoittamaan
haitalliseen committiin — tagit ovat mutable, SHA:t eivät.

---

## Kerros 2: Integraatiotesti — terraform plan (CI, PR)

### Mitä testataan

`terraform plan` luo eksekutiivisen muutossuunnitelman oikeaa GCP-backendiä vasten, mutta
ei provisoi mitään. Se validoi:

- Resurssien väliset riippuvuudet (viittaako `cloudrun_services.tf` olemassaolevaan
  service accountiin `iam.tf`:ssä)
- Muuttujien arvot oikeaa provider-skeemaa vasten
- WIF-resurssin konfiguraation (`attribute_condition`, `attribute_mapping`)
- BigQuery-datasettien ja -taulujen skeemamuutokset ennen kuin ne ajetaan

Pelkkä `validate` ei havaitse näitä, koska se ei ota yhteyttä provideriin eikä tunne
olemassaolevia resursseja.

### Toteutus

```yaml
terraform-plan:
  runs-on: ubuntu-latest
  if: github.event_name == 'pull_request'
  permissions:
    id-token: write
    contents: read
  steps:
    - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
    - uses: google-github-actions/auth@71f986410dfbc7added4569d411d040a91dc6935 # v2.1.10
      with:
        workload_identity_provider: ${{ secrets.WIF_PROVIDER }}
        service_account: ${{ secrets.WIF_SERVICE_ACCOUNT }}
    - uses: hashicorp/setup-terraform@e6baf59ee5b1f91d0b65f827518c8c40c5df80f6 # v3.1.2
      with:
        terraform_version: "1.9.8"
    - name: terraform init (no backend)
      run: terraform -chdir=terraform init -backend=false
    - name: terraform plan
      run: |
        terraform -chdir=terraform plan -no-color 2>&1 | tee -a $GITHUB_STEP_SUMMARY
      env:
        TF_VAR_gcp_project: uutisseuranta-activitystreams
        TF_VAR_github_token: ${{ secrets.GITHUB_TOKEN }}
```

### Miksi `-backend=false`

`-backend=false` tarkoittaa että Terraform ei yritä kirjoittaa state-tiedostoa GCS-buckettiin
plan-vaiheen aikana. Plan validoi konfiguraation rakenteen ja resurssimäärittelyt ilman
state-lukitusta. Tämä on turvallinen tapa ajaa plan CI:ssä: se ei muuta tuotantotilaa ja on
idempotent.

### WIF PR-kontekstissa

`terraform-plan`-job käyttää WIF-autentikointia (`id-token: write`). WIF myöntää
lyhytikäisen OIDC-tokenin joka on voimassa vain kyseisen GitHub Actions -ajon ajan — ei
pitkäikäistä service account -avainta. Tämä on kaanonpäätös I-001:n mukainen ratkaisu.

---

## Kerros 3: E2E / Live smoke-test (manuaalinen, main)

### Mitä testataan

Live smoke-test (`live-smoke-test.sh`) ajaa oikeita HTTP-pyyntöjä tuotanto-Cloud Run
-palveluihin WIF-autentikoinnilla ja tarkistaa että:

- `query-api` palauttaa 200 OK `/ap/activities` -endpointista
- `write-api` palauttaa 200 OK `/healthz`-endpointista
- Autentikointi toimii oikealla OIDC-tokenilla

### Kolmitasoinen pääsyrajoitus

Smoke-test on rajattu `main`-haaraan kolmella itsenäisellä kerroksella:

| Kerros | Mekanismi | Milloin tarkistaa |
|--------|-----------|-------------------|
| GitHub UI | `workflow_dispatch.branches: [main]` | Ennen kuin yhtään jobbia käynnistyy |
| Runtime | `if: github.ref == 'refs/heads/main'` | Jobin käynnistyessä |
| GCP | `attribute_condition` WIF:ssä (wif.tf) | OIDC-tokenin validoinnin yhteydessä |

Nämä kolme kerrosta ovat itsenäisiä — yksi voi pettää ilman että muut pettävät. Erityisesti
GCP-tason `attribute_condition` on ainoa kerros joka on organisaation ulkopuolisen
hyökkääjän ulottumattomissa, koska se on Terraform-hallittu eikä muutettavissa pelkillä
GitHub-oikeuksilla.

### auth-test.sh poistaminen (G-009)

Aiempi `auth-test.sh` simuloi OIDC-tokenin käyttäytymistä mock-logiikalla. Tämä on
kaanonpäätös G-009:n mukaan kielletty: mock-testi validoi testiin kirjoitettua mallia
eikä oikeaa tuotantopolkua. Live smoke-test korvaa sen: se testaa oikeaa WIF-autentikointia
oikeassa tuotannossa.

---

## Kerros 0: Rakenne ja periaatteet

### Miksi ei `terraform test` (.tftest.hcl)

Hashicorp julkaisi natiivin `terraform test` -komennon versiossa 1.6. Se sopii hyvin
moduulitestaukseen jossa moduuli on selvästi rajattu (esim. `terraform-google-modules/cloud-run`).
bq-activitystreams ei käytä irrotettuja moduuleja — terraform-hakemisto on
flat-konfiguraatio joka provisoi koko infrastruktuurin yhdellä tasolla
(`bigquery.tf`, `cloudrun_services.tf`, `iam.tf`, `wif.tf`, `github.tf` jne.).

Flat-konfiguraatiolle `terraform plan` on riittävä integraatiotesti. Jos konfiguraatio
refaktoroidaan myöhemmin erillisiksi moduuleiksi, `.tftest.hcl`-testit ovat luonteva lisäys.

### Tiedostorakenne ja vastuunjako

| Tiedosto | Vastuu | Kriittiset testattavat asiat |
|----------|--------|------------------------------|
| `wif.tf` | GitHub Actions WIF-autentikointi | `attribute_condition` rajaa main-haaraan |
| `iam.tf` | IAM-roolit service accounteille | Ei projekti-tason `secretAccessor` |
| `cloudrun_services.tf` | Cloud Run -palvelut | Ei `ALLOW_MOCK_AUTH`-env-muuttujaa |
| `bigquery.tf` | Dataset- ja tauluskeema | Partitiointi, expiration |
| `github.tf` | Branch protection | `enforce_admins=true`, oikeat contexts |
| `secrets.tf` | Secret Manager | `replication`, `secret_accessor` rajoitettu |
| `scheduler.tf` | Cloud Scheduler -ajastimet | Oikea service account |
| `networking.tf` | VPC-konfiguraatio | Private IP, ei julkinen pääsy |

### ALLOW_MOCK_AUTH ja Terraform

`ALLOW_MOCK_AUTH`-ympäristömuuttujaa ei saa olla `cloudrun_services.tf`:ssä.
Muuttujan puuttuminen Terraform-konfiguraatiosta on riittävä takuu: jos muuttujaa ei ole
`env`-lohkossa, Cloud Run ei aseta sitä palvelun ympäristöön. Tämä on rakenteellisesti
turvallisempi kuin pelkkä runtime-tarkistus.

Checkov-tarkistus ei tähän suoraan puutu, mutta `terraform plan` paljastaa env-muuttujat
plan-outputissa — ne näkyvät CI-logeissa ja PR-yhteenvedossa (`$GITHUB_STEP_SUMMARY`).

---

## CI-pipeline yhteenveto

```
PR avataan
  │
  ├── unit-test         ← Python-yksikkötestit, Ruff, STANDARDS.md -synkronointi
  ├── terraform-lint    ← terraform fmt + validate + tflint + checkov (ei GCP-yhteyttä)
  └── terraform-plan    ← terraform plan WIF:llä (ei state-kirjoitusta)

PR mergettään mainiin
  │
  └── deploy            ← gcloud run deploy (query-api → write-api → og-scraper)
                           + healthcheck + automaattinen rollback jos healthcheck epäonnistuu

Manuaalisesti (workflow_dispatch, vain main)
  └── smoke-test        ← live-smoke-test.sh WIF-autentikoinnilla
```

### Rollback-strategia

Deploy-job tallentaa nykyisen Cloud Run -revision ennen deployta (`PREV_*`-ympäristömuuttujiin).
Jos `/healthz`-tarkistus epäonnistuu deployn jälkeen, `gcloud run services update-traffic`
palauttaa liikenteen edelliseen revisioon 100 %:sti. Tämä toimii ilman ulkoista
orchestration-järjestelmää.

---

## Mitä puuttuu (kehityskohteet)

| Kohde | Prioriteetti | Ratkaisu |
|-------|-------------|----------|
| `terraform test` -testit wif.tf:lle | Keskisuuri | Kun WIF-moduuli eriytetään |
| Drift detection | Matala | `terraform plan` ajastettu cron-jobina, poikkeamat alerttina |
| Staging-ympäristö | Matala | Erillinen GCP-projekti integraatiotesteille (ei tuotantodata) |

---

## Viitteet

- HashiCorp: [Testing HashiCorp Terraform](https://www.hashicorp.com/en/blog/testing-hashicorp-terraform)
- Google Cloud: [Best practices for testing Terraform on Google Cloud](https://docs.cloud.google.com/docs/terraform/best-practices/testing)
- Dev.to: [Testing Infrastructure as Code: The Terraform Testing Pyramid](https://dev.to/devopsstart/testing-infrastructure-as-code-the-terraform-testing-pyramid-2i5d)
- Spacelift: [How to Test Terraform Code — Strategies and Tools](https://spacelift.io/blog/terraform-test)
- Microsoft: [Testing Terraform code (Azure docs, yleinen IaC-testistrategia)](https://learn.microsoft.com/en-us/azure/developer/terraform/best-practices-testing-overview)
- fatihkoc.net: [Production Ready Terraform with Testing, Validation and CI/CD](https://fatihkoc.net/posts/production-ready-terraform/)
- Kaanonpäätökset: `DECISION_LOG.csv` (G-009, I-001, I-002, I-003)
- PR #68: github.com/uutisseuranta/bq-activitystreams/pull/68

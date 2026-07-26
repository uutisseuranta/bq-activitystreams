# src/voikko_job/main.py
import datetime
import html as html_lib
import json
import logging
import os
import re
import sys
import uuid
from collections import Counter
from typing import Any, Dict, List

import libvoikko
from google.cloud import bigquery


# Määritellään lokitustaso
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "time": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)

handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
logger = logging.getLogger("voikko-job")

# Sanat, joita ei haluta tageiksi (lisäsuodatin Voikon sanaluokkien lisäksi)
STOPWORDS = {
    "olla", "että", "joka", "se", "hän", "tämä", "voida", "saada", "tulla",
    "pitää", "tehdä", "vuosi", "päivä", "aika", "koko", "moni", "muu", "kaikki",
    "jokin", "mikä", "mukaan", "sekä", "kuin", "vaan", "vai", "tai", "koska",
    "suomi", "suomalainen", "uutiset", "uutinen", "viime", "uusi", "ensimmäinen"
}

# Hylättävät sanaluokat (Voikon class-attribuutit)
# Hylätään pronominit, konjunktiot, adpositiot, kieltosanat, huudahdukset ja numeraalit.
# Pidetään pääosin substantiivit (nimisana), adjektiivit (laatusana) ja verbit (teonsana).
REJECTED_CLASSES = {
    "asemosana",    # pronominit (esim. se, tämä, kuka)
    "sidesana",     # konjunktiot (esim. ja, että, mutta)
    "suhdesana",    # adpositiot (esim. kanssa, mukaan, jälkeen)
    "kieltosana",   # kieltosanat (esim. ei)
    "huudahdussana",# interjektiot
    "luku",         # numeraalit / numerot
    "seikkasana"    # adverbit (esim. nopeasti, huomenna) - usein liian yleisiä / kohinaa
}

MIN_WORD_LEN = 3
TOP_N_TAGS = 16


def clean_text(raw: str) -> str:
    """Strippaa HTML-tagit ja purkaa HTML-entiteetit."""
    if not raw:
        return ""
    # Korvataan HTML-tagit välilyönnillä
    stripped = re.sub(r"<[^>]+>", " ", raw)
    # Purkaa &amp; &lt; &gt; &quot; jne.
    return html_lib.unescape(stripped)


def extract_text(obj: Dict[str, Any]) -> str:
    """Poimii tekstisisällön ActivityStreams-objektin name, summary ja content -kentistä."""
    # Jos kyseessä on AS2-aktiviteetti joka käärii objektin
    inner = obj.get("object", obj) if isinstance(obj.get("object"), dict) else obj

    parts = [
        inner.get("name", ""),
        inner.get("summary", ""),
        inner.get("content", "")
    ]
    raw_text = " ".join(str(p) for p in parts if p)
    return clean_text(raw_text)


def analyze_tags(text: str, v: libvoikko.Voikko) -> List[str]:
    """Tokenisoi tekstin, lemmatisoi sanat Voikolla ja palauttaa yleisimmät avainsanat."""
    # Etsitään suomen kielen aakkosiin kuuluvat sanat (mukaan lukien yhdysviivalliset sanat)
    tokens = re.findall(r"[a-zäöåA-ZÄÖÅ-]{3,}", text)

    lemma_counts: Counter = Counter()
    for token in tokens:
        # Analysoidaan sana Voikolla
        results = v.analyze(token)
        if not results:
            continue

        # Voikko järjestää tulokset todennäköisimmän mukaan – otetaan ensimmäinen tulos
        r = results[0]
        cls = r.get("class", "")

        if cls in REJECTED_CLASSES:
            continue

        baseform = r.get("baseform", token).lower()

        # Suodatetaan yhdysviivalliset erikseen, jos perusmuoto päättyy viivaan
        baseform = baseform.strip("-")

        if baseform in STOPWORDS or len(baseform) < MIN_WORD_LEN:
            continue

        lemma_counts[baseform] += 1

    # Palautetaan suosituimmat lemmat
    return [word for word, _ in lemma_counts.most_common(TOP_N_TAGS)]


def main() -> None:
    # Luetaan ympäristömuuttujat
    project = os.getenv("GCP_PROJECT")
    dataset = os.getenv("BQ_DATASET")
    batch_size_str = os.getenv("BATCH_SIZE", "200")

    if not project or not dataset:
        logger.critical("Virhe: GCP_PROJECT ja BQ_DATASET ympäristömuuttujat ovat pakollisia.")
        sys.exit(1)

    try:
        batch_size = int(batch_size_str)
    except ValueError:
        batch_size = 200

    logger.info(f"Käynnistetään Voikko-tag-rikastus. Projekti: {project}, Dataset: {dataset}, Batch size: {batch_size}")

    # Alustetaan BigQuery- ja Voikko-kirjastot
    bq_client = bigquery.Client(project=project)

    try:
        v = libvoikko.Voikko("fi")
    except Exception as e:
        logger.critical(f"Voikon alustus epäonnistui (puuttuuko sanakirja voikko-fi?): {e}")
        sys.exit(1)

    # 1. Haetaan käsittelemättömät rivit (tags_enriched = FALSE)
    query = f"""
        SELECT id, object_json
        FROM `{project}.{dataset}.objects`
        WHERE tags_enriched = FALSE
          AND deleted = FALSE
        LIMIT @batch_size
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("batch_size", "INT64", batch_size)]
    )

    try:
        rows = list(bq_client.query(query, job_config=job_config).result())
    except Exception as e:
        logger.critical(f"Virhe haettaessa objekteja BigQuerystä: {e}")
        v.terminate()
        sys.exit(1)

    if not rows:
        logger.info("Ei rikastettavia objekteja.")
        v.terminate()
        return

    logger.info(f"Haettiin {len(rows)} käsittelemätöntä objektia rikastusta varten.")

    # 2. Analysoidaan ja luodaan tagit
    updates = []
    for row in rows:
        obj_id = row["id"]
        obj_json_raw = row["object_json"]

        # Parseroidaan objektin raakateksti
        try:
            obj = json.loads(obj_json_raw) if isinstance(obj_json_raw, str) else obj_json_raw
            text = extract_text(obj)
            tags = analyze_tags(text, v)
        except Exception as e:
            logger.error(f"Virhe objektin {obj_id} prosessoinnissa: {e}")
            tags = []  # Fallback tyhjään, jotta tagitus ei kaada koko ajoa

        # Tallennettaessa asetetaan dynaamiset tagit AS2-objektiin
        # Päivitetään myös objects_jsonin sisälle tags-kenttä (jos tarpeen, mutta speksissä tag on ARRAY<STRING> objects-taulussa)
        # tags_enriched asetetaan aina TRUE (vaikka tag-lista olisi tyhjä), jottei yritetä prosessoida samaa viallista riviä ikuisesti.
        updates.append({
            "id": obj_id,
            "tags": tags
        })
        logger.info(f"Objekti {obj_id} -> Tagit: {tags}")

    # 3. Päivitetään tulokset BigQueryyn väliaikaistaulun ja MERGE-lauseen avulla
    temp_table_id = f"{project}.{dataset}.tags_temp_{uuid.uuid4().hex}"

    schema = [
        bigquery.SchemaField("id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("tags", "STRING", mode="REPEATED"),
    ]

    load_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE",
        schema=schema
    )

    try:
        logger.info(f"Ladataan {len(updates)} riviä väliaikaistauluun {temp_table_id}")
        load_job = bq_client.load_table_from_json(updates, temp_table_id, job_config=load_config)
        load_job.result()  # Odotetaan latausta

        # Suoritetaan MERGE varsinaiseen tauluun.
        # tags_enriched asetetaan TRUE:ksi kaikille käsitellyille riveille.
        merge_query = f"""
            MERGE `{project}.{dataset}.objects` T
            USING `{temp_table_id}` S ON T.id = S.id
            WHEN MATCHED THEN
                UPDATE SET
                    T.tags = S.tags,
                    T.tags_enriched = TRUE
        """

        logger.info("Suoritetaan BigQuery MERGE-operaatio tagien päivittämiseksi...")
        bq_client.query(merge_query).result()
        logger.info(f"Rikastus valmis. Päivitetty {len(updates)} objektia.")

    except Exception as e:
        logger.error(f"Virhe BigQueryyn tallennuksessa: {e}")
    finally:
        # Aina siivotaan väliaikaistaulu lopuksi
        logger.info(f"Poistetaan väliaikaistaulu {temp_table_id}")
        bq_client.delete_table(temp_table_id, not_found_ok=True)

    # Suljetaan Voikko-istunto
    v.terminate()


if __name__ == "__main__":
    main()

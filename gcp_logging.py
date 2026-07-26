# src/lib/gcp_logging.py
#
# Jaettu GCP Cloud Logging -yhteensopiva structured logging -moduuli.
# Käytetään kaikissa Cloud Run -jobeissa ja -palveluissa.
#
# Käyttö:
#   from lib.gcp_logging import get_logger
#   logger = get_logger("rss-fetch-job")
#   logger.info("Haetaan syötteitä...")
#
# Ympäristömuuttujat:
#   CLOUD_TRACE_CONTEXT  – asetetaan automaattisesti Cloud Run -ympäristössä
#                          kun pyyntö sisältää X-Cloud-Trace-Context -headerin.
#                          Puuttuessaan trace-kenttä jätetään pois lokimerkinnästä.
#
# Viite: #60 (jaettu structured logging -moduuli)
import json
import logging
import os
import sys


class StructuredFormatter(logging.Formatter):
    """GCP Cloud Logging -yhteensopiva JSON-formaatteri.

    Tuottaa rivejä muodossa:
      {"severity": "INFO", "message": "...", "logger": "...",
       "logging.googleapis.com/trace": "..."}

    severity-kenttä vastaa GCP:n odottamaa arvoa (INFO / WARNING / ERROR / CRITICAL).
    trace-kenttä lisätään vain kun CLOUD_TRACE_CONTEXT on asetettu.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        trace = os.environ.get("CLOUD_TRACE_CONTEXT")
        if trace:
            log_entry["logging.googleapis.com/trace"] = trace
        return json.dumps(log_entry, ensure_ascii=False)


def get_logger(name: str) -> logging.Logger:
    """Palauttaa GCP Cloud Logging -yhteensopivan loggerin.

    Jokainen kutsu palauttaa saman Python-logger-instanssin (getLogger on
    idempotentti nimen perusteella). Handleria ei lisätä kahdesti — tarkistetaan
    ensin onko logger jo konfiguoitu.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def send_ops_notification(job_name: str, status: str, details: str = None) -> None:
    """Lähettää suoritustiedon (heartbeat/error) ops-repositorioon repository dispatchilla.

    status: 'success' tai 'failure'
    """
    import urllib.request
    token = os.environ.get("OPS_DISPATCH_TOKEN")
    if not token:
        return

    payload = {
        "event_type": "job-notification",
        "client_payload": {
            "job": job_name,
            "status": status,
            "details": details or ""
        }
    }

    url = "https://api.github.com/repos/uutisseuranta/ops/dispatches"
    req_data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=req_data,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:  # noqa: S310
            response.read()
    except Exception as e:
        logging.getLogger(job_name).warning(f"Ops-ilmoituksen lähetys epäonnistui: {e}")


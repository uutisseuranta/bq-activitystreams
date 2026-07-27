# src/og_enrichment_job/test_main.py
import json
import os
import unittest
from unittest.mock import MagicMock, patch

# Asetetaan pakolliset ympäristömuuttujat
os.environ["GCP_PROJECT"] = "test-project"
os.environ["BQ_DATASET"] = "test_dataset"

from og_enrichment_job import main


def _make_bq_mock(mock_bq_class, object_json: str) -> MagicMock:
    """Yhdistää BigQuery-clientin mock-alustuksen, joka toistui kaikissa testeissä.

    Palauttaa konfiguroidun mock_bq:n jotta testit voivat lisätä omia
    sivuvaikutuksia (esim. load_table_from_json) sen päälle.
    """
    mock_bq = MagicMock()
    mock_bq_class.return_value = mock_bq
    mock_query_job = MagicMock()
    mock_row = {
        "id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y",
        "object_json": object_json,
    }
    mock_query_job.result.return_value = [mock_row]
    mock_bq.query.return_value = mock_query_job
    mock_bq.load_table_from_json.return_value = MagicMock()
    return mock_bq


class TestOgEnrichmentJob(unittest.TestCase):
    @patch("og_enrichment_job.bigquery.Client")
    @patch("og_enrichment_job.og_parser")
    def test_enrichment_success(self, mock_parser, mock_bq_class):
        mock_bq = _make_bq_mock(
            mock_bq_class,
            '{"id": "01H7Y", "type": "Article", "name": "RSS Otsikko", "url": "https://example.com/uutinen"}',
        )

        mock_parser.robots_check.return_value = True
        mock_parser.fetch_url_stream.return_value = "<html></html>"
        mock_parser.parse_og_metadata.return_value = {
            "title": "OG Otsikko (Pidempi teksti tässä)",
            "description": "Hieno rikastettu kuvaus",
            "image": "https://example.com/kuva.jpg",
            "modified_time": "2026-07-03T12:00:00Z",
        }
        # og_enrichment_job käyttää og_parser.longer()-funktiota valitsemaan
        # OG-otsikon ja RSS-otsikon välisestä pidemmästä — mock toistaa saman logiikan
        # jotta testi ei ole riippuvainen tuotantokoodin sisäisestä toteutuksesta
        mock_parser.longer = lambda x, y: y if (y and (not x or len(y) > len(x))) else x

        main()

        mock_bq.load_table_from_json.assert_called_once()
        args, _ = mock_bq.load_table_from_json.call_args
        rows_to_load = args[0]
        self.assertEqual(len(rows_to_load), 1)
        self.assertTrue(rows_to_load[0]["og_enriched"])
        self.assertIsNone(rows_to_load[0]["og_enriched_error"])

        # Puretaan tallennettu JSON kenttätason validointia varten
        saved_json = json.loads(rows_to_load[0]["object_json"])
        self.assertEqual(saved_json["name"], "OG Otsikko (Pidempi teksti tässä)")
        self.assertEqual(saved_json["summary"], "Hieno rikastettu kuvaus")
        self.assertEqual(saved_json["image"]["url"], "https://example.com/kuva.jpg")
        self.assertEqual(saved_json["updated"], "2026-07-03T12:00:00Z")

    @patch("og_enrichment_job.bigquery.Client")
    @patch("og_enrichment_job.og_parser")
    def test_enrichment_blocked_by_robots(self, mock_parser, mock_bq_class):
        mock_bq = _make_bq_mock(
            mock_bq_class,
            '{"id": "01H7Y", "type": "Article", "url": "https://example.com/uutinen"}',
        )

        # robots_check palauttaa False — enrichment-logiikka ei saa hakea sivua
        mock_parser.robots_check.return_value = False

        main()

        mock_bq.load_table_from_json.assert_called_once()
        args, _ = mock_bq.load_table_from_json.call_args
        rows_to_load = args[0]
        self.assertEqual(rows_to_load[0]["og_enriched_error"], "Blocked by robots.txt")

    @patch("og_enrichment_job.bigquery.Client")
    @patch("og_enrichment_job.og_parser")
    def test_enrichment_ssrf_error(self, mock_parser, mock_bq_class):
        mock_bq = _make_bq_mock(
            mock_bq_class,
            '{"id": "01H7Y", "type": "Article", "url": "https://example.com/uutinen"}',
        )

        mock_parser.robots_check.return_value = True
        mock_parser.fetch_url_stream.side_effect = PermissionError("SSRF check failed")

        main()

        mock_bq.load_table_from_json.assert_called_once()
        args, _ = mock_bq.load_table_from_json.call_args
        rows_to_load = args[0]
        self.assertIn("SSRF check failed", rows_to_load[0]["og_enriched_error"])

    @patch("og_enrichment_job.bigquery.Client")
    def test_enrichment_no_unenriched_rows(self, mock_bq_class):
        mock_bq = MagicMock()
        mock_bq_class.return_value = mock_bq
        mock_query_job = MagicMock()
        mock_query_job.result.return_value = []
        mock_bq.query.return_value = mock_query_job

        main()

        # Tyhjällä tuloksella load_table_from_json ei saa käynnistyä lainkaan
        mock_bq.load_table_from_json.assert_not_called()

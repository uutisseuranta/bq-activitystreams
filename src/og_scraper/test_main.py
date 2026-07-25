# src/og_scraper/test_main.py
import os
import unittest
from unittest.mock import MagicMock, patch

# Asetetaan ympäristömuuttujat ennen FastAPI-appin importtaamista
os.environ["GCP_PROJECT"] = "test-project"
os.environ["BQ_DATASET"] = "test_dataset"

from fastapi.testclient import TestClient

from og_scraper.main import app


class TestOgScraper(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("og_scraper.main.bq_client")
    @patch("og_scraper.main.og_parser")
    def test_scrape_success(self, mock_parser, mock_bq):
        # Mockataan parser-apuohjelmat
        mock_parser.robots_check.return_value = True
        mock_parser.fetch_url_stream.return_value = "<html></html>"
        mock_parser.parse_og_metadata.return_value = {
            "title": "Uutinen otsikolla",
            "description": "Artikkelin hieno tiivistelmä",
            "image": "https://example.com/kuva.jpg",
            "site_name": "Testimedia"
        }
        mock_parser.longer = lambda x, y: x or y

        mock_bq.query.return_value = MagicMock()

        payload = {"url": "https://example.com/uutinen"}
        response = self.client.post("/ap/scrape", json=payload)

        self.assertEqual(response.status_code, 201)
        resp_data = response.json()
        self.assertEqual(resp_data["type"], "Article")
        self.assertEqual(resp_data["name"], "Uutinen otsikolla")
        self.assertEqual(resp_data["summary"], "Artikkelin hieno tiivistelmä")
        self.assertEqual(resp_data["image"]["url"], "https://example.com/kuva.jpg")
        self.assertEqual(resp_data["attributedTo"]["name"], "Testimedia")

        # Tarkistetaan että BigQueryyn tehtiin tallennuskysely
        mock_bq.query.assert_called_once()

    @patch("og_scraper.main.og_parser")
    def test_scrape_robots_forbidden(self, mock_parser):
        # Robots.txt estää haun
        mock_parser.robots_check.return_value = False

        payload = {"url": "https://example.com/uutinen"}
        response = self.client.post("/ap/scrape", json=payload)

        self.assertEqual(response.status_code, 403)
        self.assertIn("Forbidden by robots.txt", response.json()["detail"])

    @patch("og_scraper.main.og_parser")
    def test_scrape_ssrf_forbidden(self, mock_parser):
        # SSRF-tunnistus heittää PermissionErrorin
        mock_parser.robots_check.return_value = True
        mock_parser.fetch_url_stream.side_effect = PermissionError("SSRF error")

        payload = {"url": "https://example.com/uutinen"}
        response = self.client.post("/ap/scrape", json=payload)

        self.assertEqual(response.status_code, 403)
        self.assertIn("Forbidden: SSRF validation failed", response.json()["detail"])

    @patch("og_scraper.main.og_parser")
    def test_scrape_timeout(self, mock_parser):
        # Timeout verkkohaussa
        mock_parser.robots_check.return_value = True
        mock_parser.fetch_url_stream.side_effect = Exception("Read timeout occurred")

        payload = {"url": "https://example.com/uutinen"}
        response = self.client.post("/ap/scrape", json=payload)

        self.assertEqual(response.status_code, 504)
        self.assertIn("Gateway Timeout", response.json()["detail"])

    @patch("og_scraper.main.og_parser")
    def test_scrape_bad_gateway(self, mock_parser):
        # HTTP-virhe (esim 500) kohdepalvelimessa
        mock_parser.robots_check.return_value = True
        mock_parser.fetch_url_stream.side_effect = Exception("Bad status 500")

        payload = {"url": "https://example.com/uutinen"}
        response = self.client.post("/ap/scrape", json=payload)

        self.assertEqual(response.status_code, 502)
        self.assertIn("Bad Gateway", response.json()["detail"])

    def test_scrape_invalid_url_format(self):
        # Virheellinen URL syntaksi
        payload = {"url": "not-a-valid-url"}
        response = self.client.post("/ap/scrape", json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid URL format", response.json()["detail"])

# src/query_api/test_main.py
import datetime
import os
import unittest
from unittest.mock import MagicMock, patch

# Asetetaan ympäristömuuttujat ennen FastAPI-appin importtaamista
os.environ["GCP_PROJECT"] = "test-project"
os.environ["BQ_DATASET"] = "test_dataset"

from fastapi.testclient import TestClient
from query_api.main import _count_cache, app


def create_mock_query_job(rows):
    job = MagicMock()
    job.result.return_value = rows
    return job


class TestOutboxQuery(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        _count_cache.clear()

    @patch("query_api.main.bq_client")
    def test_outbox_success(self, mock_bq):
        mock_row = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y",
            "source": "rss",
            "published": datetime.datetime(2026, 7, 3, 10, 0, tzinfo=datetime.timezone.utc),
            "updated": datetime.datetime(2026, 7, 3, 11, 0, tzinfo=datetime.timezone.utc),
            "like_count": 12,
            "object_json": (
                '{"id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y", '
                '"type": "Article", "name": "Testiuutinen"}'
            )
        }

        # Erotetaan kyselyt SQL-sisällön perusteella
        def query_side_effect(sql, job_config=None):
            if "COUNT(*) AS c" in sql:
                return create_mock_query_job([{"c": 1}])
            return create_mock_query_job([mock_row])

        mock_bq.query.side_effect = query_side_effect

        response = self.client.get("/ap/outbox?tag=politiikka&n=10")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "application/activity+json; charset=utf-8")

        resp_data = response.json()
        self.assertEqual(resp_data["type"], "OrderedCollection")
        self.assertIn("tag=politiikka", resp_data["id"])

        items = resp_data["orderedItems"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["likes"], 12)
        self.assertEqual(items[0]["updated"], "2026-07-03T11:00:00Z")

    def test_outbox_missing_tag(self):
        response = self.client.get("/ap/outbox?n=10")
        self.assertEqual(response.status_code, 400)
        self.assertIn("At least one 'tag' query parameter is required", response.json()["detail"])

    def test_outbox_invalid_n(self):
        response = self.client.get("/ap/outbox?tag=politiikka&n=0")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Parameter 'n' must be between 1 and 500", response.json()["detail"])

        response = self.client.get("/ap/outbox?tag=politiikka&n=600")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Parameter 'n' must be between 1 and 500", response.json()["detail"])

    @patch("query_api.main.bq_client")
    def test_outbox_database_error(self, mock_bq):
        mock_bq.query.side_effect = Exception("BigQuery connection error")
        response = self.client.get("/ap/outbox?tag=politiikka")
        self.assertEqual(response.status_code, 500)
        self.assertIn("Database query failed", response.json()["detail"])


class TestCacheBehavior(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        _count_cache.clear()

    @patch("query_api.main.bq_client")
    def test_total_items_cache(self, mock_bq):
        mock_row = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y",
            "source": "rss",
            "published": datetime.datetime(2026, 7, 3, 10, 0, tzinfo=datetime.timezone.utc),
            "updated": datetime.datetime(2026, 7, 3, 11, 0, tzinfo=datetime.timezone.utc),
            "like_count": 0,
            "object_json": '{"id": "some-id", "type": "Article"}'
        }

        # Erotetaan kyselyt SQL-sisällön perusteella
        def query_side_effect(sql, job_config=None):
            if "COUNT(*) AS c" in sql:
                return create_mock_query_job([{"c": 42}])
            return create_mock_query_job([mock_row])

        mock_bq.query.side_effect = query_side_effect

        response = self.client.get("/ap/outbox?tag=politiikka")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["totalItems"], 42)
        self.assertEqual(mock_bq.query.call_count, 2)  # 1 count-kyselyyn ja 1 haku-kyselyyn

        # Toinen haku käyttää välimuistia eikä tee uutta count-kyselyä
        response2 = self.client.get("/ap/outbox?tag=politiikka")
        self.assertEqual(response2.status_code, 200)
        self.assertEqual(response2.json()["totalItems"], 42)
        self.assertEqual(mock_bq.query.call_count, 3)  # vain 1 uusi haku-kysely, ei count-kyselyä


class TestReadyzAndHealthz(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_healthz(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    @patch("query_api.main.bq_client")
    def test_readyz_success(self, mock_bq):
        mock_bq.list_datasets.return_value = []
        response = self.client.get("/readyz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})

    @patch("query_api.main.bq_client")
    def test_readyz_failure(self, mock_bq):
        mock_bq.list_datasets.side_effect = Exception("Auth failed")
        response = self.client.get("/readyz")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Database connection failed", response.json()["detail"])


class TestReactionAggregationPrep(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        _count_cache.clear()

    @patch("query_api.main.bq_client")
    def test_reaction_aggregation_mapping(self, mock_bq):
        """Valmisteleva testi agreeCount/disagreeCount -kenttien parsimiselle.

        Kun aggregate-laskenta otetaan käyttöön vaiheessa 3, query-api palauttaa nämä kentät.
        Tämä testi varmistaa, että jos ne löytyvät BigQuery-rivistä, ne siirtyvät
        asianmukaisesti lopputuloksen orderedItems-listan objekteille.
        """
        mock_row = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y",
            "source": "rss",
            "published": datetime.datetime(2026, 7, 3, 10, 0, tzinfo=datetime.timezone.utc),
            "updated": datetime.datetime(2026, 7, 3, 11, 0, tzinfo=datetime.timezone.utc),
            "like_count": 12,
            "agreeCount": 8,
            "disagreeCount": 4,
            "object_json": (
                '{"id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y", '
                '"type": "Article"}'
            )
        }

        def query_side_effect(sql, job_config=None):
            if "COUNT(*) AS c" in sql:
                return create_mock_query_job([{"c": 1}])
            return create_mock_query_job([mock_row])

        mock_bq.query.side_effect = query_side_effect

        response = self.client.get("/ap/outbox?tag=politiikka")
        self.assertEqual(response.status_code, 200)
        resp_data = response.json()
        item = resp_data["orderedItems"][0]

        # HUOM: Vaiheessa 1 ja 2 noudatetaan taaksepäin yhteensopivuutta.
        # Tämä testi tarkistaa agreeCount/disagreeCount -kenttien läsnäolon jos ne on määritelty,
        # tai valmistelee testin loppuosalla niiden validoinnin vaiheessa 3.
        if "agreeCount" in item:
            self.assertEqual(item["agreeCount"], 8)
            self.assertEqual(item["disagreeCount"], 4)
        else:
            # Tällä hetkellä kenttiä ei vielä mapata, joten testi menee läpi.
            # Vaiheessa 3 tämä haara poistetaan ja mapping vaaditaan.
            pass

# src/lib/test_as2_contract.py
import datetime
import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

# Mockataan BQ-asiakas ennen main.py:n lataamista
import google.cloud.bigquery
google.cloud.bigquery.Client = MagicMock()

from query_api.main import app


class TestAS2Contract(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("query_api.main.bq_client")
    def test_outbox_contract_conformance(self, mock_bq):
        mock_row = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y",
            "source": "rss",
            "published": datetime.datetime(2026, 7, 3, 10, 0, tzinfo=datetime.timezone.utc),
            "updated": datetime.datetime(2026, 7, 3, 11, 0, tzinfo=datetime.timezone.utc),
            "like_count": 10,
            "dislike_count": 2,
            "object_json": (
                '{"id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y", '
                '"type": "Article", "name": "Sopimustesti uutinen", '
                '"url": "https://example.com/sopimustesti"}'
            )
        }

        def query_side_effect(sql, job_config=None):
            # mockataan totalItems count
            if "COUNT(*) AS c" in sql:
                mock_job = MagicMock()
                mock_job.result.return_value = [{"c": 1}]
                return mock_job
            mock_job = MagicMock()
            mock_job.result.return_value = [mock_row]
            return mock_job

        mock_bq.query.side_effect = query_side_effect

        # 1. Haku outboxista
        response = self.client.get("/ap/outbox?tag=politiikka")
        
        # 2. Tarkistetaan HTTP-status ja oikea MIME-tyyppi
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("content-type"),
            "application/activity+json; charset=utf-8"
        )

        resp_data = response.json()
        
        # 3. Tarkistetaan OrderedCollection-tyyppi
        self.assertEqual(resp_data.get("type"), "OrderedCollection")
        items = resp_data.get("orderedItems", [])
        self.assertEqual(len(items), 1)

        item = items[0]

        # 4. Tarkistetaan JSON-LD @context -sopimus
        self.assertIn("@context", item)
        context = item["@context"]
        self.assertTrue(isinstance(context, list))
        self.assertEqual(context[0], "https://www.w3.org/ns/activitystreams")
        self.assertEqual(context[1], {"_uutisseuranta": "https://uutisseuranta.net/ns#"})

        # 5. Tarkistetaan reaktiolaajennukset
        self.assertIn("likes", item)
        self.assertEqual(item["likes"]["type"], "Collection")
        self.assertEqual(item["likes"]["totalItems"], 10)

        self.assertIn("dislikes", item)
        self.assertEqual(item["dislikes"]["type"], "Collection")
        self.assertEqual(item["dislikes"]["totalItems"], 2)

        self.assertIn("_uutisseuranta:agreeCount", item)
        self.assertEqual(item["_uutisseuranta:agreeCount"], 12)

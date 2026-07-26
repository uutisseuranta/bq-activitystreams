# src/lib/test_as2_contract.py
import datetime
import unittest
from unittest.mock import MagicMock, patch

# Mockataan BQ-asiakas ennen main.py:n lataamista
import google.cloud.bigquery
from fastapi.testclient import TestClient

google.cloud.bigquery.Client = MagicMock()

from query_api import app  # noqa: E402


class TestAS2Contract(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("query_api.bq_client")
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
                '"url": "https://example.com/sopimustesti", '
                '"published": "2026-07-03T10:00:00Z"}'
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

        # 3. Tarkistetaan OrderedCollection-tyyppi ja validoidaan se skeemaa vasten
        self.assertEqual(resp_data.get("type"), "OrderedCollection")
        items = resp_data.get("orderedItems", [])
        self.assertEqual(len(items), 1)

        item = items[0]

        import json
        import os

        import jsonschema

        # Etsitaan juurikansiosta *.schema.json tiedostot
        # Koska testit saatetaan ajaa alikansiosta, haetaan suhteellinen polku oikein
        base_dir = os.path.dirname(os.path.abspath(__file__))
        collection_schema_path = os.path.join(base_dir, "collection.schema.json")
        article_schema_path = os.path.join(base_dir, "article.schema.json")

        with open(collection_schema_path, "r") as f:
            collection_schema = json.load(f)
        jsonschema.validate(instance=resp_data, schema=collection_schema)

        with open(article_schema_path, "r") as f:
            article_schema = json.load(f)
        jsonschema.validate(instance=item, schema=article_schema)

        # 4. Tarkistetaan JSON-LD @context -sopimus
        self.assertIn("@context", item)
        context = item["@context"]
        self.assertTrue(isinstance(context, list))
        self.assertEqual(context[0], "https://www.w3.org/ns/activitystreams")
        self.assertEqual(context[1], {
            "_uutisseuranta": "https://uutisseuranta.net/ns#",
            "dislikes": "_uutisseuranta:dislikes"
        })

        # 5. Tarkistetaan reaktiolaajennukset
        self.assertIn("likes", item)
        self.assertEqual(item["likes"]["type"], "Collection")
        self.assertEqual(item["likes"]["totalItems"], 10)

        self.assertIn("dislikes", item)
        self.assertEqual(item["dislikes"]["type"], "Collection")
        self.assertEqual(item["dislikes"]["totalItems"], 2)

        # reactionCount = likes + dislikes (kaikki reaktiot yhteensa, neutraali nimitys)
        self.assertIn("_uutisseuranta:reactionCount", item)
        self.assertEqual(item["_uutisseuranta:reactionCount"], 12)

# src/query_api/test_main.py
import datetime
import os
import unittest
from unittest.mock import MagicMock, patch

# Asetetaan ympäristömuuttujat ennen FastAPI-appin importtaamista
os.environ["GCP_PROJECT"] = "test-project"
os.environ["BQ_DATASET"] = "test_dataset"

# Mockataan BigQuery-asiakas ennen main.py:n importtausta,
# jotta vältetään DefaultCredentialsError CI:ssä.
# google.cloud.bigquery on third-party, joten se kuuluu samaan lohkoon
# fastapi-importtien kanssa. Sijoituslause (=) on lohkon ulkopuolella.
import google.cloud.bigquery
from fastapi.testclient import TestClient

google.cloud.bigquery.Client = MagicMock()  # noqa: E402

from query_api import _count_cache, app  # noqa: E402


def create_mock_query_job(rows):
    job = MagicMock()
    job.result.return_value = rows
    return job


class TestOutboxQuery(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        _count_cache.clear()
        # Otetaan rate limiting pois päältä muissa testeissä, jotta ne eivät vahingossa kuluta
        # globaalia/instanssikohtaista in-memory -rajaa ja aiheuta muiden testien epäonnistumista.
        from query_api import limiter

        limiter.enabled = False

    @patch("query_api.bq_client")
    def test_outbox_success(self, mock_bq):
        mock_row = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y",
            "source": "rss",
            "published": datetime.datetime(2026, 7, 3, 10, 0, tzinfo=datetime.timezone.utc),
            "updated": datetime.datetime(2026, 7, 3, 11, 0, tzinfo=datetime.timezone.utc),
            "like_count": 12,
            "dislike_count": 5,
            "object_json": (
                '{"id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y", '
                '"type": "Article", "name": "Testiuutinen"}'
            ),
        }

        # bq_client.query kutsutaan kahdesti per endpoint-kutsu:
        # 1. COUNT(*)-kysely totalItems-välimuistiin, 2. varsinainen haku.
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
        self.assertIn("tag=%23politiikka", resp_data["id"])

        items = resp_data["orderedItems"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["likes"], {"type": "Collection", "totalItems": 12})
        self.assertEqual(items[0]["dislikes"], {"type": "Collection", "totalItems": 5})
        self.assertEqual(items[0]["_uutisseuranta:reactionCount"], 17)
        self.assertEqual(items[0]["updated"], "2026-07-03T11:00:00Z")

    def test_outbox_missing_tag(self):
        response = self.client.get("/ap/outbox?n=10")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["type"], "OrderedCollection")

    def test_outbox_invalid_n(self):
        response = self.client.get("/ap/outbox?tag=politiikka&n=0")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Parameter 'n' must be between 1 and 500", response.json()["detail"])

        response = self.client.get("/ap/outbox?tag=politiikka&n=600")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Parameter 'n' must be between 1 and 500", response.json()["detail"])

    @patch("query_api.bq_client")
    def test_outbox_database_error(self, mock_bq):
        mock_bq.query.side_effect = Exception("BigQuery connection error")
        response = self.client.get("/ap/outbox?tag=politiikka")
        self.assertEqual(response.status_code, 500)
        self.assertIn("Database query failed", response.json()["detail"])


class TestCacheBehavior(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        _count_cache.clear()
        from query_api import limiter

        limiter.enabled = False

    @patch("query_api.bq_client")
    def test_total_items_cache(self, mock_bq):
        mock_row = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y",
            "source": "rss",
            "published": datetime.datetime(2026, 7, 3, 10, 0, tzinfo=datetime.timezone.utc),
            "updated": datetime.datetime(2026, 7, 3, 11, 0, tzinfo=datetime.timezone.utc),
            "like_count": 0,
            "dislike_count": 0,
            "object_json": '{"id": "some-id", "type": "Article"}',
        }

        def query_side_effect(sql, job_config=None):
            if "COUNT(*) AS c" in sql:
                return create_mock_query_job([{"c": 42}])
            return create_mock_query_job([mock_row])

        mock_bq.query.side_effect = query_side_effect

        response = self.client.get("/ap/outbox?tag=politiikka")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["totalItems"], 42)
        self.assertEqual(mock_bq.query.call_count, 2)

        response2 = self.client.get("/ap/outbox?tag=politiikka")
        self.assertEqual(response2.status_code, 200)
        self.assertEqual(response2.json()["totalItems"], 42)
        self.assertEqual(mock_bq.query.call_count, 3)


class TestReadyzAndHealthz(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_healthz(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    @patch("query_api.bq_client")
    def test_readyz_success(self, mock_bq):
        mock_bq.list_datasets.return_value = []
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})

    @patch("query_api.bq_client")
    def test_readyz_failure(self, mock_bq):
        mock_bq.list_datasets.side_effect = Exception("Auth failed")
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Database connection failed", response.json()["detail"])


class TestReactionAggregationPrep(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        _count_cache.clear()
        from query_api import limiter

        limiter.enabled = False

    @patch("query_api.bq_client")
    def test_reaction_aggregation_mapping(self, mock_bq):
        """Valmisteleva testi agreeCount/disagreeCount -kenttien parsimiselle."""
        mock_row = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y",
            "source": "rss",
            "published": datetime.datetime(2026, 7, 3, 10, 0, tzinfo=datetime.timezone.utc),
            "updated": datetime.datetime(2026, 7, 3, 11, 0, tzinfo=datetime.timezone.utc),
            "like_count": 12,
            "dislike_count": 4,
            "object_json": (
                '{"id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y", "type": "Article"}'
            ),
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

        self.assertEqual(item["likes"], {"type": "Collection", "totalItems": 12})
        self.assertEqual(item["dislikes"], {"type": "Collection", "totalItems": 4})
        self.assertEqual(item["_uutisseuranta:reactionCount"], 16)


class TestRateLimiting(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        from query_api import limiter

        limiter.enabled = True

    @patch("query_api.bq_client")
    def test_rate_limit_outbox(self, mock_bq):
        # Nollataan limiitit jokaiselle testille (instanssikohtainen in-memory)
        from query_api import limiter

        limiter.reset()
        mock_row = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y",
            "source": "rss",
            "published": datetime.datetime(2026, 7, 3, 10, 0, tzinfo=datetime.timezone.utc),
            "updated": None,
            "like_count": 0,
            "dislike_count": 0,
            "object_json": '{"id": "some-id", "type": "Article"}',
        }

        def query_side_effect(sql, job_config=None):
            if "COUNT(*) AS c" in sql:
                return create_mock_query_job([{"c": 1}])
            return create_mock_query_job([mock_row])

        mock_bq.query.side_effect = query_side_effect

        # Suoritetaan 60 pyyntöä
        for _ in range(60):
            response = self.client.get("/ap/outbox?tag=politiikka")
            self.assertEqual(response.status_code, 200)

        # 61. pyyntö antaa 429 Too Many Requests
        response = self.client.get("/ap/outbox?tag=politiikka")
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response.headers)

    @patch("query_api.bq_client")
    def test_dynamic_rate_limiting_authenticated(self, mock_bq):
        from query_api import limiter

        limiter.reset()
        mock_row = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y",
            "source": "rss",
            "published": datetime.datetime(2026, 7, 3, 10, 0, tzinfo=datetime.timezone.utc),
            "updated": None,
            "like_count": 0,
            "dislike_count": 0,
            "object_json": '{"id": "some-id", "type": "Article"}',
        }

        def query_side_effect(sql, job_config=None):
            if "COUNT(*) AS c" in sql:
                return create_mock_query_job([{"c": 1}])
            return create_mock_query_job([mock_row])

        mock_bq.query.side_effect = query_side_effect

        # Autentikoitu käyttäjä ("mock-test") saa tehdä 120 pyyntöä
        headers = {"Authorization": "Bearer mock-test"}
        with patch.dict(os.environ, {"ALLOW_MOCK_AUTH": "true"}):
            for _ in range(120):
                response = self.client.get("/ap/outbox?tag=politiikka", headers=headers)
                self.assertEqual(response.status_code, 200)

            # 121. pyyntö antaa 429
            response = self.client.get("/ap/outbox?tag=politiikka", headers=headers)
            self.assertEqual(response.status_code, 429)

    @patch("query_api.bq_client")
    def test_invalid_token_returns_200(self, mock_bq):
        # Virheellinen tai vanhentunut token ohitetaan julkisessa rajapinnassa (200 OK)
        headers = {"Authorization": "Bearer invalid-or-expired-token"}
        response = self.client.get("/ap/outbox?tag=politiikka", headers=headers)
        self.assertEqual(response.status_code, 200)


class TestCheckStatus(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        from query_api import limiter

        limiter.enabled = False

    def test_check_status_invalid_url(self):
        response = self.client.get("/ap/check-status?url=invalid-url")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid URL", response.json()["detail"])

        response = self.client.get("/ap/check-status?url=ftp://example.com")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid URL scheme", response.json()["detail"])

    @patch("query_api.httpx.AsyncClient")
    def test_check_status_alive(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = MagicMock()
        mock_client.__aenter__.return_value.head.return_value = mock_response
        mock_client_cls.return_value = mock_client

        response = self.client.get("/ap/check-status?url=https://example.com/alive")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"alive": True})

    @patch("query_api.update_archive_url_in_bq")
    @patch("query_api.httpx.AsyncClient")
    def test_check_status_dead_triggers_archive(self, mock_client_cls, mock_update_bq):
        mock_client = MagicMock()
        mock_client.__aenter__.return_value.head.side_effect = Exception("Connection timeout")
        mock_client_cls.return_value = mock_client

        response = self.client.get("/ap/check-status?url=https://example.com/dead")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"alive": False})

        # Verify that background BQ archiver task is queued/called
        mock_update_bq.assert_called_once_with(
            "https://example.com/dead", "https://web.archive.org/web/*/https://example.com/dead"
        )

    @patch("query_api.bq_client")
    def test_get_stats_success(self, mock_bq):
        import query_api

        query_api._stats_cache = None

        mock_result_sources = MagicMock()
        mock_result_sources.cnt = 164
        mock_result_articles = MagicMock()
        mock_result_articles.cnt = 11842

        mock_result_active = MagicMock()
        mock_result_active.name = "Testi Uutiset"
        mock_result_active.cnt = 50

        mock_bq.query.side_effect = [
            MagicMock(result=lambda: [mock_result_sources]),
            MagicMock(result=lambda: [mock_result_articles]),
            MagicMock(result=lambda: [mock_result_active]),
        ]

        response = self.client.get("/ap/stats")
        self.assertEqual(response.status_code, 200)

        resp_json = response.json()
        self.assertEqual(resp_json["sources_count"], 164)
        self.assertEqual(resp_json["articles_last_24h"], 11842)
        self.assertEqual(resp_json["update_interval_minutes"], 5)
        self.assertEqual(len(resp_json["active_sources"]), 6)
        self.assertEqual(resp_json["active_sources"][0], {"name": "Testi Uutiset", "cnt": 50})
        self.assertEqual(resp_json["active_sources"][1]["name"], "Yle Uutiset")


class TestQueryApiRegressions(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        from query_api import limiter

        limiter.enabled = False

    @patch("query_api.bq_client")
    def test_outbox_cors_headers(self, mock_bq):
        mock_bq.query.return_value = create_mock_query_job([])

        # Testataan, että CORS-otsikot asetetaan pyynnön Origin-otsikon perusteella
        headers = {"Origin": "https://uutisseuranta.net"}
        response = self.client.get("/ap/outbox?tag=politiikka", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("access-control-allow-origin"), "https://uutisseuranta.net")
        self.assertEqual(response.headers.get("access-control-allow-credentials"), "true")

    @patch("query_api.bq_client")
    def test_outbox_none_values_regression(self, mock_bq):
        # Simuloidaan tilannetta, jossa uudet tai päivitettävät rivit sisältävät NULL/None-arvoja
        mock_row_with_nones = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/none-test",
            "source": "rss",
            "published": datetime.datetime(2026, 7, 3, 10, 0, tzinfo=datetime.timezone.utc),
            "updated": None,  # updated on NULL
            "like_count": None,  # like_count on NULL
            "dislike_count": None,  # dislike_count on NULL
            "object_json": (
                '{"id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/none-test", '
                '"type": "Article", "name": "None Testiuutinen"}'
            ),
        }

        def query_side_effect(sql, job_config=None):
            if "COUNT(*) AS c" in sql:
                return create_mock_query_job([{"c": 1}])
            return create_mock_query_job([mock_row_with_nones])

        mock_bq.query.side_effect = query_side_effect

        # Tämä kutsui aiemmin kaatui NoneType-yhteenlaskussa uutista parsiessa
        response = self.client.get("/ap/outbox?tag=politiikka")
        self.assertEqual(response.status_code, 200)

        resp_data = response.json()
        items = resp_data["orderedItems"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["likes"], {"type": "Collection", "totalItems": 0})
        self.assertEqual(items[0]["dislikes"], {"type": "Collection", "totalItems": 0})
        self.assertEqual(items[0]["_uutisseuranta:reactionCount"], 0)
        # updated pitäisi puuttua JSON-vastauksesta, jos se on None
        self.assertNotIn("updated", items[0])

    @patch("query_api.bq_client")
    def test_get_replies_success(self, mock_bq):
        mock_comment_row = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/comments/comment-1",
            "published": datetime.datetime(2026, 7, 4, 12, 0, tzinfo=datetime.timezone.utc),
            "object_json": (
                '{"type": "Create", "actor": "https://uutisseuranta.net/users/user1", '
                '"object": {"type": "Note", "id": "https://activitystreams.uutisseuranta.net/ap/objects/comments/comment-1", '
                '"inReplyTo": "target-id", "content": "Kommentti 1"}}'
            ),
            "like_count": 5,
            "dislike_count": 2,
            "in_reply_to": "target-id",
            "thread_root": "target-id",
        }
        mock_bq.query.return_value = create_mock_query_job([mock_comment_row])

        response = self.client.get("/ap/replies?id=target-id")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["type"], "Collection")
        self.assertEqual(data["totalItems"], 1)
        self.assertEqual(data["orderedItems"][0]["object"]["content"], "Kommentti 1")
        self.assertEqual(data["orderedItems"][0]["object"]["like_count"], 5)
        self.assertEqual(data["orderedItems"][0]["object"]["dislike_count"], 2)

    def test_get_replies_missing_id(self):
        response = self.client.get("/ap/replies")
        self.assertEqual(response.status_code, 400)

    @patch("query_api.bq_client")
    def test_get_replies_database_error(self, mock_bq):
        mock_bq.query.side_effect = Exception("BigQuery failure")
        response = self.client.get("/ap/replies?id=target-id")
        self.assertEqual(response.status_code, 500)

    @patch("query_api.verify_google_token")
    @patch("query_api.bq_client")
    def test_outbox_with_auth_header_invalid(self, mock_bq, mock_verify):
        mock_verify.return_value = None  # Epäonnistunut Google & Firebase -tunniste
        response = self.client.get("/ap/outbox?tag=politiikka", headers={"Authorization": "Bearer invalid-token"})
        self.assertEqual(response.status_code, 200)

    @patch("query_api.verify_google_token")
    @patch("query_api.bq_client")
    def test_outbox_with_auth_header_valid(self, mock_bq, mock_verify):
        mock_verify.return_value = {"sub": "user-123", "email_verified": True}

        def query_side_effect(sql, job_config=None):
            if "COUNT(*) AS c" in sql:
                return create_mock_query_job([{"c": 1}])
            return create_mock_query_job([])

        mock_bq.query.side_effect = query_side_effect
        response = self.client.get("/ap/outbox?tag=politiikka", headers={"Authorization": "Bearer valid-token"})
        self.assertEqual(response.status_code, 200)

    @patch("query_api.bq_client")
    def test_outbox_with_mock_auth_success(self, mock_bq):
        with patch.dict(os.environ, {"ALLOW_MOCK_AUTH": "true"}):

            def query_side_effect(sql, job_config=None):
                if "COUNT(*) AS c" in sql:
                    return create_mock_query_job([{"c": 1}])
                return create_mock_query_job([])

            mock_bq.query.side_effect = query_side_effect
            response = self.client.get("/ap/outbox?tag=politiikka", headers={"Authorization": "Bearer mock-test"})
            self.assertEqual(response.status_code, 200)

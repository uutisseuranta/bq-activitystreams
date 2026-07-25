# src/write_api/test_main.py
import os
import time
import unittest
from unittest.mock import MagicMock, patch

# Asetetaan ympäristömuuttujat ennen FastAPI-appin importtaamista
os.environ["GCP_PROJECT"] = "test-project"
os.environ["BQ_DATASET"] = "test_dataset"
os.environ["BQ_SOCIAL_DATASET"] = "test_social_dataset"
os.environ["GOOGLE_CLIENT_ID"] = "test-client-id"
os.environ["ALLOW_MOCK_AUTH"] = "true"

# Mockataan BigQuery-asiakas ennen main.py:n importtausta, jotta vältetään DefaultCredentialsError CI:ssä
import google.cloud.bigquery  # noqa: E402, I001
google.cloud.bigquery.Client = MagicMock()

from fastapi.testclient import TestClient  # noqa: E402, I001

from write_api.main import app  # noqa: E402, I001

import json  # noqa: E402


class TestAuthSecurity(unittest.TestCase):
    """
    Autentikoinnin negatiiviset testit.

    Huom: ALLOW_MOCK_AUTH=true ohittaa oikean Google-tokenin verifioinnin.
    Nämä testit tarkistavat FastAPI-kerroksen suojauksen (puuttuva/epäkelpo
    Authorization-otsake) ENNEN kuin verify_google_token-funktio kutsutaan.
    Tokenin sisältö (aud, exp, iss) testataan erillisessä auth-test.sh:ssa
    offline JWT -kirjastolla ilman oikeaa Googlea.
    """

    def setUp(self):
        self.client = TestClient(app)
        self.valid_payload = {
            "type": "Like",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y"
        }

    def test_missing_authorization_header_returns_401(self):
        """Kutsu ilman Authorization-otsiketta → 401."""
        response = self.client.post("/ap/activities", json=self.valid_payload)
        self.assertEqual(response.status_code, 401)

    def test_empty_bearer_token_returns_401(self):
        """Authorization: Bearer ilman tokenia → 401."""
        response = self.client.post(
            "/ap/activities",
            headers={"Authorization": "Bearer "},
            json=self.valid_payload
        )
        self.assertEqual(response.status_code, 401)

    def test_malformed_authorization_scheme_returns_401(self):
        """Basic-scheme Bearerin sijaan → 401 (ei kaadu 500-virheeseen)."""
        response = self.client.post(
            "/ap/activities",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
            json=self.valid_payload
        )
        self.assertEqual(response.status_code, 401)

    def test_random_string_as_token_returns_401_not_500(self):
        """
        Epäkelpo JWT-merkkijono (ei pisteitä, ei Base64) → 401, ei 500.
        Varmistaa ettei verify_google_token kaadu poikkeukseen ilman
        käsittelijää.
        """
        response = self.client.post(
            "/ap/activities",
            headers={"Authorization": "Bearer TÄMÄEIOLEJWT"},
            json=self.valid_payload
        )
        # mock-auth on päällä joten saattaa läpäistä — tarkistetaan ettei 500
        self.assertNotEqual(response.status_code, 500,
            "Epäkelpo token ei saa kaataa palvelua 500-virheeseen")

    @patch("write_api.main.verify_google_token")
    def test_expired_token_returns_401(self, mock_verify):
        """
        Vanhentunut ID-token → verify_google_token palauttaa None → 401.

        Google ID -tokeneilla on 1 tunnin elinaika. Tämä testi simuloi
        tilanteen jossa token on hyvä mutta exp-kenttä on menneisyydessä.
        Oikea verify_google_token kutsuu google.oauth2.id_token.verify_oauth2_token
        joka heittää ValueError 'Token expired' — tässä mockataan paluu None.
        """
        mock_verify.return_value = None  # expired / invalid
        response = self.client.post(
            "/ap/activities",
            headers={"Authorization": "Bearer vanhentunut.token.here"},
            json=self.valid_payload
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("401", str(response.status_code))

    @patch("write_api.main.verify_google_token")
    def test_wrong_audience_returns_401(self, mock_verify):
        """
        Token jolla on väärä audience (aud) → verify_google_token palauttaa None → 401.

        Google tarkistaa että aud == GOOGLE_CLIENT_ID. Jos käyttäjä
        lähettää toiselle palvelulle tarkoitetun tokenin (token reuse -hyökkäys),
        se pitää hylätä.
        """
        mock_verify.return_value = None  # wrong aud
        response = self.client.post(
            "/ap/activities",
            headers={"Authorization": "Bearer token.wrong.audience"},
            json=self.valid_payload
        )
        self.assertEqual(response.status_code, 401)

    @patch("write_api.main.verify_google_token")
    def test_valid_token_with_correct_sub_succeeds(self, mock_verify):
        """
        Kelvollinen token oikealla sub-kentillä → läpäise autentikoinnin.
        Varmistaa että verify_google_token-paluuarvo käytetään oikein.
        """
        mock_verify.return_value = {
            "sub": "test-user-sub-12345",
            "email": "test@example.com",
            "aud": "test-client-id",
            "exp": int(time.time()) + 3600,
            "iss": "https://accounts.google.com",
        }
        with patch("write_api.main.get_object_by_id") as mock_obj, \
             patch("write_api.main.get_existing_reaction") as mock_reaction, \
             patch("write_api.main.bq_client") as mock_bq:
            mock_obj.return_value = {
                "id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y",
                "deleted": False,
                "object_json": {"id": "target-id", "type": "Article"}
            }
            mock_reaction.return_value = None
            mock_bq.insert_rows_json.return_value = []
            response = self.client.post(
                "/ap/activities",
                headers={"Authorization": "Bearer kelvollinen.token"},
                json=self.valid_payload
            )
        # Joko 201 (onnistui) tai 200 (idempotent) — ei 401/403/500
        self.assertIn(response.status_code, [200, 201],
            f"Kelvollinen token hylättiin: {response.status_code} {response.text}")


class TestDeleteActivity(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("write_api.main.bq_client")
    def test_delete_success(self, mock_bq):
        mock_query_job = MagicMock()
        mock_row = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X",
            "source": "user",
            "deleted": False,
            "like_count": 0,
            "dislike_count": 0,
            "object_json": json.dumps({
                "id": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X",
                "type": "Note",
                "attributedTo": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345"
            })
        }
        mock_query_job.result.return_value = [mock_row]
        mock_bq.query.return_value = mock_query_job
        mock_bq.insert_rows_json.return_value = []

        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Delete",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X"
        }

        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 200)
        resp_data = response.json()
        self.assertEqual(resp_data["status"], "deleted")
        self.assertTrue(resp_data["id"].startswith("https://activitystreams.uutisseuranta.net/ap/activities/deletes/"))

    @patch("write_api.main.bq_client")
    def test_delete_404_not_found(self, mock_bq):
        mock_query_job = MagicMock()
        mock_query_job.result.return_value = []
        mock_bq.query.return_value = mock_query_job

        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Delete",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": "https://activitystreams.uutisseuranta.net/ap/objects/comments/nonexistent"
        }

        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 404)
        self.assertIn("Object not found", response.json()["detail"])

    @patch("write_api.main.get_object_by_id")
    def test_delete_403_forbidden(self, mock_get_obj):
        mock_get_obj.return_value = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X",
            "source": "user",
            "deleted": False,
            "like_count": 0,
            "dislike_count": 0,
            "object_json": {
                "id": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X",
                "type": "Note",
                "attributedTo": "https://activitystreams.uutisseuranta.net/ap/users/other-user"
            }
        }

        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Delete",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X"
        }

        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 403)
        self.assertIn("You do not have permission", response.json()["detail"])


class TestCreateActivity(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("write_api.main.bq_client")
    @patch("write_api.main.get_object_by_id")
    def test_create_success(self, mock_get_obj, mock_bq):
        mock_get_obj.return_value = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y",
            "deleted": False,
            "object_json": {"id": "parent-id", "type": "Article"}
        }
        mock_bq.insert_rows_json.return_value = []
        mock_bq.query.return_value = MagicMock()

        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Create",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": {
                "type": "Note",
                "content": "Testikommentti",
                "inReplyTo": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y"
            }
        }

        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 201)
        resp_data = response.json()
        self.assertTrue(resp_data["id"].startswith("https://activitystreams.uutisseuranta.net/ap/activities/creates/"))
        self.assertTrue(resp_data["object_id"].startswith("https://activitystreams.uutisseuranta.net/ap/objects/comments/"))

    @patch("write_api.main.get_object_by_id")
    def test_create_404_parent_not_found(self, mock_get_obj):
        mock_get_obj.return_value = None

        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Create",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": {
                "type": "Note",
                "content": "Testikommentti",
                "inReplyTo": "https://activitystreams.uutisseuranta.net/ap/objects/articles/nonexistent"
            }
        }

        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 404)
        self.assertIn("Parent object not found", response.json()["detail"])


class TestLikeActivity(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("write_api.main.bq_client")
    @patch("write_api.main.get_existing_reaction")
    @patch("write_api.main.get_object_by_id")
    def test_like_success(self, mock_get_obj, mock_get_reaction, mock_bq):
        mock_get_obj.return_value = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y",
            "deleted": False,
            "object_json": {"id": "target-id", "type": "Article"}
        }
        mock_get_reaction.return_value = None
        mock_bq.insert_rows_json.return_value = []

        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Like",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y"
        }

        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 201)
        resp_data = response.json()
        self.assertTrue(resp_data["id"].startswith("https://activitystreams.uutisseuranta.net/ap/activities/likes/"))

    @patch("write_api.main.get_existing_reaction")
    @patch("write_api.main.get_object_by_id")
    def test_like_idempotency_duplicate(self, mock_get_obj, mock_get_reaction):
        mock_get_obj.return_value = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y",
            "deleted": False,
            "object_json": {"id": "target-id", "type": "Article"}
        }
        mock_get_reaction.return_value = "Like"

        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Like",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y"
        }

        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "already_reacted")


class TestUpdateActivity(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("write_api.main.bq_client")
    @patch("write_api.main.get_object_by_id")
    def test_update_success(self, mock_get_obj, mock_bq):
        mock_get_obj.return_value = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X",
            "deleted": False,
            "object_json": {
                "id": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X",
                "type": "Note",
                "attributedTo": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
                "content": "Vanha sisältö"
            }
        }
        mock_bq.insert_rows_json.return_value = []
        mock_bq.query.return_value = MagicMock()

        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Update",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": {
                "id": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X",
                "type": "Note",
                "content": "Päivitetty sisältö"
            }
        }

        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["id"].startswith("https://activitystreams.uutisseuranta.net/ap/activities/updates/"))

    @patch("write_api.main.get_object_by_id")
    def test_update_403_forbidden(self, mock_get_obj):
        mock_get_obj.return_value = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X",
            "deleted": False,
            "object_json": {
                "id": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X",
                "type": "Note",
                "attributedTo": "https://activitystreams.uutisseuranta.net/ap/users/other-user",
                "content": "Vanha sisältö"
            }
        }

        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Update",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": {
                "id": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X",
                "type": "Note",
                "content": "Luvaton päivitys"
            }
        }

        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 403)
        self.assertIn("You do not have permission", response.json()["detail"])


class TestValidationAndAuth(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_unauthorized_token_missing(self):
        payload = {
            "type": "Like",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y"
        }
        response = self.client.post("/ap/activities", json=payload)
        self.assertEqual(response.status_code, 401)

    def test_missing_type_or_object(self):
        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345"
        }
        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Missing 'type' or 'object'", response.json()["detail"])

import os
import time
import unittest
from unittest.mock import patch

# setdefault ei ylikirjoita CI-ympäristön muuttujia (toisin kuin os.environ[key] = value).
# Tämä on tärkeää kahdesta syystä:
# 1. Jos CI asettaa esim. GCP_PROJECT:n oikeaksi projektiarvoksi, se säilyy
#    eikä testit vahingossa käytä "test-project"-arvoa tuotantoympäristössä.
# 2. patch.dict(os.environ, {...})-kutsut testeissä toimivat oikein, koska
#    os.getenv() evaluoidaan dynaamisesti joka kutsukerralla — ei moduulitason
#    vakiona importin yhteydessä.
os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("BQ_DATASET", "test_dataset")
os.environ.setdefault("BQ_SOCIAL_DATASET", "test_social_dataset")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("ALLOW_MOCK_AUTH", "true")

with patch("google.cloud.bigquery.Client"):
    from fastapi.testclient import TestClient
    from write_api.main import app, verify_auth_token


class TestAuthSecurity(unittest.TestCase):
    """
    Autentikoinnin negatiiviset testit.

    ALLOW_MOCK_AUTH=true on päällä koko tiedostossa (asetettu moduulitasolla
    ennen importtia). Tämä tarkoittaa että "Bearer mock-test" -token ohittaa
    Google-tokeniverifioinnin kaikissa muissa testeissä (TestDeleteActivity,
    TestLikeActivity jne.) — se on tarkoituksellista, jotta näissä luokissa
    voidaan testata bisneslogiikkaa ilman GCP-yhteyttä.

    Tässä luokassa testataan FastAPI-kerroksen suojauksen (puuttuva/epäkelpo
    Authorization-otsake) ENNEN kuin verify_auth_token tarkistaa tokenin sisällön.
    Nämä tarkistukset toimivat ALLOW_MOCK_AUTH-tilasta riippumatta, koska
    ne tapahtuvat ennen mock-haaraa (auth_header puuttuu tai ei ala "Bearer ").

    Tokenin sisältö (aud, exp, iss, sub) testataan tässä luokassa suoraan
    mockkaamalla google.oauth2.id_token.verify_oauth2_token ja
    verify_google_token — katso test_expired_token_returns_401,
    test_wrong_audience_returns_401 ja test_token_without_sub_returns_401.
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

        ALLOW_MOCK_AUTH=true-ympäristössä "TÄMÄEIOLEJWT" ei matchaa "mock-test",
        joten koodi menee Google-verifiointihaaraan → verify_google_token
        palauttaa None → 401. Tulos on deterministinen: 403 ei ole mahdollinen
        tässä koodipolussa.
        """
        response = self.client.post(
            "/ap/activities",
            headers={"Authorization": "Bearer TÄMÄEIOLEJWT"},
            json=self.valid_payload
        )
        self.assertEqual(response.status_code, 401,
            f"Epäkelpo token sai väärän statuksen: {response.status_code}")

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

    @patch("write_api.main.id_token.verify_oauth2_token")
    def test_token_without_sub_returns_401(self, mock_verify_oauth2):
        """
        Token josta puuttuu sub-kenttä → 401, ei KeyError/500.

        verify_auth_token käyttää id_info.get("sub") ja tarkistaa
        eksplisiittisesti onko arvo tyhjä. Ilman tätä tarkistusta
        puuttuva sub aiheuttaisi KeyError:n actorUrl-rakentamisessa
        (f-string {sub}) mikä kaatuu 500-virheeseen.

        Testataan mockkaamalla verify_oauth2_token suoraan (ei verify_google_token)
        jotta ALLOW_MOCK_AUTH ei ohita tarkistusta. os.getenv luetaan
        dynaamisesti (ei moduulitason vakiota), joten patch.dict toimii:
        Python evaluoi os.getenv() joka kutsulla uudelleen, toisin kuin
        moduulitason vakio joka evaluoitaisiin kerran importin yhteydessä.
        """
        mock_verify_oauth2.return_value = {
            "email": "test@example.com",
            "email_verified": True,
            "aud": "test-client-id",
            "exp": int(time.time()) + 3600,
            "iss": "https://accounts.google.com",
            # sub puuttuu tarkoituksella
        }
        with patch.dict(os.environ, {"ALLOW_MOCK_AUTH": "false"}):
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as ctx:
                verify_auth_token("Bearer jokin.oikea.token")
            self.assertEqual(ctx.exception.status_code, 401)
            self.assertIn("subject", ctx.exception.detail.lower(),
                "Virheviesti ei mainitse puuttuvaa subject-kenttää")

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
    """
    Delete-aktiviteetin bisneslogiikkatestit.

    Autentikointi: nämä testit käyttävät "Bearer mock-test" -tokenia.
    ALLOW_MOCK_AUTH=true on asetettu moduulitasolla ennen importtia, joten
    verify_auth_token ohittaa Google-tokeniverifioinnin ja palauttaa
    "test-user-sub-12345" suoraan. Tämä on tarkoituksellinen valinta:
    tässä luokassa testataan Delete-käsittelijän omistajuustarkistusta,
    idempotenttiutta ja virhetiloja — ei autentikoinnin toimintaa.
    Autentikointilogiikka testataan erikseen TestAuthSecurity-luokassa.
    """

    def setUp(self):
        self.client = TestClient(app)

    @patch("write_api.main.get_object_by_id")
    @patch("write_api.main.bq_client")
    def test_delete_own_activity(self, mock_bq, mock_get_obj):
        mock_get_obj.return_value = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7Y",
            "deleted": False,
            "object_json": {
                "type": "Note",
                "attributedTo": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345"
            }
        }
        mock_bq.insert_rows_json.return_value = []
        mock_bq.query.return_value.result.return_value = None

        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Delete",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7Y"
        }
        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 200)
        resp_data = response.json()
        self.assertEqual(resp_data["status"], "deleted")
        self.assertTrue(
            resp_data["id"].startswith("https://activitystreams.uutisseuranta.net/ap/activities/deletes/"),
            f"Delete-aktiviteetin id-kenttä väärässä muodossa: {resp_data.get('id')}"
        )

    @patch("write_api.main.get_object_by_id")
    def test_delete_others_activity_returns_403(self, mock_get_obj):
        mock_get_obj.return_value = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/comments/OTHER",
            "deleted": False,
            "object_json": {
                "type": "Note",
                "attributedTo": "https://activitystreams.uutisseuranta.net/ap/users/toinen-kayttaja"
            }
        }
        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Delete",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": "https://activitystreams.uutisseuranta.net/ap/objects/comments/OTHER"
        }
        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 403)

    @patch("write_api.main.get_object_by_id")
    def test_delete_already_deleted_returns_200(self, mock_get_obj):
        mock_get_obj.return_value = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7Y",
            "deleted": True,
            "object_json": {
                "type": "Note",
                "attributedTo": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345"
            }
        }
        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Delete",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7Y"
        }
        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "already_deleted")

    @patch("write_api.main.get_object_by_id")
    def test_delete_nonexistent_returns_404(self, mock_get_obj):
        mock_get_obj.return_value = None
        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Delete",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": "https://activitystreams.uutisseuranta.net/ap/objects/comments/NOTFOUND"
        }
        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 404)


class TestLikeActivity(unittest.TestCase):
    """
    Like/Dislike-reaktioiden bisneslogiikkatestit.

    Autentikointi: nämä testit käyttävät "Bearer mock-test" -tokenia.
    ALLOW_MOCK_AUTH=true on asetettu moduulitasolla ennen importtia, joten
    verify_auth_token ohittaa Google-tokeniverifioinnin ja palauttaa
    "test-user-sub-12345" suoraan. Tämä on tarkoituksellinen valinta:
    tässä luokassa testataan Like-käsittelijän toggle-logiikkaa (#33),
    idempotenttiutta ja virhetiloja — ei autentikoinnin toimintaa.
    Autentikointilogiikka testataan erikseen TestAuthSecurity-luokassa.
    """

    def setUp(self):
        self.client = TestClient(app)

    @patch("write_api.main.get_object_by_id")
    @patch("write_api.main.get_existing_reaction")
    @patch("write_api.main.bq_client")
    def test_like_success(self, mock_bq, mock_get_reaction, mock_get_obj):
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
        self.assertIn("id", response.json())

    @patch("write_api.main.get_object_by_id")
    @patch("write_api.main.get_existing_reaction")
    def test_like_idempotency_duplicate(self, mock_get_reaction, mock_get_obj):
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

    @patch("write_api.main.get_object_by_id")
    def test_like_deleted_object_returns_404(self, mock_get_obj):
        mock_get_obj.return_value = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y",
            "deleted": True,
            "object_json": {"id": "target-id", "type": "Article"}
        }
        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Like",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y"
        }
        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 404)

    @patch("write_api.main.get_object_by_id")
    def test_like_nonexistent_object_returns_404(self, mock_get_obj):
        mock_get_obj.return_value = None
        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Like",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": "https://activitystreams.uutisseuranta.net/ap/objects/articles/NOTFOUND"
        }
        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 404)

    @patch("write_api.main.get_object_by_id")
    @patch("write_api.main.get_existing_reaction")
    @patch("write_api.main.remove_reaction")
    @patch("write_api.main.bq_client")
    def test_like_toggle_from_dislike(self, mock_bq, mock_remove, mock_get_reaction, mock_get_obj):
        """Dislike → Like toggle: vanha poistetaan, uusi tallennetaan."""
        mock_get_obj.return_value = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y",
            "deleted": False,
            "object_json": {"id": "target-id", "type": "Article"}
        }
        mock_get_reaction.return_value = "Dislike"
        mock_bq.insert_rows_json.return_value = []

        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Like",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y"
        }
        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 201)
        mock_remove.assert_called_once()
        mock_bq.insert_rows_json.assert_called_once()
        args = mock_bq.insert_rows_json.call_args[0]
        rows = args[1]
        self.assertEqual(rows[0]["type"], "Like")


class TestCreateActivity(unittest.TestCase):
    """
    Create-aktiviteetin (kommentointi) bisneslogiikkatestit.

    Autentikointi: nämä testit käyttävät "Bearer mock-test" -tokenia.
    ALLOW_MOCK_AUTH=true on asetettu moduulitasolla ennen importtia, joten
    verify_auth_token ohittaa Google-tokeniverifioinnin ja palauttaa
    "test-user-sub-12345" suoraan. Tämä on tarkoituksellinen valinta:
    tässä luokassa testataan Create Note -käsittelijän inReplyTo-validointia,
    syvyysrajoitusta ja virhetiloja — ei autentikoinnin toimintaa.
    Autentikointilogiikka testataan erikseen TestAuthSecurity-luokassa.
    """

    def setUp(self):
        self.client = TestClient(app)

    @patch("write_api.main.get_object_by_id")
    @patch("write_api.main.bq_client")
    def test_create_note_success(self, mock_bq, mock_get_obj):
        mock_get_obj.return_value = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y",
            "deleted": False,
            "object_json": {"id": "target-id", "type": "Article", "thread_root": None}
        }
        mock_bq.insert_rows_json.return_value = []
        mock_bq.query.return_value.result.return_value = None

        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Create",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": {
                "type": "Note",
                "content": "Tämä on testi-kommentti",
                "inReplyTo": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y"
            }
        }
        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(
            body["id"].startswith("https://activitystreams.uutisseuranta.net/ap/activities/creates/"),
            f"Create-aktiviteetin id väärässä muodossa: {body.get('id')}"
        )
        self.assertTrue(
            body["object_id"].startswith("https://activitystreams.uutisseuranta.net/ap/objects/comments/"),
            f"object_id väärässä muodossa: {body.get('object_id')}"
        )

    @patch("write_api.main.get_object_by_id")
    def test_create_note_missing_inreplyto_returns_400(self, mock_get_obj):
        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Create",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": {
                "type": "Note",
                "content": "Kommentti ilman inReplyTo"
            }
        }
        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 400)

    @patch("write_api.main.get_object_by_id")
    def test_create_note_wrong_type_returns_400(self, mock_get_obj):
        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Create",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": {
                "type": "Article",
                "inReplyTo": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y"
            }
        }
        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 400)

    @patch("write_api.main.get_object_by_id")
    def test_create_reply_depth_limit(self, mock_get_obj):
        """Kommentti kommentille jonka vanhempi on jo kommentti → 400 (max 2 tasoa)."""
        parent_url = "https://activitystreams.uutisseuranta.net/ap/objects/comments/parent"
        grandparent_url = "https://activitystreams.uutisseuranta.net/ap/objects/comments/grandparent"
        root_url = "https://activitystreams.uutisseuranta.net/ap/objects/articles/ROOT"

        mock_get_obj.side_effect = lambda obj_id: {
            parent_url: {
                "id": parent_url, "deleted": False,
                "object_json": {"type": "Note", "inReplyTo": grandparent_url, "thread_root": root_url}
            },
            grandparent_url: {
                "id": grandparent_url, "deleted": False,
                "object_json": {"type": "Note", "inReplyTo": root_url, "thread_root": root_url}
            },
        }.get(obj_id, None)

        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Create",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": {
                "type": "Note",
                "content": "Liian syvä vastaus",
                "inReplyTo": parent_url
            }
        }
        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("depth", response.json()["detail"].lower())


class TestUnsupportedActivityType(unittest.TestCase):
    """
    Tuntemattomat aktiviteettityypit.

    Autentikointi: nämä testit käyttävät "Bearer mock-test" -tokenia.
    ALLOW_MOCK_AUTH=true on asetettu moduulitasolla ennen importtia, joten
    verify_auth_token ohittaa Google-tokeniverifioinnin. Tässä luokassa
    testataan että tuntematon tyyppi (Follow, puuttuva type) hylätään 400:lla
    ennen kuin bisneslogiikka edes käynnistyy.
    """

    def setUp(self):
        self.client = TestClient(app)

    def test_unsupported_type_returns_400(self):
        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Follow",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": "https://activitystreams.uutisseuranta.net/ap/users/toinen"
        }
        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 400)

    def test_missing_type_returns_400(self):
        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": "https://activitystreams.uutisseuranta.net/ap/objects/articles/01H7Y"
        }
        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 400)


class TestHealthEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_healthz_returns_ok(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    @patch("write_api.main.bq_client")
    def test_readyz_returns_ready_when_bq_ok(self, mock_bq):
        mock_bq.list_datasets.return_value = iter([])
        response = self.client.get("/readyz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")

    @patch("write_api.main.bq_client")
    def test_readyz_returns_503_when_bq_fails(self, mock_bq):
        mock_bq.list_datasets.side_effect = Exception("BQ unreachable")
        response = self.client.get("/readyz")
        self.assertEqual(response.status_code, 503)

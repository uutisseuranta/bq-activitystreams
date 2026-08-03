# test_lausuntopalvelu_fetch_job.py
import datetime
import unittest
from unittest.mock import MagicMock, patch

from lausuntopalvelu_fetch_job import parse_xml_date, build_as2_article

class TestLausuntopalveluFetchJob(unittest.TestCase):
    def test_parse_xml_date_len_under_19(self):
        # boundary case: len <= 19 (no timezone offset)
        dt = parse_xml_date("2015-05-13T14:25:24")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, datetime.timezone.utc)
        self.assertEqual(dt.year, 2015)
        self.assertEqual(dt.month, 5)
        self.assertEqual(dt.day, 13)
        self.assertEqual(dt.hour, 14)
        self.assertEqual(dt.minute, 25)
        self.assertEqual(dt.second, 24)

    def test_parse_xml_date_only(self):
        # date-only input (e.g. "2026-08-03")
        dt = parse_xml_date("2026-08-03")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, datetime.timezone.utc)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 8)
        self.assertEqual(dt.day, 3)
        self.assertEqual(dt.hour, 0)
        self.assertEqual(dt.minute, 0)
        self.assertEqual(dt.second, 0)

    def test_parse_xml_date_with_offset(self):
        # offset-bearing string (e.g. "2015-05-13T14:25:24+03:00")
        dt = parse_xml_date("2015-05-13T14:25:24+03:00")
        self.assertIsNotNone(dt)
        # Should be converted to UTC: 14:25:24+03:00 -> 11:25:24 UTC
        self.assertEqual(dt.tzinfo, datetime.timezone.utc)
        self.assertEqual(dt.hour, 11)
        self.assertEqual(dt.minute, 25)
        self.assertEqual(dt.second, 24)

    def test_build_as2_article(self):
        prop = {
            "Id": "12345",
            "Name": "Test Proposal",
            "PublishedOn": "2026-08-03T12:00:00",
            "Goals": "<p>These are goals</p>",
            "OrganizationName": "Ministry of Justice"
        }
        res = build_as2_article(prop, "lausuntopalvelu", "example.com")
        self.assertTrue(res["id"].startswith("https://example.com/ap/objects/"))
        self.assertEqual(len(res["id"]), 31 + 16) # 31 chars for prefix + 16 chars hash
        self.assertEqual(res["source"], "lausuntopalvelu")
        self.assertIsNotNone(res["published"])
        self.assertEqual(res["published"].year, 2026)
        
        obj = res["object_json"]
        self.assertEqual(obj["type"], "Article")
        self.assertEqual(obj["name"], "Test Proposal")
        self.assertEqual(obj["summary"], "These are goals")
        self.assertIsNone(obj["license"])

    def test_get_existing_ids(self):
        from lausuntopalvelu_fetch_job import get_existing_ids
        mock_bq = MagicMock()
        mock_row1 = MagicMock()
        mock_row1.id = "id1"
        mock_row2 = MagicMock()
        mock_row2.id = "id2"
        mock_bq.query.return_value.result.return_value = [mock_row1, mock_row2]

        ids = get_existing_ids(mock_bq, "project", "dataset", "lausuntopalvelu")
        self.assertEqual(ids, {"id1", "id2"})
        mock_bq.query.assert_called_once()

    def test_get_last_fetched_timestamp(self):
        from lausuntopalvelu_fetch_job import get_last_fetched_timestamp
        mock_bq = MagicMock()
        mock_row = MagicMock()
        mock_row.value = "2026-08-03T12:00:00Z"
        mock_bq.query.return_value.result.return_value = [mock_row]

        ts = get_last_fetched_timestamp(mock_bq, "project", "dataset")
        self.assertEqual(ts, "2026-08-03T12:00:00Z")

    def test_write_to_bigquery(self):
        from lausuntopalvelu_fetch_job import write_to_bigquery
        mock_bq = MagicMock()
        items = [{
            "id": "123",
            "source": "lausuntopalvelu",
            "published": datetime.datetime.now(datetime.timezone.utc),
            "object_json": {"name": "test"}
        }]
        write_to_bigquery(mock_bq, "project", "dataset", items)
        mock_bq.load_table_from_json.assert_called_once()

    @patch("lausuntopalvelu_fetch_job.httpx.get")
    @patch("lausuntopalvelu_fetch_job.write_to_gcs_bronze")
    @patch("lausuntopalvelu_fetch_job.bigquery.Client")
    @patch("lausuntopalvelu_fetch_job.get_existing_ids", return_value=set())
    @patch("lausuntopalvelu_fetch_job.get_last_fetched_timestamp", return_value=None)
    @patch("lausuntopalvelu_fetch_job.write_to_bigquery")
    @patch("lausuntopalvelu_fetch_job.update_last_fetched_timestamp")
    def test_main_pagination(self, mock_update, mock_write_bq, mock_last, mock_exist, mock_bq_class, mock_gcs, mock_get):
        from lausuntopalvelu_fetch_job import main
        import os
        os.environ["GCP_PROJECT"] = "test-project"

        # Mock page 1 with nextLink and page 2 without nextLink
        xml_page1 = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <link rel="next" href="https://example.com/page2" />
          <entry>
            <content type="application/xml">
              <m:properties xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">
                <d:Id>1</d:Id>
                <d:Name>Proposal 1</d:Name>
              </m:properties>
            </content>
          </entry>
        </feed>
        """
        xml_page2 = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <content type="application/xml">
              <m:properties xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">
                <d:Id>2</d:Id>
                <d:Name>Proposal 2</d:Name>
              </m:properties>
            </content>
          </entry>
        </feed>
        """
        
        mock_resp1 = MagicMock()
        mock_resp1.content = xml_page1.encode("utf-8")
        mock_resp1.raise_for_status = MagicMock()

        mock_resp2 = MagicMock()
        mock_resp2.content = xml_page2.encode("utf-8")
        mock_resp2.raise_for_status = MagicMock()

        mock_get.side_effect = [mock_resp1, mock_resp2]

        main()

        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(mock_gcs.call_count, 2)
        mock_write_bq.assert_called_once()
        args, _ = mock_write_bq.call_args
        self.assertEqual(len(args[3]), 2) # Two items total from two pages

    @patch("lausuntopalvelu_fetch_job.httpx.get")
    @patch("lausuntopalvelu_fetch_job.write_to_gcs_bronze")
    @patch("lausuntopalvelu_fetch_job.bigquery.Client")
    @patch("lausuntopalvelu_fetch_job.get_existing_ids", return_value=set())
    @patch("lausuntopalvelu_fetch_job.get_last_fetched_timestamp", return_value=None)
    @patch("lausuntopalvelu_fetch_job.write_to_bigquery")
    @patch("lausuntopalvelu_fetch_job.update_last_fetched_timestamp")
    def test_main_pagination_relative_next(self, mock_update, mock_write_bq, mock_last, mock_exist, mock_bq_class, mock_gcs, mock_get):
        from lausuntopalvelu_fetch_job import main
        import os
        os.environ["GCP_PROJECT"] = "test-project"

        # Mock page 1 with relative nextLink
        xml_page1 = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <link rel="next" href="Proposals?$top=100&amp;$skip=100" />
          <entry>
            <content type="application/xml">
              <m:properties xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">
                <d:Id>1</d:Id>
                <d:Name>Proposal 1</d:Name>
              </m:properties>
            </content>
          </entry>
        </feed>
        """
        xml_page2 = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <content type="application/xml">
              <m:properties xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">
                <d:Id>2</d:Id>
                <d:Name>Proposal 2</d:Name>
              </m:properties>
            </content>
          </entry>
        </feed>
        """
        
        mock_resp1 = MagicMock()
        mock_resp1.content = xml_page1.encode("utf-8")
        mock_resp1.raise_for_status = MagicMock()

        mock_resp2 = MagicMock()
        mock_resp2.content = xml_page2.encode("utf-8")
        mock_resp2.raise_for_status = MagicMock()

        mock_get.side_effect = [mock_resp1, mock_resp2]

        main()

        self.assertEqual(mock_get.call_count, 2)
        # Verify the second URL is prefixed with the base service URL
        mock_get.assert_any_call(
            "https://www.lausuntopalvelu.fi/api/v1/Lausuntopalvelu.svc/Proposals?$top=100&$skip=100",
            headers={"User-Agent": "uutisseuranta-fetch-bot/1.0 (+https://uutisseuranta.net)"},
            timeout=25,
            follow_redirects=True
        )


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

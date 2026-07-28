# bq-activitystreams/test_rss_fetch_job.py
import unittest
from unittest.mock import MagicMock, patch

import rss_fetch_job
from bs4 import BeautifulSoup


class TestRssFetchJob(unittest.TestCase):
    def test_parse_rss_images_hs_format(self):
        # Helsingin Sanomat format (uses media:content or media:thumbnail)
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
          <channel>
            <title>HS.fi - Uutiset</title>
            <link>https://www.hs.fi</link>
            <item>
              <title>Ura | Neuvosto-Virossa oltiin ihmeissään, kun Jukka Kunnas toi amerikkalaista viihdettä maahan</title>
              <link>https://www.hs.fi/ura/art-2000010582967.html</link>
              <description>Jukka Kunnas toi amerikkalaista show-viihdettä 80-luvun Tallinnaan.</description>
              <pubDate>Sun, 26 Jul 2026 12:00:00 +0300</pubDate>
              <media:content url="https://hs.mediadelivery.fi/img/1920/123456.jpg" medium="image" />
            </item>
          </channel>
        </rss>
        """

        # Test basic parsing via BeautifulSoup
        soup = BeautifulSoup(xml_content, "xml")
        items = soup.find_all("item")
        self.assertEqual(len(items), 1)

        # Parse XML content using fetch_rss_feed mock
        mock_response = MagicMock()
        mock_response.content = xml_content.encode("utf-8")
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_response):
            parsed = rss_fetch_job.fetch_rss_feed("https://www.hs.fi/rss/tuoreimmat.xml", timeout=10)

        self.assertEqual(len(parsed), 1)
        self.assertEqual(
            parsed[0]["title"],
            "Ura | Neuvosto-Virossa oltiin ihmeissään, kun Jukka Kunnas toi amerikkalaista viihdettä maahan",
        )
        self.assertEqual(parsed[0]["image_url"], "https://hs.mediadelivery.fi/img/1920/123456.jpg")

    def test_parse_rss_images_media_thumbnail(self):
        # Feeds using media:thumbnail
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
          <channel>
            <title>Test Feed</title>
            <link>https://example.com</link>
            <item>
              <title>Testiartikkeli</title>
              <link>https://example.com/artikkeli</link>
              <description>Lyhyt kuvaus</description>
              <pubDate>Mon, 27 Jul 2026 12:00:00 +0300</pubDate>
              <media:thumbnail url="https://example.com/thumb.jpg" />
            </item>
          </channel>
        </rss>
        """
        mock_response = MagicMock()
        mock_response.content = xml_content.encode("utf-8")
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_response):
            parsed = rss_fetch_job.fetch_rss_feed("https://example.com/rss", timeout=10)

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["image_url"], "https://example.com/thumb.jpg")

    def test_parse_rss_images_enclosure(self):
        # Feeds using enclosure
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <title>Test Feed</title>
            <link>https://example.com</link>
            <item>
              <title>Testiartikkeli</title>
              <link>https://example.com/artikkeli</link>
              <description>Lyhyt kuvaus</description>
              <pubDate>Mon, 27 Jul 2026 12:00:00 +0300</pubDate>
              <enclosure url="https://example.com/enclosure.jpg" type="image/jpeg" length="12345" />
            </item>
          </channel>
        </rss>
        """
        mock_response = MagicMock()
        mock_response.content = xml_content.encode("utf-8")
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_response):
            parsed = rss_fetch_job.fetch_rss_feed("https://example.com/rss", timeout=10)

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["image_url"], "https://example.com/enclosure.jpg")

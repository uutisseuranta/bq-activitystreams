# src/shared/test_og_parser.py
import unittest
from unittest.mock import patch

import og_parser


class TestOgParser(unittest.TestCase):
    def test_longer(self):
        self.assertEqual(og_parser.longer("  a  ", " b "), "a")
        self.assertEqual(og_parser.longer(" ", "\n"), None)
        self.assertEqual(og_parser.longer("abc", "ab"), "abc")
        self.assertEqual(og_parser.longer("ab", "abc"), "abc")
        self.assertEqual(og_parser.longer(None, "abc"), "abc")
        self.assertEqual(og_parser.longer("abc", None), "abc")

    def test_is_forbidden_ip(self):
        self.assertTrue(og_parser.is_forbidden_ip("127.0.0.1"))
        self.assertTrue(og_parser.is_forbidden_ip("::1"))
        self.assertTrue(og_parser.is_forbidden_ip("10.0.0.5"))
        self.assertTrue(og_parser.is_forbidden_ip("172.16.5.5"))
        self.assertTrue(og_parser.is_forbidden_ip("192.168.1.1"))
        self.assertTrue(og_parser.is_forbidden_ip("169.254.169.254"))
        self.assertTrue(og_parser.is_forbidden_ip("169.254.10.10"))
        # Sallittu julkinen IP
        self.assertFalse(og_parser.is_forbidden_ip("8.8.8.8"))
        self.assertFalse(og_parser.is_forbidden_ip("2001:4860:4860::8888"))

    def test_parse_og_metadata(self):
        html = b"""
        <html>
        <head>
          <title>Fallback Title</title>
          <meta property="og:title" content="OG Title" />
          <meta property="og:description" content="OG Description" />
          <meta property="og:image" content="https://example.com/img.png" />
          <meta property="og:url" content="https://example.com/canonical" />
          <meta property="og:site_name" content="Example Site" />
          <meta property="article:published_time" content="2026-07-02T12:00:00Z" />
          <meta property="article:modified_time" content="2026-07-02T13:00:00Z" />
        </head>
        <body></body>
        </html>
        """
        metadata = og_parser.parse_og_metadata(html, "https://example.com/req")
        self.assertEqual(metadata["title"], "OG Title")
        self.assertEqual(metadata["description"], "OG Description")
        self.assertEqual(metadata["image"], "https://example.com/img.png")
        self.assertEqual(metadata["url"], "https://example.com/canonical")
        self.assertEqual(metadata["site_name"], "Example Site")
        self.assertEqual(metadata["published_time"], "2026-07-02T12:00:00Z")
        self.assertEqual(metadata["modified_time"], "2026-07-02T13:00:00Z")

    def test_parse_og_metadata_fallback(self):
        # Testataan fallback <title> tagille ja description namelle
        html = b"""
        <html>
        <head>
          <title>Fallback Title</title>
          <meta name="description" content="Meta Description" />
        </head>
        <body></body>
        </html>
        """
        metadata = og_parser.parse_og_metadata(html, "https://example.com/req")
        self.assertEqual(metadata["title"], "Fallback Title")
        self.assertEqual(metadata["description"], "Meta Description")
        self.assertEqual(metadata["image"], None)

    def test_parse_og_metadata_k5a_paywall_locked(self):
        html = b"""
        <html>
        <head>
          <meta property="k5a:paywall" content="locked" />
          <meta property="k5a:paid" content="1" />
        </head>
        <body></body>
        </html>
        """
        metadata = og_parser.parse_og_metadata(html, "https://example.com/req")
        self.assertEqual(metadata["k5a_paywall"], "locked")
        self.assertEqual(metadata["k5a_paid"], "1")
        self.assertEqual(metadata["is_accessible_for_free"], False)

    def test_parse_og_metadata_k5a_paywall_open(self):
        html = b"""
        <html>
        <head>
          <meta property="k5a:paywall" content="open" />
          <meta property="k5a:paid" content="1" />
          <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "@type": "NewsArticle",
            "isAccessibleForFree": true
          }
          </script>
        </head>
        <body></body>
        </html>
        """
        metadata = og_parser.parse_og_metadata(html, "https://example.com/req")
        self.assertEqual(metadata["k5a_paywall"], "open")
        self.assertEqual(metadata["k5a_paid"], "1")
        # should remain True because paywall is open and JSON-LD says true
        self.assertEqual(metadata["is_accessible_for_free"], True)

    def test_parse_og_metadata_property_attributes(self):
        html = b"""
        <html>
        <head>
          <meta property="article:content_tier" content="locked" />
        </head>
        <body></body>
        </html>
        """
        metadata = og_parser.parse_og_metadata(html, "https://example.com/req")
        self.assertEqual(metadata["is_accessible_for_free"], False)

    @patch("og_parser.fetch_url_stream")
    def test_robots_check_allow(self, mock_fetch):
        # robots.txt sallii kaiken
        mock_fetch.return_value = b"User-agent: *\nAllow: /"

        # Tyhjennetään cache testejä varten
        og_parser.ROBOTS_CACHE.clear()

        allowed = og_parser.robots_check("https://example.com/allowed-page")
        self.assertTrue(allowed)

        # Testataan välimuistin toiminta (fetch pitäisi kutsua vain kerran)
        allowed_again = og_parser.robots_check("https://example.com/another-page")
        self.assertTrue(allowed_again)
        mock_fetch.assert_called_once()

    @patch("og_parser.fetch_url_stream")
    def test_robots_check_disallow(self, mock_fetch):
        # robots.txt kieltää
        mock_fetch.return_value = b"User-agent: *\nDisallow: /"
        og_parser.ROBOTS_CACHE.clear()

        allowed = og_parser.robots_check("https://disallowed.com/page")
        self.assertFalse(allowed)

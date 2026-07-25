#!/bin/bash
# unit-test.sh — yksikkötestit, ei verkkoa eikä BigQuerya
# Malli: uutisseuranta.github.io/live-smoke-test.sh
set -euo pipefail

# Otetaan käyttöön keskitetyt alustusapulaiset
# shellcheck source=fetch_helpers.sh
source "$(dirname "$0")/fetch_helpers.sh"

echo "Ajetaan Python-yksikkötestit..."

python3 - <<'EOF'
import sys
from datetime import timezone, datetime
from defusedxml import ElementTree as ET
from email.utils import parsedate_to_datetime
import re
import html as html_lib
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Testattavat funktiot (inline — ei vaadi erillistä moduulia vielä)
# ---------------------------------------------------------------------------

NS = {
    "media":   "http://search.yahoo.com/mrss/",
    "atom":    "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
}

@dataclass
class RssItem:
    guid: str; url: str; title: str
    summary: object; image_url: object
    published: object; updated: object

def parse_datetime(raw):
    """Parsi datetime RFC 2822- tai ISO 8601 -muodosta."""
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

def clean_text(raw):
    """Strippaa HTML-tagit ja pura entiteetit."""
    stripped = re.sub(r"<[^>]+>", " ", raw)
    return html_lib.unescape(stripped).strip()

def parse_feed(xml_bytes):
    root = ET.fromstring(xml_bytes)
    channel = root.find("channel")
    if channel is None:
        return []
    channel_image = None
    if (img := channel.find("image")) is not None:
        channel_image = img.findtext("url")
    items = []
    for item in channel.findall("item"):
        pub_raw = item.findtext("pubDate") or item.findtext("atom:updated", namespaces=NS)
        if not pub_raw:
            continue
        published = parse_datetime(pub_raw)
        if published is None:
            continue
        updated_raw = item.findtext("atom:updated", namespaces=NS)
        updated = parse_datetime(updated_raw) if updated_raw else published
        if updated is None:
            updated = published
        url = item.findtext("link") or ""
        guid = item.findtext("guid") or url
        if not url:
            continue
        title = clean_text(item.findtext("title") or "")
        summary = clean_text(item.findtext("description") or "") or None
        image_url = None
        if (mt := item.find("media:thumbnail", NS)) is not None:
            image_url = mt.get("url")
        if not image_url:
            for enc in item.findall("enclosure"):
                if (enc.get("type") or "").startswith("image/"):
                    image_url = enc.get("url"); break
        if not image_url:
            image_url = channel_image
        items.append(RssItem(guid=guid, url=url, title=title,
                              summary=summary, image_url=image_url,
                              published=published, updated=updated))
    return items

# ---------------------------------------------------------------------------
# Testit
# ---------------------------------------------------------------------------

# parse_datetime: RFC 2822
dt = parse_datetime("Tue, 01 Jul 2026 17:00:00 +0000")
assert dt is not None, "RFC 2822 parsinta epäonnistui"
assert dt.year == 2026 and dt.month == 7 and dt.day == 1, f"Väärä päivä: {dt}"

# parse_datetime: ISO 8601 (atom:updated)
dt2 = parse_datetime("2026-07-01T17:00:00Z")
assert dt2 is not None, "ISO 8601 parsinta epäonnistui"
assert dt2.tzinfo is not None, "Aikavyöhyke puuttuu"

# parse_datetime: tuntematon muoto → None
assert parse_datetime("ei-paivays") is None, "Pitäisi palauttaa None"

print("  ✓ parse_datetime: RFC 2822, ISO 8601, tuntematon muoto")

# clean_text
assert clean_text("<b>Otsikko</b>") == "Otsikko"
assert clean_text("A &amp; B") == "A & B"
assert clean_text("  ") == ""

print("  ✓ clean_text: HTML-tagit, entiteetit, tyhjä merkkijono")

# parse_feed: perusrakenne + pubDate-ohitus
RSS_BASIC = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Testi</title>
    <item>
      <title>Artikkeli 1</title>
      <link>https://example.com/a1</link>
      <pubDate>Tue, 01 Jul 2026 17:00:00 +0000</pubDate>
      <description>Kuvaus &amp; lisää</description>
    </item>
    <item>
      <title>Ohitettava — ei pubDate</title>
      <link>https://example.com/a2</link>
    </item>
  </channel>
</rss>""".encode('utf-8')

items = parse_feed(RSS_BASIC)
assert len(items) == 1, f"Odotettu 1 artikkeli, saatiin {len(items)}"
assert items[0].title == "Artikkeli 1"
assert items[0].summary == "Kuvaus & lisää"
assert items[0].url == "https://example.com/a1"

print("  ✓ parse_feed: perusrakenne, pubDate-ohitus")

# parse_feed: atom:updated fallback pubDatelle
RSS_ATOM_UPDATED = """<?xml version="1.0"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Atom-testi</title>
    <item>
      <title>Atom-artikkeli</title>
      <link>https://example.com/atom1</link>
      <atom:updated>2026-07-01T18:00:00Z</atom:updated>
    </item>
  </channel>
</rss>""".encode('utf-8')

items2 = parse_feed(RSS_ATOM_UPDATED)
assert len(items2) == 1, f"atom:updated fallback epäonnistui: {len(items2)} kpl"
assert items2[0].published.hour == 18

print("  ✓ parse_feed: atom:updated fallback pubDatelle")

# parse_feed: kuvaprioriteetti (media:thumbnail > enclosure > kanava)
RSS_IMAGES = """<?xml version="1.0"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>Kuvatesti</title>
    <image><url>https://example.com/channel.jpg</url></image>
    <item>
      <title>Kuva enclosuresta</title>
      <link>https://example.com/enc</link>
      <pubDate>Tue, 01 Jul 2026 17:00:00 +0000</pubDate>
      <enclosure url="https://example.com/enc.jpg" type="image/jpeg"/>
    </item>
    <item>
      <title>Kuva media:thumbnailista</title>
      <link>https://example.com/mt</link>
      <pubDate>Tue, 01 Jul 2026 17:00:00 +0000</pubDate>
      <media:thumbnail url="https://example.com/thumb.jpg"/>
      <enclosure url="https://example.com/enc2.jpg" type="image/jpeg"/>
    </item>
    <item>
      <title>Kuva kanavalta</title>
      <link>https://example.com/ch</link>
      <pubDate>Tue, 01 Jul 2026 17:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>""".encode('utf-8')

imgs = parse_feed(RSS_IMAGES)
assert imgs[0].image_url == "https://example.com/enc.jpg",  f"enclosure: {imgs[0].image_url}"
assert imgs[1].image_url == "https://example.com/thumb.jpg", f"media:thumbnail: {imgs[1].image_url}"
assert imgs[2].image_url == "https://example.com/channel.jpg", f"kanava: {imgs[2].image_url}"

print("  ✓ parse_feed: kuvaprioriteetti (media:thumbnail > enclosure > kanava)")

# parse_feed: tyhjä syöte
RSS_EMPTY = b"""<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>"""
assert parse_feed(RSS_EMPTY) == [], "Tyhjä syöte ei palauttanut []"

print("  ✓ parse_feed: tyhjä syöte → []")

# parse_feed: ei <channel>-elementtiä
RSS_NO_CHANNEL = b"""<?xml version="1.0"?><rss version="2.0"></rss>"""
assert parse_feed(RSS_NO_CHANNEL) == [], "Ei channel-elementtiä ei palauttanut []"

print("  ✓ parse_feed: puuttuva <channel> → []")

print("\nKaikki yksikkötestit läpäisty ✓")
EOF

echo "Ajetaan shared-paketin unittest-testit..."

python3 -m unittest src/shared/test_og_parser.py

echo "Ajetaan write_api-paketin unittest-testit..."
python3 -m unittest src/write_api/test_main.py

echo "Ajetaan query_api-paketin unittest-testit..."
python3 -m unittest src/query_api/test_main.py

echo "Ajetaan og_scraper-paketin unittest-testit..."
python3 -m unittest src/og_scraper/test_main.py

echo "Ajetaan og_enrichment_job-paketin unittest-testit..."
python3 -m unittest src/og_enrichment_job/test_main.py

echo "Kaikki testit suoritettu onnistuneesti!"

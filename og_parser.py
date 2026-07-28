# src/shared/og_parser.py
import ipaddress
import logging
import socket
import time
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("og-parser")

# Shared constants
OG_HEADERS = {
    "User-Agent": "Uutisseuranta-Bot/1.0 (+https://uutisseuranta.net)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
TIMEOUT = 10.0
MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MB

# In-memory cache for Robots.txt parser
# netloc -> (expiry_time, RobotFileParser)
ROBOTS_CACHE: Dict[str, tuple[float, RobotFileParser]] = {}
ROBOTS_CACHE_TTL = 24 * 3600  # 24 hours


def is_forbidden_ip(ip_str: str) -> bool:
    """Tarkistaa onko IP-osoite sallittu (ei localhost, private RFC1918, link-local, metadata)."""
    try:
        ip = ipaddress.ip_address(ip_str)
        # Loopback (127.0.0.1, ::1)
        if ip.is_loopback:
            return True
        # Private (RFC1918: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
        if ip.is_private:
            return True
        # Link-local (169.254.0.0/16)
        if ip.is_link_local:
            return True
        # Reserved / Multicast
        if ip.is_reserved or ip.is_multicast:
            return True
        # Explicitly check GCP metadata server
        if ip_str == "169.254.169.254":
            return True
        return False
    except ValueError:
        # Jos ei voida parsia, estetään turvallisuussyistä
        return True


def validate_url_ip(url: str) -> bool:
    """SSRF-suojaus: Resolvoi domainin IP:t ja tarkistaa ovatko ne sallittuja."""
    try:
        parsed = urlparse(url)
        if not parsed.scheme or parsed.scheme not in ("http", "https"):
            logger.warning(f"Invalid URL scheme: {url}")
            return False
        hostname = parsed.hostname
        if not hostname:
            logger.warning(f"No hostname in URL: {url}")
            return False

        # Resolvoidaan osoitteet
        addrinfo = socket.getaddrinfo(hostname, None)
        for _family, _socktype, _proto, _canonname, sockaddr in addrinfo:
            ip = sockaddr[0]
            if is_forbidden_ip(ip):
                logger.warning(f"SSRF block: Hostname {hostname} resolved to forbidden IP {ip}")
                return False
        return True
    except Exception as e:
        logger.warning(f"Failed to validate URL IP for {url}: {e}")
        return False


def fetch_url_stream_with_headers(
    url: str, timeout: float = TIMEOUT, max_bytes: int = MAX_RESPONSE_BYTES
) -> tuple[bytes, dict[str, str]]:
    """Hakee sivun turvallisesti redirectejä seuraten ja SSRF-suojauksen tarkistaen.

    Streamataan vain </head>-tagiin asti tai max_bytes kokoon saakka.
    Palauttaa tuplena (sisältö tavuina, otsikot sanakirjana).
    """
    current_url = url
    redirect_count = 0

    while True:
        if not validate_url_ip(current_url):
            raise PermissionError(f"SSRF check failed: forbidden destination IP for {current_url}")

        with httpx.Client(timeout=timeout) as client:
            with client.stream("GET", current_url, headers=OG_HEADERS, follow_redirects=False) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    redirect_count += 1
                    if redirect_count > MAX_REDIRECTS:
                        raise ValueError("Too many redirects")

                    location = response.headers.get("Location")
                    if not location:
                        raise ValueError("Redirect response missing Location header")

                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()

                # Varmistetaan että sisältö on HTML-tyyppistä
                content_type = response.headers.get("Content-Type", "")
                if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                    raise ValueError(f"Invalid content type: {content_type}")

                content_bytes = bytearray()
                for chunk in response.iter_bytes():
                    content_bytes.extend(chunk)
                    if len(content_bytes) > max_bytes:
                        content_bytes = content_bytes[:max_bytes]
                        break



                headers_dict = dict(response.headers)
                return bytes(content_bytes), headers_dict


def fetch_url_stream(url: str, timeout: float = TIMEOUT, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    """Hakee sivun turvallisesti redirectejä seuraten ja SSRF-suojauksen tarkistaen.

    Säilyttää alkuperäisen rajapinnan palauttamalla vain sisällön tavuina.
    """
    content, _headers = fetch_url_stream_with_headers(url, timeout, max_bytes)
    return content


def get_robots_parser(url: str) -> RobotFileParser:
    """Hakee robots.txt-tiedoston ja palauttaa parserin cachettuna 24 tunniksi."""
    parsed = urlparse(url)
    netloc = parsed.netloc
    scheme = parsed.scheme
    if not netloc or not scheme:
        rp = RobotFileParser()
        rp.parse([])  # Sallitaan kaikki jos URL on virheellinen
        return rp

    now = time.time()
    if netloc in ROBOTS_CACHE:
        expiry, rp = ROBOTS_CACHE[netloc]
        if now < expiry:
            return rp

    robots_url = f"{scheme}://{netloc}/robots.txt"
    rp = RobotFileParser()
    try:
        # Hae robots.txt SSRF-suojauksen läpi, max 50KB riittää
        content_bytes = fetch_url_stream(robots_url, timeout=5.0, max_bytes=50000)
        lines = content_bytes.decode("utf-8", errors="ignore").splitlines()
        rp.parse(lines)
    except Exception as e:
        logger.info(f"Failed to fetch robots.txt for {netloc} ({e}), assuming allowed")
        rp.parse([])  # Sallitaan kaikki virhetilanteissa

    ROBOTS_CACHE[netloc] = (now + ROBOTS_CACHE_TTL, rp)
    return rp


def robots_check(url: str, user_agent: str = "Uutisseuranta-Bot") -> bool:
    """Tarkistaa salliiko robots.txt haun kyseiselle URL-osoitteelle.
    Noudattaa Robots Exclusion Protocolia (RFC 9309).
    """
    try:
        rp = get_robots_parser(url)
        return rp.can_fetch(user_agent, url) or rp.can_fetch("*", url)
    except Exception as e:
        logger.warning(f"Error in robots_check for {url}: {e}")
        return True


def get_wayback_snapshot(url: str, timeout: float = 5.0) -> Optional[str]:
    """Tarkistaa Internet Archiven Availability API:sta, onko URL-osoitteesta olemassa tallennettua snapshotia.
    Palauttaa suoran Wayback Machine snapshot URL-osoitteen jos saatavilla, muuten None.
    """
    try:
        api_url = f"https://archive.org/wayback/available?url={url}"
        response = httpx.get(api_url, headers=OG_HEADERS, timeout=timeout)
        if response.status_code == 200:
            data = response.json()
            closest = data.get("archived_snapshots", {}).get("closest")
            if isinstance(closest, dict) and closest.get("available") is True and closest.get("url"):
                return str(closest["url"])
    except Exception as e:
        logger.info(f"Wayback Availability check error for {url}: {e}")
    return None


def parse_og_metadata(html_content: bytes, default_url: str) -> Dict[str, Any]:
    """Parsii Open Graph, meta-tagit ja JSON-LD Schema.org (ml. maksumuurin status isAccessibleForFree)."""
    soup = BeautifulSoup(html_content, "lxml")
    metadata: Dict[str, Any] = {
        "title": None,
        "description": None,
        "image": None,
        "url": default_url,
        "site_name": None,
        "published_time": None,
        "modified_time": None,
        "is_accessible_for_free": None,
    }

    is_accessible_for_free: Optional[bool] = None

    for meta in soup.find_all("meta"):
        property_attr = meta.get("property", "").lower()
        name_attr = meta.get("name", "").lower()
        content = meta.get("content", "")

        if not content:
            continue

        if property_attr == "og:title":
            metadata["title"] = content
        elif property_attr == "og:description":
            metadata["description"] = content
        elif property_attr == "og:image":
            metadata["image"] = content
        elif property_attr == "og:url":
            metadata["url"] = content
        elif property_attr == "og:site_name":
            metadata["site_name"] = content
        elif property_attr == "article:published_time":
            metadata["published_time"] = content
        elif property_attr == "article:modified_time":
            metadata["modified_time"] = content
        elif name_attr == "description" and not metadata["description"]:
            metadata["description"] = content

        # Meta-tason maksumuuritunnistus
        if "paywall" in name_attr and content.lower() in ("true", "yes", "1", "paid", "locked"):
            is_accessible_for_free = False
        elif name_attr == "article:content_tier" and content.lower() in ("locked", "metered", "paywall"):
            is_accessible_for_free = False

    # 2. Etsitään JSON-LD-lohkoja (Schema.org)
    import json

    json_ld_published = None
    json_ld_modified = None
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else (data.get("@graph", [data]) if isinstance(data, dict) else [data])
            for item in items:
                if not isinstance(item, dict):
                    continue
                if "datePublished" in item and not json_ld_published:
                    json_ld_published = item["datePublished"]
                if "dateModified" in item and not json_ld_modified:
                    json_ld_modified = item["dateModified"]

                # JSON-LD Schema.org isAccessibleForFree maksumuuritarkistus
                if "isAccessibleForFree" in item:
                    val = item["isAccessibleForFree"]
                    if val is False or str(val).lower() == "false":
                        is_accessible_for_free = False
                    elif val is True or str(val).lower() == "true":
                        if is_accessible_for_free is None:
                            is_accessible_for_free = True

                has_part = item.get("hasPart")
                has_parts = has_part if isinstance(has_part, list) else [has_part]
                for hp in has_parts:
                    # hasPart.isAccessibleForFree=False tarkoittaa, että OSA sisällöstä on
                    # tilaajille. Ylikirjoitetaan vain jos ylätason arvo ei ole jo True.
                    if isinstance(hp, dict) and hp.get("isAccessibleForFree") is False:
                        if is_accessible_for_free is not True:
                            is_accessible_for_free = False
        except Exception:
            pass

    # JSON-LD datePublished ja dateModified ovat ensisijaisia (prioriteettijärjestys)
    if json_ld_published:
        metadata["published_time"] = json_ld_published
    if json_ld_modified:
        metadata["modified_time"] = json_ld_modified

    metadata["is_accessible_for_free"] = is_accessible_for_free

    # Fallback otsikolle jos og:title puuttuu
    if not metadata["title"]:
        title_tag = soup.find("title")
        if title_tag:
            metadata["title"] = title_tag.get_text()

    return metadata


def longer(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """Palauttaa pidemmän ei-tyhjän merkkijonon. Trim ennen vertailua."""
    a_stripped = (a or "").strip() or None
    b_stripped = (b or "").strip() or None
    if not a_stripped:
        return b_stripped
    if not b_stripped:
        return a_stripped
    return a_stripped if len(a_stripped) >= len(b_stripped) else b_stripped

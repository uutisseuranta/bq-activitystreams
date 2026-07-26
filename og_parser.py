# src/shared/og_parser.py
import ipaddress
import logging
import socket
import time
from typing import Dict, Optional
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


def fetch_url_stream(url: str, timeout: float = TIMEOUT, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    """Hakee sivun turvallisesti redirectejä seuraten ja SSRF-suojauksen tarkistaen.

    Streamataan vain </head>-tagiin asti tai max_bytes kokoon saakka.
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

                    try:
                        # Etsitään head-osion päättymistä
                        text = content_bytes.decode("utf-8", errors="ignore")
                        if "</head>" in text.lower():
                            break
                    except Exception:
                        pass

                return bytes(content_bytes)


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
    """Tarkistaa salliiko robots.txt haun kyseiselle URL-osoitteelle."""
    try:
        rp = get_robots_parser(url)
        return rp.can_fetch(user_agent, url) or rp.can_fetch("*", url)
    except Exception as e:
        logger.warning(f"Error in robots_check for {url}: {e}")
        return True


def parse_og_metadata(html_content: bytes, default_url: str) -> Dict[str, Optional[str]]:
    """Parsii Open Graph ja meta-tagit HTML-sisällöstä."""
    soup = BeautifulSoup(html_content, "lxml")
    metadata: Dict[str, Optional[str]] = {
        "title": None,
        "description": None,
        "image": None,
        "url": default_url,
        "site_name": None,
        "published_time": None,
        "modified_time": None,
    }

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

"""
Terabox URL validation and file metadata fetching.

BUG FIXED: The original code referenced `AURIXS_API_TEMPLATE` which was never
defined, causing a NameError crash on every single download attempt.
Now imports `TERABOX_API_TEMPLATE` from config.py.
"""

import re
import time
from urllib.parse import parse_qs, urlparse

import requests

from config import TERABOX_API_TEMPLATE
from tools import get_formatted_size

TERABOX_PATTERNS = [
    r"ww\.mirrobox\.com",
    r"www\.nephobox\.com",
    r"freeterabox\.com",
    r"www\.freeterabox\.com",
    r"1024tera\.com",
    r"4funbox\.co",
    r"www\.4funbox\.com",
    r"mirrobox\.com",
    r"nephobox\.com",
    r"terabox\.app",
    r"terabox\.com",
    r"www\.terabox\.ap",
    r"www\.terabox\.com",
    r"www\.1024tera\.co",
    r"www\.momerybox\.com",
    r"teraboxapp\.com",
    r"momerybox\.com",
    r"tibibox\.com",
    r"www\.tibibox\.com",
    r"www\.teraboxapp\.com",
]


def check_url_patterns(url: str) -> bool:
    for pattern in TERABOX_PATTERNS:
        if re.search(pattern, url):
            return True
    return False


def get_urls_from_string(string: str):
    urls = re.findall(r"(https?://\S+)", string)
    urls = [u for u in urls if check_url_patterns(u)]
    return urls[0] if urls else None


def extract_surl_from_url(url: str):
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    surl = qs.get("surl", [])
    return surl[0] if surl else None


def retry_request(method: str, url: str, attempts: int = 3, delay: int = 2, **kwargs):
    """Generic retry wrapper for GET / HEAD requests."""
    for i in range(1, attempts + 1):
        try:
            resp = requests.request(method, url, timeout=25, **kwargs)
            if resp.status_code in (200, 302):
                return resp
            print(f"[API][Retry {i}] HTTP {resp.status_code}")
        except Exception as e:
            print(f"[API][Retry {i}] Error: {e}")
        time.sleep(delay)
    return None


def get_data(url: str) -> dict | None:
    """
    Fetch Terabox file metadata via the configured API.
    Returns a dict with file_name, size, sizebytes, thumb, direct_link, link
    or None on failure.
    """
    # ── FIXED: was AURIXS_API_TEMPLATE (NameError) ──
    api_url = TERABOX_API_TEMPLATE.format(url=url)
    print(f"\n[API] Requesting: {api_url}")

    res = retry_request("GET", api_url)
    if not res:
        print("[API] Failed after all retries")
        return None

    print(f"[API] Status: {res.status_code}")

    try:
        data = res.json()
    except Exception as e:
        print(f"[API] JSON parse error: {e}")
        return None

    print(f"[API] Response keys: {list(data.keys())}")

    fast_link = data.get("directlink")
    if not fast_link:
        print("[API] No 'directlink' in response — check API token / URL")
        return None

    size_bytes = int(data.get("sizebytes", 0))

    # Resolve any HTTP redirects to get the final CDN URL
    head = retry_request("HEAD", fast_link, allow_redirects=True)
    direct_url = head.url if head else fast_link

    return {
        "file_name": data.get("file_name", "file"),
        "size": data.get("size") or get_formatted_size(size_bytes),
        "sizebytes": size_bytes,
        "thumb": data.get("thumb"),
        "direct_link": direct_url,
        "link": fast_link,
    }

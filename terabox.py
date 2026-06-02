"""
Self-contained Terabox resolver — no third-party API needed.
Uses your own Terabox session cookies directly against Terabox's web API.
A free Terabox account is sufficient for publicly shared files.

Cookie setup (one-time):
  1. Log in at https://www.terabox.com in Chrome or Firefox
  2. Press F12 → Network tab → reload the page
  3. Click any request to terabox.com → Headers → find "Cookie:"
  4. Copy the full value and set it as TERABOX_COOKIE in your env / Railway vars

Common errno values from the Terabox API:
  0   → success
  -6  → cookie expired or invalid — refresh your cookie
  -9  → file not found or link expired
  2   → share link password required (not supported)
"""

import re
import requests
from urllib.parse import urlparse, parse_qs

from tools import get_formatted_size


# ── Accepted URL patterns ────────────────────────────────────────────────────
TERABOX_PATTERNS = [
    r"ww\.mirrobox\.com", r"www\.nephobox\.com",
    r"freeterabox\.com", r"www\.freeterabox\.com",
    r"1024tera\.com", r"4funbox\.co", r"www\.4funbox\.com",
    r"mirrobox\.com", r"nephobox\.com",
    r"terabox\.app", r"terabox\.com",
    r"www\.terabox\.ap", r"www\.terabox\.com",
    r"www\.1024tera\.co", r"www\.momerybox\.com",
    r"teraboxapp\.com", r"momerybox\.com",
    r"tibibox\.com", r"www\.tibibox\.com", r"www\.teraboxapp\.com",
]

_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.terabox.com/",
    "Origin":          "https://www.terabox.com",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def check_url_patterns(url: str) -> bool:
    return any(re.search(p, url) for p in TERABOX_PATTERNS)


def get_urls_from_string(string: str):
    urls = re.findall(r"(https?://\S+)", string)
    valid = [u for u in urls if check_url_patterns(u)]
    return valid[0] if valid else None


def extract_surl(url: str) -> str | None:
    """Returns the short-URL key regardless of which Terabox-family domain is used."""
    m = re.search(r'/s/(\w+)', url)
    if m:
        return m.group(1)
    qs = parse_qs(urlparse(url).query)
    return qs["surl"][0] if "surl" in qs else None


def _headers(cookie: str) -> dict:
    return {**_BASE, "Cookie": cookie}


# ── Core resolver ─────────────────────────────────────────────────────────────

def get_data(url: str, cookie: str) -> dict | None:
    """
    Resolve a Terabox shared link to a direct downloadable URL.

    Returns dict(file_name, size, sizebytes, thumb, direct_link, link)
    or None on any failure.
    """
    if not cookie:
        print(
            "[Terabox] ❌ TERABOX_COOKIE is empty.\n"
            "   Set it in your .env or Railway Variables.\n"
            "   See the comment at the top of terabox.py for instructions."
        )
        return None

    surl = extract_surl(url)
    if not surl:
        print(f"[Terabox] ❌ Cannot extract surl from: {url}")
        return None

    h = _headers(cookie)

    # ── Step 1: fetch the share page → grab jsToken + logid ─────────────────
    try:
        page = requests.get(
            f"https://www.terabox.com/wap/share/filelist?surl={surl}",
            headers=h, timeout=20,
        )
    except Exception as e:
        print(f"[Terabox] ❌ Page request failed: {e}")
        return None

    if page.status_code != 200:
        print(f"[Terabox] ❌ Share page returned HTTP {page.status_code}")
        return None

    js_token = ""
    for pat in [
        r'"jsToken"\s*:\s*"([^"]+)"',
        r"jsToken\s*=\s*['\"]([^'\"]+)['\"]",
        r'locals\.mset\([^)]*?"jsToken"\s*:\s*"([^"]+)"',
    ]:
        m = re.search(pat, page.text)
        if m:
            js_token = m.group(1)
            break

    logid_m = re.search(r'dp-logid=([^&\s"\']+)', page.text)
    log_id  = logid_m.group(1) if logid_m else "0"

    print(f"[Terabox] surl={surl} | jsToken={'✓' if js_token else '✗ not found'} | logid={log_id}")

    # ── Step 2: get file list ────────────────────────────────────────────────
    try:
        lr = requests.get(
            "https://www.terabox.com/share/list",
            params={
                "app_id": "250528", "web": "1",
                "shorturl": surl,   "root": "1",
                "num": "20",        "page": "1",
                "by": "name",       "order": "asc",
                "dp-logid": log_id, "jsToken": js_token,
            },
            headers=h, timeout=20,
        )
        list_data = lr.json()
    except Exception as e:
        print(f"[Terabox] ❌ File-list request failed: {e}")
        return None

    errno = list_data.get("errno", -1)
    if errno != 0:
        hint = ""
        if errno == -6:
            hint = " → Your cookie is expired or invalid. Refresh it."
        elif errno == -9:
            hint = " → The share link is broken or has expired."
        elif errno == 2:
            hint = " → This link requires a password (not supported)."
        print(f"[Terabox] ❌ File-list API errno={errno}{hint}")
        return None

    files = list_data.get("list", [])
    if not files:
        print("[Terabox] ❌ File list is empty.")
        return None

    file     = files[0]
    fs_id    = str(file["fs_id"])
    filename = file.get("server_filename", "file")
    size     = int(file.get("size", 0))
    thumbs   = file.get("thumbs", {})
    thumb    = thumbs.get("url3") or thumbs.get("url1") or ""

    print(f"[Terabox] File: {filename} | {get_formatted_size(size)} | fs_id={fs_id}")

    # ── Step 3: get shareid + uk ─────────────────────────────────────────────
    shareid = uk = ""
    try:
        ir = requests.get(
            "https://www.terabox.com/api/shorturlinfo",
            params={"app_id": "250528", "shorturl": surl, "root": "1"},
            headers=h, timeout=20,
        )
        info    = ir.json()
        shareid = str(info.get("shareid", ""))
        uk      = str(info.get("uk", ""))
        print(f"[Terabox] shareid={shareid} | uk={uk}")
    except Exception as e:
        print(f"[Terabox] ⚠ shorturlinfo failed (non-fatal): {e}")

    # ── Step 4: get download link ─────────────────────────────────────────────
    try:
        dr = requests.get(
            "https://www.terabox.com/api/download",
            params={
                "app_id":   "250528", "web":     "1",
                "shorturl": surl,     "jsToken": js_token,
                "dplogid":  log_id,   "fs_id":   fs_id,
                "shareId":  shareid,  "uk":      uk,
            },
            headers=h, timeout=20,
        )
        dl_data = dr.json()
    except Exception as e:
        print(f"[Terabox] ❌ Download-link request failed: {e}")
        return None

    dl_errno = dl_data.get("errno", -1)
    if dl_errno != 0:
        print(f"[Terabox] ❌ Download API errno={dl_errno} | {dl_data.get('errmsg', '')}")
        return None

    dlink = dl_data.get("dlink", "")
    if not dlink:
        print("[Terabox] ❌ Response has no 'dlink' field.")
        return None

    # Follow any CDN redirect to get the final URL
    try:
        final      = requests.head(dlink, headers=h, allow_redirects=True, timeout=20)
        direct_url = final.url
    except Exception:
        direct_url = dlink

    print(f"[Terabox] ✅ Done → {filename}")

    return {
        "file_name":   filename,
        "size":        get_formatted_size(size),
        "sizebytes":   size,
        "thumb":       thumb,
        "direct_link": direct_url,
        "link":        dlink,
    }

"""
Self-contained Terabox resolver.
Uses your own Terabox session cookies against Terabox's web API.
A free Terabox account works for publicly shared files.

get_data() returns (dict, "") on success OR (None, "reason") on failure.

errno=4000020 fix: Terabox now requires a `bdstoken` parameter on most API calls.
bdstoken == csrfToken from your cookie. We now extract it automatically.
"""

import re
import requests
from urllib.parse import urlparse, parse_qs

from tools import get_formatted_size


TERABOX_PATTERNS = [
    r"ww\.mirrobox\.com", r"www\.nephobox\.com",
    r"freeterabox\.com",  r"www\.freeterabox\.com",
    r"1024tera\.com",     r"4funbox\.co",  r"www\.4funbox\.com",
    r"mirrobox\.com",     r"nephobox\.com",
    r"terabox\.app",      r"terabox\.com",
    r"www\.terabox\.ap",  r"www\.terabox\.com",
    r"www\.1024tera\.co", r"www\.momerybox\.com",
    r"teraboxapp\.com",   r"momerybox\.com",
    r"tibibox\.com",      r"www\.tibibox\.com",
    r"www\.teraboxapp\.com",
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

_JS_PATTERNS = [
    r'"jsToken"\s*:\s*"([^"]+)"',
    r"jsToken\s*=\s*['\"]([^'\"]+)['\"]",
    r"'jsToken'\s*:\s*'([^']+)'",
    r'window\.jsToken\s*=\s*"([^"]+)"',
    r'jsToken\s*:\s*"([^"]+)"',
]

_BDSTOKEN_PATTERNS = [
    r'"bdstoken"\s*:\s*"([^"]+)"',
    r"bdstoken\s*=\s*['\"]([^'\"]+)['\"]",
    r"'bdstoken'\s*:\s*'([^']+)'",
]


# ── Public helpers ─────────────────────────────────────────────────────────────

def check_url_patterns(url: str) -> bool:
    return any(re.search(p, url) for p in TERABOX_PATTERNS)


def get_urls_from_string(string: str):
    urls = re.findall(r"(https?://\S+)", string)
    valid = [u for u in urls if check_url_patterns(u)]
    return valid[0] if valid else None


def extract_surl(url: str) -> str | None:
    m = re.search(r'/s/(\w+)', url)
    if m:
        return m.group(1)
    qs = parse_qs(urlparse(url).query)
    return qs["surl"][0] if "surl" in qs else None


def _h(cookie: str) -> dict:
    return {**_BASE, "Cookie": cookie}


# ── Token extraction ───────────────────────────────────────────────────────────

def _cookie_field(cookie: str, key: str) -> str:
    """Extract a single field value from a Cookie header string."""
    m = re.search(rf'(?:^|;)\s*{re.escape(key)}=([^;]+)', cookie)
    return m.group(1).strip() if m else ""


def _get_bdstoken(cookie: str, surl: str) -> str:
    """
    bdstoken is required by Terabox's share/list and download APIs.
    It equals csrfToken from the session cookie in almost all cases.
    Falls back to fetching it from the API or page source.
    """
    # Strategy 1 (fastest): csrfToken from cookie string
    csrf = _cookie_field(cookie, "csrfToken")
    if csrf:
        print(f"[Terabox] bdstoken from cookie csrfToken: {csrf[:12]}…")
        return csrf

    h = _h(cookie)

    # Strategy 2: dedicated API endpoint
    try:
        r = requests.get(
            "https://www.terabox.com/api/gettemplatevariable",
            params={"fields": '["bdstoken"]'},
            headers=h, timeout=10,
        )
        tok = r.json().get("result", {}).get("bdstoken", "")
        if tok:
            print(f"[Terabox] bdstoken from gettemplatevariable: {tok[:12]}…")
            return tok
    except Exception as e:
        print(f"[Terabox] gettemplatevariable failed: {e}")

    # Strategy 3: scrape from share page HTML
    for page_url in [
        f"https://www.terabox.com/wap/share/filelist?surl={surl}",
        f"https://www.terabox.com/sharing/link?surl={surl}",
    ]:
        try:
            r = requests.get(page_url, headers=h, timeout=20)
            for pat in _BDSTOKEN_PATTERNS:
                m = re.search(pat, r.text)
                if m:
                    print(f"[Terabox] bdstoken from page {page_url}: {m.group(1)[:12]}…")
                    return m.group(1)
        except Exception:
            pass

    print("[Terabox] ⚠ bdstoken not found — API calls may fail with errno=4000020")
    return ""


def _get_page_tokens(surl: str, cookie: str) -> tuple[str, str]:
    """Returns (js_token, log_id) by scraping the share page."""
    h = _h(cookie)
    pages = [
        f"https://www.terabox.com/wap/share/filelist?surl={surl}",
        f"https://www.terabox.com/sharing/link?surl={surl}",
        f"https://www.terabox.com/s/{surl}",
    ]
    for page_url in pages:
        try:
            r = requests.get(page_url, headers=h, timeout=20, allow_redirects=True)
            if r.status_code != 200:
                continue
            text = r.text
            js_token = ""
            for pat in _JS_PATTERNS:
                m = re.search(pat, text)
                if m:
                    js_token = m.group(1)
                    break
            logid_m = re.search(r'dp-logid=([^&\s"\']+)', text)
            log_id  = logid_m.group(1) if logid_m else "0"
            if js_token:
                print(f"[Terabox] jsToken found via {page_url}")
                return js_token, log_id
        except Exception as e:
            print(f"[Terabox] Page {page_url} failed: {e}")
    print("[Terabox] jsToken not found — continuing without it")
    return "", "0"


# ── API steps ──────────────────────────────────────────────────────────────────

def _get_file_list(surl: str, js_token: str, bdstoken: str,
                   log_id: str, h: dict) -> tuple[list | None, str]:
    params = {
        "app_id": "250528", "web":  "1",
        "shorturl": surl,   "root": "1",
        "num": "20",        "page": "1",
        "by": "name",       "order": "asc",
    }
    if js_token:
        params["jsToken"]  = js_token
    if bdstoken:
        params["bdstoken"] = bdstoken      # ← THE FIX for errno=4000020
    if log_id and log_id != "0":
        params["dp-logid"] = log_id

    try:
        r     = requests.get("https://www.terabox.com/share/list",
                             params=params, headers=h, timeout=20)
        data  = r.json()
        errno = data.get("errno", -1)
        if errno != 0:
            hints = {
                -6:       " → cookie expired/invalid, refresh TERABOX_COOKIE",
                -9:       " → link broken or expired",
                 2:       " → link needs a password (not supported)",
                 4000020: " → bdstoken missing or wrong (check csrfToken in cookie)",
            }
            return None, f"share/list errno={errno}{hints.get(errno, '')}"
        files = data.get("list", [])
        return (files or None), ("" if files else "share/list returned empty list")
    except Exception as e:
        return None, f"share/list request error: {e}"


def _get_shareid_uk(surl: str, bdstoken: str, h: dict) -> tuple[str, str]:
    params = {"app_id": "250528", "shorturl": surl, "root": "1"}
    if bdstoken:
        params["bdstoken"] = bdstoken
    try:
        r    = requests.get("https://www.terabox.com/api/shorturlinfo",
                            params=params, headers=h, timeout=20)
        info = r.json()
        # shorturlinfo sometimes contains the file list too — opportunistic grab
        return str(info.get("shareid", "")), str(info.get("uk", ""))
    except Exception:
        return "", ""


def _get_dlink(surl: str, fs_id: str, shareid: str, uk: str,
               js_token: str, bdstoken: str, log_id: str,
               h: dict) -> tuple[str | None, str]:
    params = {
        "app_id":  "250528", "web":     "1",
        "shorturl": surl,    "fs_id":   fs_id,
        "shareId": shareid,  "uk":      uk,
    }
    if js_token:
        params["jsToken"]  = js_token
    if bdstoken:
        params["bdstoken"] = bdstoken      # ← also needed here
    if log_id and log_id != "0":
        params["dplogid"]  = log_id

    try:
        r     = requests.get("https://www.terabox.com/api/download",
                             params=params, headers=h, timeout=20)
        data  = r.json()
        errno = data.get("errno", -1)
        if errno != 0:
            return None, f"download API errno={errno} ({data.get('errmsg', '')})"
        dlink = data.get("dlink", "")
        return (dlink or None), ("" if dlink else "download API returned no dlink")
    except Exception as e:
        return None, f"download API error: {e}"


# ── Main resolver ──────────────────────────────────────────────────────────────

def get_data(url: str, cookie: str) -> tuple[dict | None, str]:
    """
    Returns (data_dict, "")          on success
    Returns (None,  "reason string") on failure  ← shown in Telegram
    """
    if not cookie:
        return None, "TERABOX_COOKIE is not set — check your env variables"

    surl = extract_surl(url)
    if not surl:
        return None, f"Could not extract surl from URL: {url}"

    h = _h(cookie)
    print(f"\n[Terabox] ── Resolving surl={surl} ─────────────────────")

    # ── Gather tokens ─────────────────────────────────────────────────────────
    bdstoken             = _get_bdstoken(cookie, surl)
    js_token, log_id     = _get_page_tokens(surl, cookie)

    # ── File list: try with all tokens, then without jsToken ──────────────────
    files, err = _get_file_list(surl, js_token, bdstoken, log_id, h)
    if files is None and js_token:
        print(f"[Terabox] Retry share/list without jsToken…")
        files, err = _get_file_list(surl, "", bdstoken, "0", h)
    if files is None:
        return None, err

    file      = files[0]
    fs_id     = str(file["fs_id"])
    filename  = file.get("server_filename", "file")
    size      = int(file.get("size", 0))
    thumbs    = file.get("thumbs", {})
    thumb     = thumbs.get("url3") or thumbs.get("url1") or ""
    list_dlink = file.get("dlink", "")  # sometimes present in the list itself

    print(f"[Terabox] File: {filename} | {get_formatted_size(size)} | fs_id={fs_id}")

    # ── shareid + uk ──────────────────────────────────────────────────────────
    shareid, uk = _get_shareid_uk(surl, bdstoken, h)
    print(f"[Terabox] shareid={shareid or '(empty)'} | uk={uk or '(empty)'}")

    # ── Download link: with tokens → without jsToken → list fallback ──────────
    dlink, dl_err = _get_dlink(surl, fs_id, shareid, uk, js_token, bdstoken, log_id, h)
    if not dlink and js_token:
        print("[Terabox] Retry dlink without jsToken…")
        dlink, dl_err = _get_dlink(surl, fs_id, shareid, uk, "", bdstoken, "0", h)
    if not dlink and list_dlink:
        print("[Terabox] Using dlink embedded in file list (fallback)")
        dlink = list_dlink
    if not dlink:
        return None, dl_err

    # ── Resolve CDN redirect ──────────────────────────────────────────────────
    try:
        final      = requests.head(dlink, headers=h, allow_redirects=True, timeout=20)
        direct_url = final.url
    except Exception:
        direct_url = dlink

    print(f"[Terabox] ✅ Resolved: {filename}")

    return {
        "file_name":   filename,
        "size":        get_formatted_size(size),
        "sizebytes":   size,
        "thumb":       thumb,
        "direct_link": direct_url,
        "link":        dlink,
    }, ""

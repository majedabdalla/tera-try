"""
Terabox resolver — clean rebuild.

What the debug session revealed:
  ✅ Cookie valid
  ✅ shorturlinfo works  → gives us files, shareid, uk, sign, timestamp
  ❌ share/list fails    → errno unknown, jsToken missing
  ❌ /api/download errno=2 even with sign+timestamp present

Root causes of errno=2 (confirmed by comparing our request with real browser traffic):
  1.  shareId  ≠  shareid   — Terabox API is case-sensitive; browsers send lowercase
  2.  Missing channel=dubox, clienttype=0  — required by current API
  3.  Referer was "https://www.terabox.com/" instead of the share page URL
  4.  No X-Requested-With: XMLHttpRequest header
  5.  No session warm-up  — browser visits the share page first; the download
      endpoint checks that the session cookie has a "seen this share" state
"""

import json
import re
import requests
from urllib.parse import urlparse, parse_qs, unquote

from tools import get_formatted_size


TERABOX_PATTERNS = [
    r"ww\.mirrobox\.com",     r"www\.nephobox\.com",
    r"freeterabox\.com",      r"www\.freeterabox\.com",
    r"1024tera\.com",         r"4funbox\.co",
    r"www\.4funbox\.com",     r"mirrobox\.com",
    r"nephobox\.com",         r"terabox\.app",
    r"terabox\.com",          r"www\.terabox\.ap",
    r"www\.terabox\.com",     r"www\.1024tera\.co",
    r"www\.momerybox\.com",   r"teraboxapp\.com",
    r"momerybox\.com",        r"tibibox\.com",
    r"www\.tibibox\.com",     r"www\.teraboxapp\.com",
]

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)

_BASE_HEADERS = {
    "User-Agent":      _UA,
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin":          "https://www.terabox.com",
}

_JS_PATTERNS = [
    r'"jsToken"\s*:\s*"([^"]+)"',
    r"jsToken\s*=\s*['\"]([^'\"]+)['\"]",
    r"'jsToken'\s*:\s*'([^']+)'",
    r'window\.jsToken\s*=\s*"([^"]+)"',
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def check_url_patterns(url: str) -> bool:
    return any(re.search(p, url) for p in TERABOX_PATTERNS)


def get_urls_from_string(string: str) -> str | None:
    urls = re.findall(r"(https?://\S+)", string)
    valid = [u for u in urls if check_url_patterns(u)]
    return valid[0] if valid else None


def extract_surl(url: str) -> str | None:
    m = re.search(r'/s/(\w+)', url)
    if m:
        return m.group(1)
    qs = parse_qs(urlparse(url).query)
    return qs["surl"][0] if "surl" in qs else None


def _cookie_field(cookie: str, key: str) -> str:
    m = re.search(rf'(?:^|;)\s*{re.escape(key)}=([^;]+)', cookie)
    return unquote(m.group(1).strip()) if m else ""


def _headers(cookie: str, referer: str = "https://www.terabox.com/") -> dict:
    return {**_BASE_HEADERS, "Cookie": cookie, "Referer": referer}


def _make_session(cookie: str) -> requests.Session:
    """
    Build a requests.Session pre-loaded with the user's cookie string.
    Using a Session means any cookies set during the share-page visit
    are automatically carried into the download request.
    """
    s = requests.Session()
    s.headers.update(_BASE_HEADERS)
    for part in cookie.split(";"):
        part = part.strip()
        if "=" in part:
            name, _, value = part.partition("=")
            s.cookies.set(name.strip(), value.strip(), domain=".terabox.com")
    return s


# ─────────────────────────────────────────────────────────────────────────────
# Token extraction
# ─────────────────────────────────────────────────────────────────────────────

def _get_bdstoken(cookie: str) -> str:
    """csrfToken cookie field = bdstoken in all current Terabox versions."""
    tok = _cookie_field(cookie, "csrfToken")
    if tok:
        print(f"[TB] bdstoken from csrfToken: {tok[:16]}…")
        return tok
    # Fallback: API
    try:
        r = requests.get(
            "https://www.terabox.com/api/gettemplatevariable",
            params={"fields": '["bdstoken"]'},
            headers=_headers(cookie), timeout=10,
        )
        tok = r.json().get("result", {}).get("bdstoken", "")
        if tok:
            print(f"[TB] bdstoken from API: {tok[:16]}…")
            return tok
    except Exception:
        pass
    print("[TB] ⚠ bdstoken not found")
    return ""


def _get_js_token(surl: str, cookie: str) -> tuple[str, str]:
    """Returns (js_token, dp_logid) scraped from the share page."""
    h = _headers(cookie, referer=f"https://www.terabox.com/s/{surl}")
    for url in [
        f"https://www.terabox.com/wap/share/filelist?surl={surl}",
        f"https://www.terabox.com/sharing/link?surl={surl}",
    ]:
        try:
            r = requests.get(url, headers=h, timeout=20, allow_redirects=True)
            if r.status_code != 200:
                continue
            text = r.text
            js_token = ""
            for p in _JS_PATTERNS:
                m = re.search(p, text)
                if m:
                    js_token = m.group(1)
                    break
            m = re.search(r'dp-logid=([^&\s"\']+)', text)
            log_id = m.group(1) if m else ""
            if js_token:
                return js_token, log_id
        except Exception:
            pass
    return "", ""


# ─────────────────────────────────────────────────────────────────────────────
# File info  (Strategy A: shorturlinfo — most reliable, returns sign too)
# ─────────────────────────────────────────────────────────────────────────────

def _try_shorturlinfo(surl: str, bdstoken: str, h: dict) -> tuple:
    """
    Returns (files, shareid, uk, sign, timestamp, error).
    The sign+timestamp in this response are the per-share auth tokens
    needed by /api/download.
    """
    for params in [
        {"app_id": "250528", "web": "1", "shorturl": surl, "root": "1", "bdstoken": bdstoken},
        {"app_id": "250528", "web": "1", "shorturl": surl, "root": "1"},
        {"app_id": "250528",             "shorturl": surl, "root": "1"},
    ]:
        try:
            r    = requests.get("https://www.terabox.com/api/shorturlinfo",
                                params=params, headers=h, timeout=20)
            data = r.json()
            if data.get("errno", -1) != 0:
                continue
            shareid   = str(data.get("shareid", ""))
            uk        = str(data.get("uk", ""))
            sign      = str(data.get("sign", ""))
            timestamp = str(data.get("timestamp", ""))
            files     = data.get("list", [])
            if sign:
                print(f"[TB] sign from shorturlinfo: …{sign[-8:]}")
            if files:
                print(f"[TB] ✅ shorturlinfo — {len(files)} file(s)")
                return files, shareid, uk, sign, timestamp, ""
            # Got auth data but no file list
            return None, shareid, uk, sign, timestamp, "shorturlinfo: no list"
        except Exception:
            continue
    return None, "", "", "", "", "shorturlinfo failed all combos"


def _try_page_scrape(surl: str, cookie: str) -> tuple:
    """
    Fallback: scrape file data from the share page HTML.
    Returns (files, shareid, uk, error).
    """
    h = _headers(cookie, referer=f"https://www.terabox.com/s/{surl}")
    for url in [
        f"https://www.terabox.com/wap/share/filelist?surl={surl}",
        f"https://www.terabox.com/s/{surl}",
        f"https://www.terabox.com/sharing/link?surl={surl}",
    ]:
        try:
            r = requests.get(url, headers=h, timeout=25, allow_redirects=True)
            if r.status_code != 200:
                continue
            text = r.text

            for pattern in [
                r'locals\.mset\((\{.{50,}\})\)',
                r'window\.__redux_state__\s*=\s*(\{.+?\})\s*;',
            ]:
                m = re.search(pattern, text, re.DOTALL)
                if not m:
                    continue
                try:
                    obj = json.loads(m.group(1))
                    # Handle redux state nesting
                    if "share" in obj:
                        obj = obj["share"]
                        files = obj.get("fileinfo", {}).get("list", []) or obj.get("list", [])
                    else:
                        files = obj.get("list", [])
                    if files:
                        print(f"[TB] ✅ page scrape via {url}")
                        return (files, str(obj.get("shareid", "")),
                                str(obj.get("uk", "")), "")
                except Exception:
                    pass

            m = re.search(r'"list"\s*:\s*(\[\{.+?\}\])', text, re.DOTALL)
            if m:
                try:
                    files = json.loads(m.group(1))
                    if files and files[0].get("fs_id"):
                        print(f"[TB] ✅ page scrape (list fragment) via {url}")
                        return files, "", "", ""
                except Exception:
                    pass
        except Exception as e:
            print(f"[TB] page scrape {url}: {e}")

    return None, "", "", "page scraping found no data"


def _try_share_list(surl: str, bdstoken: str, js_token: str,
                    log_id: str, h: dict) -> tuple[list | None, str]:
    """
    Returns (files, error).
    Files from share/list include a 'dlink' field — no /api/download needed.
    """
    base = {
        "app_id": "250528", "web": "1", "shorturl": surl, "root": "1",
        "num": "20", "page": "1", "by": "name", "order": "asc",
    }
    for params in [
        {**base, "bdstoken": bdstoken, "jsToken": js_token, "dp-logid": log_id},
        {**base, "bdstoken": bdstoken},
        {**base, "bdstoken": bdstoken, "channel": "dubox", "clienttype": "0"},
        {**base},
    ]:
        params = {k: v for k, v in params.items() if v and v != "0"}
        try:
            r    = requests.get("https://www.terabox.com/share/list",
                                params=params, headers=h, timeout=20)
            data = r.json()
            errno = data.get("errno", -1)
            if errno == 0:
                files = data.get("list", [])
                if files:
                    print(f"[TB] ✅ share/list — {len(files)} file(s)"
                          f" | dlink={'yes' if files[0].get('dlink') else 'no'}")
                    return files, ""
            print(f"[TB] share/list errno={errno}")
        except Exception as e:
            print(f"[TB] share/list error: {e}")
    return None, "share/list failed all combos"


# ─────────────────────────────────────────────────────────────────────────────
# Download link  (rebuilt from scratch)
# ─────────────────────────────────────────────────────────────────────────────

def _get_dlink(surl: str, fs_id: str, shareid: str, uk: str,
               bdstoken: str, sign: str, timestamp: str,
               js_token: str, log_id: str,
               cookie: str) -> tuple[str | None, str]:
    """
    Obtain a signed CDN download link.

    Key fixes vs previous version:
      • shareId  →  shareid  (API is case-sensitive; browsers always lowercase)
      • Added channel=dubox, clienttype=0  (required by current Terabox API)
      • Referer = share page URL, not just terabox.com
      • X-Requested-With: XMLHttpRequest  (marks it as AJAX, required)
      • requests.Session that first visits the share page — the download
        endpoint validates that the cookie session has "seen" the share
    """
    share_url = f"https://www.terabox.com/s/{surl}"

    # Build a session: pre-load cookies, then warm up with a share-page visit.
    session = _make_session(cookie)
    try:
        session.get(share_url, timeout=15, allow_redirects=True)
        print("[TB] share page visited (session warmed)")
    except Exception as e:
        print(f"[TB] share page visit failed (non-fatal): {e}")

    # Headers that all download requests share.
    dl_headers = {
        "Referer":            share_url,
        "X-Requested-With":   "XMLHttpRequest",
    }

    # Parameter combos — ordered best-first.
    # IMPORTANT: parameter name is "shareid" (all lowercase), not "shareId".
    common = {
        "app_id":     "250528",
        "web":        "1",
        "channel":    "dubox",
        "clienttype": "0",
        "shareid":    shareid,   # ← lowercase
        "uk":         uk,
        "fs_id":      fs_id,
    }
    combos = [
        # Full: sign + all auth tokens
        {**common, "sign": sign, "timestamp": timestamp,
         "bdstoken": bdstoken, "dp-logid": log_id},
        # sign without logid
        {**common, "sign": sign, "timestamp": timestamp, "bdstoken": bdstoken},
        # sign, no bdstoken
        {**common, "sign": sign, "timestamp": timestamp},
        # sign + shorturl (some API versions key on shorturl instead of shareid)
        {**common, "sign": sign, "timestamp": timestamp,
         "shorturl": surl, "bdstoken": bdstoken},
        # Legacy: no sign
        {**common, "bdstoken": bdstoken},
        {**common},
    ]

    errors = []
    for params in combos:
        params = {k: v for k, v in params.items() if v and v != "0"}
        try:
            r    = session.get("https://www.terabox.com/api/download",
                               params=params, headers=dl_headers, timeout=20)
            # Guard against non-JSON (empty body, HTML redirect, etc.)
            try:
                data = r.json()
            except Exception:
                errors.append(f"non-JSON response (HTTP {r.status_code})")
                print(f"[TB] download API non-JSON: HTTP {r.status_code} body={r.text[:80]}")
                continue
            errno = data.get("errno", -1)
            if errno == 0:
                dlink = data.get("dlink", "")
                if dlink:
                    print("[TB] ✅ dlink obtained")
                    return dlink, ""
            errmsg = data.get("errmsg", "")
            err = f"errno={errno}({errmsg}) keys={list(params.keys())}"
            errors.append(err)
            print(f"[TB] download API: {err}")
        except Exception as e:
            errors.append(f"exc:{e}")
            print(f"[TB] download API exception: {e}")

    return None, "download API failed — " + " | ".join(errors[:3])


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def get_data(url: str, cookie: str) -> tuple[dict | None, str]:
    """
    Returns (data_dict, "")         on success.
    Returns (None, "reason string") on failure.
    """
    if not cookie:
        return None, "TERABOX_COOKIE is not set"

    surl = extract_surl(url)
    if not surl:
        return None, f"Cannot extract surl from: {url}"

    h        = _headers(cookie, referer=f"https://www.terabox.com/s/{surl}")
    bdstoken = _get_bdstoken(cookie)
    js_token, log_id = _get_js_token(surl, cookie)

    print(f"\n[TB] ── surl={surl} | bds={'yes' if bdstoken else 'NO'}"
          f" | js={'yes' if js_token else 'no'} ──")

    # ── Step 1: get file metadata + sign ──────────────────────────────────────
    files     = None
    shareid   = uk = sign = timestamp = ""
    err_b = err_c = "not attempted"

    # Primary: shorturlinfo (also gives us sign+timestamp)
    files_a, shareid_a, uk_a, sign_a, ts_a, err_a = _try_shorturlinfo(surl, bdstoken, h)
    if files_a:
        files, shareid, uk, sign, timestamp = files_a, shareid_a, uk_a, sign_a, ts_a
    else:
        shareid, uk, sign, timestamp = shareid_a, uk_a, sign_a, ts_a

    # Fallback A: share/list (also provides pre-signed dlink per file)
    list_dlink = ""
    if not files:
        files_b, err_b = _try_share_list(surl, bdstoken, js_token, log_id, h)
        if files_b:
            files = files_b
            list_dlink = files_b[0].get("dlink", "")

    # Fallback B: page scrape
    if not files or not shareid or not uk:
        files_c, shareid_c, uk_c, err_c = _try_page_scrape(surl, cookie)
        if not files and files_c:
            files = files_c
        shareid = shareid or shareid_c
        uk      = uk or uk_c

    if not files:
        return None, (
            f"All file-info strategies failed.\n"
            f"shorturlinfo: {err_a}\n"
            f"share/list: {err_b}\n"
            f"page scrape: {err_c}"
        )

    # If share/list gave us files but not a dlink, try again targeted
    if not list_dlink and files:
        list_dlink = files[0].get("dlink", "")

    file      = files[0]
    fs_id     = str(file["fs_id"])
    filename  = file.get("server_filename", "file")
    size      = int(file.get("size", 0))
    thumbs    = file.get("thumbs", {})
    thumb     = thumbs.get("url3") or thumbs.get("url1") or ""

    print(f"[TB] file={filename} | {get_formatted_size(size)}"
          f" | shareid={'yes' if shareid else 'NO'}"
          f" | sign={'yes' if sign else 'NO'}")

    # ── Step 2: if share/list gave us a ready dlink, prefer it ────────────────
    # share/list dlinks are pre-signed CDN redirect URLs — no /api/download needed.
    if list_dlink:
        print("[TB] using pre-signed dlink from share/list")
        dlink = list_dlink
    else:
        # ── Step 3: call /api/download (rebuilt with fixed params/headers) ────
        dlink, dl_err = _get_dlink(
            surl, fs_id, shareid, uk,
            bdstoken, sign, timestamp,
            js_token, log_id,
            cookie,
        )
        if not dlink:
            return None, f"Could not get download link: {dl_err}"

    # ── Step 4: follow CDN redirect to get final URL ──────────────────────────
    try:
        final      = requests.head(dlink, headers=h, allow_redirects=True, timeout=20)
        direct_url = final.url
    except Exception:
        direct_url = dlink

    print(f"[TB] ✅ done: {filename}")
    return {
        "file_name":   filename,
        "size":        get_formatted_size(size),
        "sizebytes":   size,
        "thumb":       thumb,
        "direct_link": direct_url,
        "link":        dlink,
    }, ""

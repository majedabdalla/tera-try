"""
Self-contained Terabox resolver.
get_data() returns (dict, "") on success or (None, "reason") on failure.

errno=4000020 investigation:
  - Cookie valid ✅  - bdstoken found ✅  - but share/list still 4000020
  This means Terabox changed share/list to need extra params beyond bdstoken.
  Fix: use shorturlinfo as primary source (less strict), plus page scraping
  as fallback.
"""

import json
import re
import requests
from urllib.parse import urlparse, parse_qs, unquote

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


def _h(cookie: str, referer: str = "https://www.terabox.com/") -> dict:
    return {**_BASE, "Cookie": cookie, "Referer": referer}


def _cookie_field(cookie: str, key: str) -> str:
    m = re.search(rf'(?:^|;)\s*{re.escape(key)}=([^;]+)', cookie)
    return unquote(m.group(1).strip()) if m else ""   # URL-decode automatically


# ── Token helpers ──────────────────────────────────────────────────────────────

def _get_bdstoken(cookie: str, surl: str) -> str:
    """bdstoken = URL-decoded csrfToken from cookie in almost all cases."""
    csrf = _cookie_field(cookie, "csrfToken")   # already URL-decoded by _cookie_field
    if csrf:
        print(f"[Terabox] bdstoken (csrfToken): {csrf[:16]}…")
        return csrf

    # Fallback: dedicated API
    try:
        r   = requests.get(
            "https://www.terabox.com/api/gettemplatevariable",
            params={"fields": '["bdstoken"]'},
            headers=_h(cookie), timeout=10,
        )
        tok = r.json().get("result", {}).get("bdstoken", "")
        if tok:
            print(f"[Terabox] bdstoken from API: {tok[:16]}…")
            return tok
    except Exception:
        pass

    print("[Terabox] ⚠ bdstoken not found")
    return ""


def _get_js_token(surl: str, cookie: str) -> tuple[str, str]:
    """Returns (js_token, log_id) from the share page."""
    h = _h(cookie)
    for page_url in [
        f"https://www.terabox.com/wap/share/filelist?surl={surl}",
        f"https://www.terabox.com/sharing/link?surl={surl}",
    ]:
        try:
            r = requests.get(page_url, headers=h, timeout=20, allow_redirects=True)
            if r.status_code != 200:
                continue
            text     = r.text
            js_token = ""
            for pat in _JS_PATTERNS:
                m = re.search(pat, text)
                if m:
                    js_token = m.group(1)
                    break
            logid_m = re.search(r'dp-logid=([^&\s"\']+)', text)
            log_id  = logid_m.group(1) if logid_m else "0"
            if js_token:
                return js_token, log_id
        except Exception:
            pass
    return "", "0"


# ── File info strategies (ordered best → last resort) ─────────────────────────

def _try_shorturlinfo(surl: str, bdstoken: str, h: dict) -> tuple[list | None, str, str, str]:
    """
    Strategy A — shorturlinfo.
    Sometimes returns the full file list directly AND shareid/uk.
    Less strictly enforced than share/list.
    Returns (files, shareid, uk, error).
    """
    for params in [
        {"app_id": "250528", "web": "1", "shorturl": surl, "root": "1", "bdstoken": bdstoken},
        {"app_id": "250528", "web": "1", "shorturl": surl, "root": "1"},
        {"app_id": "250528",             "shorturl": surl, "root": "1"},
    ]:
        try:
            r     = requests.get("https://www.terabox.com/api/shorturlinfo",
                                 params=params, headers=h, timeout=20)
            data  = r.json()
            errno = data.get("errno", -1)
            if errno != 0:
                continue
            shareid = str(data.get("shareid", ""))
            uk      = str(data.get("uk", ""))
            files   = data.get("list", [])
            if files:
                print(f"[Terabox] ✅ Strategy A (shorturlinfo) — {len(files)} file(s)")
                return files, shareid, uk, ""
            # Got shareid/uk but no list — still useful for later
            return None, shareid, uk, "shorturlinfo returned no list"
        except Exception as e:
            continue
    return None, "", "", "shorturlinfo failed all parameter combos"


def _try_share_list(surl: str, bdstoken: str, js_token: str,
                    log_id: str, h: dict) -> tuple[list | None, str]:
    """
    Strategy B — share/list with multiple parameter combos.
    Returns (files, error).
    """
    base = {
        "app_id": "250528", "web": "1", "shorturl": surl, "root": "1",
        "num": "20", "page": "1", "by": "name", "order": "asc",
    }
    combos = [
        # bdstoken + jsToken + logid (full)
        {**base, "bdstoken": bdstoken, "jsToken": js_token, "dp-logid": log_id},
        # bdstoken only (no jsToken)
        {**base, "bdstoken": bdstoken},
        # bdstoken + channel/clienttype (some Terabox versions need these)
        {**base, "bdstoken": bdstoken, "channel": "dubox", "clienttype": "0"},
        # no bdstoken at all — cookie-only
        {**base},
    ]
    for params in combos:
        # Remove empty-string values
        params = {k: v for k, v in params.items() if v and v != "0"}
        try:
            r     = requests.get("https://www.terabox.com/share/list",
                                 params=params, headers=h, timeout=20)
            data  = r.json()
            errno = data.get("errno", -1)
            if errno == 0:
                files = data.get("list", [])
                if files:
                    print(f"[Terabox] ✅ Strategy B (share/list) params={list(params.keys())}")
                    return files, ""
            # Log but keep trying
            print(f"[Terabox] share/list errno={errno} for params={list(params.keys())}")
        except Exception as e:
            print(f"[Terabox] share/list error: {e}")
    return None, "share/list failed all parameter combos"


def _try_page_scrape(surl: str, cookie: str) -> tuple[list | None, str, str, str]:
    """
    Strategy C — scrape file data embedded in the share page HTML.
    Completely bypasses the share/list API.
    Returns (files, shareid, uk, error).
    """
    h = _h(cookie)
    pages = [
        f"https://www.terabox.com/wap/share/filelist?surl={surl}",
        f"https://www.terabox.com/s/{surl}",
        f"https://www.terabox.com/sharing/link?surl={surl}",
    ]
    for page_url in pages:
        try:
            r = requests.get(page_url, headers=h, timeout=25, allow_redirects=True)
            if r.status_code != 200:
                continue
            text = r.text

            # Pattern 1: locals.mset({...list:[...]...})
            m = re.search(r'locals\.mset\((\{.{50,}\})\)', text, re.DOTALL)
            if m:
                try:
                    obj     = json.loads(m.group(1))
                    files   = obj.get("list", [])
                    shareid = str(obj.get("shareid", ""))
                    uk      = str(obj.get("uk", ""))
                    if files:
                        print(f"[Terabox] ✅ Strategy C (locals.mset) via {page_url}")
                        return files, shareid, uk, ""
                except Exception:
                    pass

            # Pattern 2: window.__redux_state__ = {...}
            m = re.search(r'window\.__redux_state__\s*=\s*(\{.+?\})\s*;', text, re.DOTALL)
            if m:
                try:
                    obj   = json.loads(m.group(1))
                    share = obj.get("share", {})
                    files = (share.get("fileinfo", {}).get("list", [])
                             or share.get("list", []))
                    if files:
                        print(f"[Terabox] ✅ Strategy C (__redux_state__) via {page_url}")
                        return files, str(share.get("shareid", "")), str(share.get("uk", "")), ""
                except Exception:
                    pass

            # Pattern 3: bare "list":[{...}] JSON fragment
            m = re.search(r'"list"\s*:\s*(\[\{.+?\}\])', text, re.DOTALL)
            if m:
                try:
                    files = json.loads(m.group(1))
                    if files and isinstance(files, list) and files[0].get("fs_id"):
                        print(f"[Terabox] ✅ Strategy C (list fragment) via {page_url}")
                        return files, "", "", ""
                except Exception:
                    pass

        except Exception as e:
            print(f"[Terabox] Page scrape {page_url} failed: {e}")

    return None, "", "", "page scraping found no file data"


# ── Download link ──────────────────────────────────────────────────────────────

def _get_dlink(surl: str, fs_id: str, shareid: str, uk: str,
               js_token: str, bdstoken: str, log_id: str,
               h: dict) -> tuple[str | None, str]:
    combos = [
        {"app_id": "250528", "web": "1", "shorturl": surl, "fs_id": fs_id,
         "shareId": shareid, "uk": uk, "jsToken": js_token,
         "bdstoken": bdstoken, "dplogid": log_id},
        {"app_id": "250528", "web": "1", "shorturl": surl, "fs_id": fs_id,
         "shareId": shareid, "uk": uk, "bdstoken": bdstoken},
        {"app_id": "250528", "web": "1", "shorturl": surl, "fs_id": fs_id,
         "shareId": shareid, "uk": uk},
    ]
    for params in combos:
        params = {k: v for k, v in params.items() if v and v != "0"}
        try:
            r     = requests.get("https://www.terabox.com/api/download",
                                 params=params, headers=h, timeout=20)
            data  = r.json()
            errno = data.get("errno", -1)
            if errno == 0:
                dlink = data.get("dlink", "")
                if dlink:
                    print(f"[Terabox] ✅ dlink obtained")
                    return dlink, ""
            print(f"[Terabox] download API errno={errno}")
        except Exception as e:
            print(f"[Terabox] download API error: {e}")
    return None, "download API failed all parameter combos"


# ── Main resolver ──────────────────────────────────────────────────────────────

def get_data(url: str, cookie: str) -> tuple[dict | None, str]:
    """
    Returns (data_dict, "")        on success
    Returns (None, "reason string") on failure  ← shown in Telegram
    """
    if not cookie:
        return None, "TERABOX_COOKIE is not set"

    surl = extract_surl(url)
    if not surl:
        return None, f"Could not extract surl from: {url}"

    h        = _h(cookie)
    bdstoken = _get_bdstoken(cookie, surl)
    js_token, log_id = _get_js_token(surl, cookie)

    print(f"\n[Terabox] ── surl={surl} | bds={'yes' if bdstoken else 'no'} | js={'yes' if js_token else 'no'} ──")

    # ── Collect file info: try A → B → C ──────────────────────────────────────
    files = shareid = uk = None

    # Strategy A: shorturlinfo
    files_a, shareid_a, uk_a, err_a = _try_shorturlinfo(surl, bdstoken, h)
    if files_a:
        files, shareid, uk = files_a, shareid_a, uk_a
    else:
        shareid, uk = shareid_a, uk_a   # might have gotten shareid/uk even without list

    # Strategy B: share/list (only if A didn't give us files)
    if not files:
        files_b, err_b = _try_share_list(surl, bdstoken, js_token, log_id, h)
        if files_b:
            files = files_b

    # Strategy C: page scraping (last resort)
    if not files:
        files_c, shareid_c, uk_c, err_c = _try_page_scrape(surl, cookie)
        if files_c:
            files   = files_c
            shareid = shareid or shareid_c
            uk      = uk or uk_c

    if not files:
        return None, (
            f"All strategies failed.\n"
            f"A (shorturlinfo): {err_a}\n"
            f"B (share/list): {err_b}\n"
            f"C (page scrape): {err_c}"
        )

    # Get shareid/uk if still missing
    if not shareid or not uk:
        shareid_x, uk_x = _try_shorturlinfo(surl, bdstoken, h)[1:3]
        shareid = shareid or shareid_x
        uk      = uk      or uk_x

    file      = files[0]
    fs_id     = str(file["fs_id"])
    filename  = file.get("server_filename", "file")
    size      = int(file.get("size", 0))
    thumbs    = file.get("thumbs", {})
    thumb     = thumbs.get("url3") or thumbs.get("url1") or ""
    list_dlink = file.get("dlink", "")

    print(f"[Terabox] File: {filename} | {get_formatted_size(size)}")

    # ── Download link ─────────────────────────────────────────────────────────
    dlink, dl_err = _get_dlink(surl, fs_id, shareid, uk, js_token, bdstoken, log_id, h)
    if not dlink and list_dlink:
        print("[Terabox] Using dlink from file list")
        dlink = list_dlink
    if not dlink:
        return None, f"Could not get download link: {dl_err}"

    # ── Follow CDN redirect ───────────────────────────────────────────────────
    try:
        final      = requests.head(dlink, headers=h, allow_redirects=True, timeout=20)
        direct_url = final.url
    except Exception:
        direct_url = dlink

    print(f"[Terabox] ✅ Done: {filename}")
    return {
        "file_name":   filename,
        "size":        get_formatted_size(size),
        "sizebytes":   size,
        "thumb":       thumb,
        "direct_link": direct_url,
        "link":        dlink,
    }, ""

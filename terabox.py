"""
Self-contained Terabox resolver.
Uses your own Terabox session cookies against Terabox's web API.
A free Terabox account works for publicly shared files.

get_data() now returns (dict, "") on success OR (None, "reason string") on failure.
The reason string is shown directly in the Telegram message so you don't need logs.
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


# ── Internal steps (each returns result + error string) ───────────────────────

def _find_js_token(surl: str, h: dict) -> tuple[str, str]:
    """
    Tries several Terabox page URLs to find jsToken and logid.
    Returns ("token", "logid") — both may be empty strings if nothing found.
    """
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
            for pat in _JS_PATTERNS:
                m = re.search(pat, r.text)
                if m:
                    js_token = m.group(1)
                    logid_m  = re.search(r'dp-logid=([^&\s"\']+)', r.text)
                    log_id   = logid_m.group(1) if logid_m else "0"
                    print(f"[Terabox] jsToken found via {page_url}")
                    return js_token, log_id
        except Exception as e:
            print(f"[Terabox] Page {page_url} failed: {e}")
    print("[Terabox] jsToken not found on any page — will proceed without it")
    return "", "0"


def _get_file_list(surl: str, js_token: str, log_id: str, h: dict) -> tuple[list | None, str]:
    """Returns (files_list, error_message)."""
    params = {
        "app_id": "250528", "web": "1",
        "shorturl": surl,   "root": "1",
        "num": "20",        "page": "1",
        "by": "name",       "order": "asc",
    }
    if js_token:
        params["jsToken"] = js_token
    if log_id and log_id != "0":
        params["dp-logid"] = log_id
    try:
        r     = requests.get("https://www.terabox.com/share/list", params=params, headers=h, timeout=20)
        data  = r.json()
        errno = data.get("errno", -1)
        if errno != 0:
            hints = {
                -6: " (cookie expired/invalid — refresh TERABOX_COOKIE)",
                -9: " (link broken or expired)",
                 2: " (link needs a password — not supported)",
            }
            return None, f"share/list errno={errno}{hints.get(errno, '')}"
        files = data.get("list", [])
        return (files if files else None), ("share/list returned empty list" if not files else "")
    except Exception as e:
        return None, f"share/list request error: {e}"


def _get_shareid_uk(surl: str, h: dict) -> tuple[str, str]:
    try:
        r    = requests.get("https://www.terabox.com/api/shorturlinfo",
                            params={"app_id": "250528", "shorturl": surl, "root": "1"},
                            headers=h, timeout=20)
        info = r.json()
        return str(info.get("shareid", "")), str(info.get("uk", ""))
    except Exception:
        return "", ""


def _get_dlink(surl: str, fs_id: str, shareid: str, uk: str,
               js_token: str, log_id: str, h: dict) -> tuple[str | None, str]:
    """Returns (dlink, error_message)."""
    params = {
        "app_id":  "250528", "web":     "1",
        "shorturl": surl,    "fs_id":   fs_id,
        "shareId": shareid,  "uk":      uk,
    }
    if js_token:
        params["jsToken"]  = js_token
    if log_id and log_id != "0":
        params["dplogid"] = log_id
    try:
        r     = requests.get("https://www.terabox.com/api/download", params=params, headers=h, timeout=20)
        data  = r.json()
        errno = data.get("errno", -1)
        if errno != 0:
            return None, f"download API errno={errno} ({data.get('errmsg', '')})"
        dlink = data.get("dlink", "")
        return (dlink if dlink else None), ("download API returned no dlink" if not dlink else "")
    except Exception as e:
        return None, f"download API request error: {e}"


# ── Main resolver ──────────────────────────────────────────────────────────────

def get_data(url: str, cookie: str) -> tuple[dict | None, str]:
    """
    Resolve a Terabox shared link to a direct download URL.

    Returns:
        (data_dict, "")           on success
        (None,  "reason string")  on failure  ← shown in Telegram message
    """
    if not cookie:
        return None, "TERABOX_COOKIE is not set — check your env variables"

    surl = extract_surl(url)
    if not surl:
        return None, f"Could not extract surl from URL: {url}"

    h = _h(cookie)
    print(f"\n[Terabox] ── Resolving surl={surl} ──────────────")

    # ── Step 1: jsToken (optional but improves success rate) ────────────────
    js_token, log_id = _find_js_token(surl, h)

    # ── Step 2: file list (try WITH jsToken, fallback to WITHOUT) ────────────
    files, err = _get_file_list(surl, js_token, log_id, h)
    if files is None and js_token:
        print(f"[Terabox] share/list with jsToken failed ({err}), retrying without…")
        files, err = _get_file_list(surl, "", "0", h)
    if files is None:
        return None, err

    file     = files[0]
    fs_id    = str(file["fs_id"])
    filename = file.get("server_filename", "file")
    size     = int(file.get("size", 0))
    thumbs   = file.get("thumbs", {})
    thumb    = thumbs.get("url3") or thumbs.get("url1") or ""
    # Sometimes dlink is already in the file list entry — use as backup
    list_dlink = file.get("dlink", "")

    print(f"[Terabox] File: {filename} | {get_formatted_size(size)} | fs_id={fs_id}")

    # ── Step 3: shareid + uk ─────────────────────────────────────────────────
    shareid, uk = _get_shareid_uk(surl, h)
    print(f"[Terabox] shareid={shareid or '(empty)'} | uk={uk or '(empty)'}")

    # ── Step 4: download link (with jsToken, then without, then list fallback) ─
    dlink, dl_err = _get_dlink(surl, fs_id, shareid, uk, js_token, log_id, h)
    if not dlink and js_token:
        print(f"[Terabox] dlink with jsToken failed ({dl_err}), retrying without…")
        dlink, dl_err = _get_dlink(surl, fs_id, shareid, uk, "", "0", h)
    if not dlink and list_dlink:
        print("[Terabox] Using dlink from file list as last-resort fallback")
        dlink = list_dlink
    if not dlink:
        return None, dl_err

    # ── Step 5: follow CDN redirect ──────────────────────────────────────────
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

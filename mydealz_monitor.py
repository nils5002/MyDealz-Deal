#!/usr/bin/env python3
"""
MyDealz Comment Image Monitor
-----------------------------
Monitors a specific MyDealz (pepper.com platform) deal thread for new comments
and sends any newly posted comments (text and images) to a Telegram chat.

Setup:
  1) pip install -r requirements.txt
  2) Create a .env file (see .env.example) with:
     DEAL_URL=...
     TELEGRAM_BOT_TOKEN=...
     TELEGRAM_CHAT_ID=...
     (optional) POLL_SECONDS=60
     (optional) STARTUP_MESSAGE="Monitor gestartet"
     (optional) STARTUP_IMAGE_URL=https://example.com/test.jpg
     (optional) SEEN_LIMIT=5000
  3) python mydealz_monitor.py

Notes:
  - Respects already seen comment IDs across runs (creates state.json).
  - Detects inline <img> and image links in comments.
  - Builds a direct anchor link to the comment: <deal_url>#comment-<id>
  - Uses a desktop User-Agent and simple backoff.
"""
import os
import time
import json
import re
import html
import logging
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from typing import Optional
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

DEAL_URL = os.getenv("DEAL_URL", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))
STATE_PATH = os.getenv("STATE_PATH", "state.json")
STARTUP_MESSAGE = os.getenv("STARTUP_MESSAGE", "").strip()
STARTUP_IMAGE_URL = os.getenv("STARTUP_IMAGE_URL", "").strip()
SEEN_LIMIT = int(os.getenv("SEEN_LIMIT", "5000"))
GRAPHQL_PAGE_LIMIT = int(os.getenv("GRAPHQL_PAGE_LIMIT", "50"))
THREAD_ID = os.getenv("THREAD_ID", "").strip()

if not DEAL_URL or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise SystemExit("Please set DEAL_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID in your .env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
})

IMAGE_EXT_RE = re.compile(r'\.(?:jpg|jpeg|png|gif|webp)\b', re.I)
THREAD_ID_HTML_PATTERNS = [
    re.compile(r'\"threadId\"\s*:\s*\"?(?P<id>\d+)\"?', re.I),
    re.compile(r'data-thread-id=[\"\'](?P<id>\d+)[\"\']', re.I),
]
CANONICAL_LINK_RE = re.compile(
    r'<link[^>]+rel=[\"\']canonical[\"\'][^>]+href=[\"\'](?P<href>[^\"\']+)[\"\']',
    re.I,
)

def extract_thread_id_from_url(url: str) -> str:
    if not url:
        return ""
    path = urlparse(url).path
    digits = re.findall(r'(\d+)', path)
    return digits[-1] if digits else ""

def extract_thread_id_from_html(html_text: str, base_url: str) -> str:
    candidates: list[str] = []
    for pattern in THREAD_ID_HTML_PATTERNS:
        matches = pattern.findall(html_text)
        for match in matches:
            if isinstance(match, tuple):
                match = next((m for m in match if m), "")
            if match:
                candidates.append(str(match))
    unique = list(dict.fromkeys(candidates))
    if len(unique) == 1:
        return unique[0]
    canonical_match = CANONICAL_LINK_RE.search(html_text)
    if canonical_match:
        canonical_url = urljoin(base_url, canonical_match.group('href'))
        return extract_thread_id_from_url(canonical_url)
    return ""

def resolve_thread_id_from_page(url: str) -> str:
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        logging.warning("Failed to fetch %s to resolve thread ID: %s", url, exc)
        return ""
    resolved = extract_thread_id_from_url(response.url)
    if resolved:
        return resolved
    return extract_thread_id_from_html(response.text, url)

BASE_DEAL_URL = DEAL_URL.split("#")[0]
session.headers["Referer"] = BASE_DEAL_URL

if not THREAD_ID:
    THREAD_ID = extract_thread_id_from_url(BASE_DEAL_URL)

if not THREAD_ID:
    THREAD_ID = resolve_thread_id_from_page(BASE_DEAL_URL)
    if THREAD_ID:
        logging.info("Resolved thread ID via page fetch: %s", THREAD_ID)

if not THREAD_ID:
    raise SystemExit("Could not determine thread ID. Set THREAD_ID in .env or use a DEAL_URL ending with -<id>.")

GRAPHQL_ENDPOINT = "https://www.mydealz.de/graphql"
def load_state():
    state = {"seen_comment_ids": []}
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                state.update(loaded)
        except Exception:
            logging.warning("state.json unreadable, starting fresh")
    seen = state.get("seen_comment_ids")
    if not isinstance(seen, list):
        state["seen_comment_ids"] = []
    return state

def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)

def comment_sort_key(comment):
    created_ts = comment.get("created_ts")
    if created_ts is not None:
        try:
            return int(created_ts)
        except (TypeError, ValueError):
            pass
    cid = str(comment.get("id", ""))
    digits = re.findall(r"\d+", cid)
    if digits:
        try:
            return int(digits[-1])
        except ValueError:
            pass
    return cid


def append_seen(state, cid):
    if not cid:
        return
    seen = state.setdefault("seen_comment_ids", [])
    if cid in seen:
        return
    seen.append(cid)
    if SEEN_LIMIT > 0 and len(seen) > SEEN_LIMIT:
        del seen[:-SEEN_LIMIT]


GRAPHQL_COMMENTS_QUERY = """
query Comments($threadId: ID!, $page: Int, $limit: Int) {
  comments(filter: {threadId: {eq: $threadId}}, page: $page, limit: $limit) {
    items {
      commentId
      content
      createdAt
      createdAtTs
      user { username }
    }
  }
}
"""

def ensure_xsrf_token() -> str:
    token = session.cookies.get("xsrf_t")
    if token:
        return token.strip('"')
    resp = session.get(BASE_DEAL_URL, timeout=30)
    resp.raise_for_status()
    token = session.cookies.get("xsrf_t")
    if not token:
        raise RuntimeError("Could not obtain xsrf token from MyDealz")
    return token.strip('"')

def graphql_query(query: str, variables: dict, operation_name: Optional[str] = None) -> dict:
    token = ensure_xsrf_token()
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Referer": BASE_DEAL_URL,
        "X-Requested-With": "XMLHttpRequest",
        "X-XSRF-Token": token,
    }
    payload = {"query": query, "variables": variables}
    if operation_name:
        payload["operationName"] = operation_name
    response = session.post(GRAPHQL_ENDPOINT, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        snippet = response.text[:200]
        raise RuntimeError(f"Invalid GraphQL response: {snippet}") from exc
    errors = data.get("errors") or []
    if errors:
        raise RuntimeError(f"GraphQL error: {errors}")
    return data.get("data") or {}

def parse_comment_content(html_content: str) -> tuple[str, list[str]]:
    if not html_content:
        return "", []
    soup = BeautifulSoup(html_content, "html.parser")
    text = soup.get_text(" ", strip=True)
    images: list[str] = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy") or ""
        if not src and img.get("srcset"):
            first = img["srcset"].split(',')[0].strip().split(' ')[0]
            src = first
        if src and IMAGE_EXT_RE.search(src):
            images.append(urljoin(DEAL_URL, src))
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if IMAGE_EXT_RE.search(href):
            images.append(urljoin(DEAL_URL, href))
    images = list(dict.fromkeys(images))
    return text, images

def normalize_comment_item(item: dict) -> dict:
    comment_id = str(item.get("commentId") or "")
    user = item.get("user") or {}
    author = user.get("username") or ""
    created_ts_raw = item.get("createdAtTs")
    created_ts = None
    if created_ts_raw is not None:
        try:
            created_ts = int(created_ts_raw)
        except (TypeError, ValueError):
            created_ts = None
    timestamp = item.get("createdAt") or ""
    if created_ts is not None:
        try:
            timestamp = datetime.fromtimestamp(created_ts, tz=timezone.utc).astimezone().isoformat()
        except (OverflowError, OSError, ValueError):
            timestamp = item.get("createdAt") or ""
    text, images = parse_comment_content(item.get("content") or "")
    return {
        "id": comment_id,
        "author": author,
        "text": text,
        "timestamp": timestamp,
        "images": images,
        "created_ts": created_ts,
    }

def fetch_recent_comments(page: int = 1, limit: Optional[int] = None) -> list[dict]:
    limit = limit or GRAPHQL_PAGE_LIMIT
    variables = {"threadId": THREAD_ID, "page": page, "limit": limit}
    data = graphql_query(GRAPHQL_COMMENTS_QUERY, variables, operation_name="Comments")
    items = ((data.get("comments") or {}).get("items")) or []
    normalized = {}
    for raw in items:
        comment = normalize_comment_item(raw)
        if comment.get("id"):
            normalized[comment["id"]] = comment
    return sorted(normalized.values(), key=comment_sort_key)


def trim_text(text, limit):
    if len(text) <= limit:
        return text
    ellipsis = "..."
    if limit <= len(ellipsis):
        return text[:limit]
    return text[: limit - len(ellipsis)].rstrip() + ellipsis


def to_plain_text(value):
    if not value:
        return ""
    if isinstance(value, dict):
        for key in ("text", "body", "content", "html", "value"):
            if value.get(key):
                value = value[key]
                break
        else:
            value = str(value)
    return BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)


def extract_comments_from_preloaded_state(html_text):
    comments = []
    pattern = re.compile(r"window.__PRELOADED_STATE__\s*=\s*({.*?})\s*;", re.DOTALL)
    for match in pattern.finditer(html_text):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        entities = data.get("entities") or {}
        raw_comments = entities.get("comments") or entities.get("comment") or {}
        if isinstance(raw_comments, list):
            iterable = raw_comments
        elif isinstance(raw_comments, dict):
            iterable = raw_comments.values()
        else:
            continue
        for raw in iterable:
            if not isinstance(raw, dict):
                continue
            cid = raw.get("id") or raw.get("commentId") or raw.get("commentID")
            if not cid:
                continue
            cid = str(cid)
            author = (
                raw.get("authorName")
                or raw.get("userName")
                or raw.get("username")
                or raw.get("name")
            )
            if not author and isinstance(raw.get("user"), dict):
                user = raw["user"]
                author = user.get("name") or user.get("username") or user.get("displayName")
            text = to_plain_text(raw.get("content") or raw.get("body") or raw.get("text"))
            ts = raw.get("createdAt") or raw.get("timestamp") or raw.get("dateCreated") or ""
            images = []
            media_sources = []
            for key in ("media", "sharedMedia", "attachments", "images"):
                val = raw.get(key)
                if not val:
                    continue
                if isinstance(val, dict):
                    media_sources.extend(val.values())
                elif isinstance(val, list):
                    media_sources.extend(val)
                else:
                    media_sources.append(val)
            for item in media_sources:
                url = ""
                if isinstance(item, dict):
                    url = item.get("url") or item.get("src") or item.get("image") or item.get("imageUrl")
                    if not url and item.get("path"):
                        url = item["path"]
                elif isinstance(item, str):
                    url = item
                if not url:
                    continue
                if not IMAGE_EXT_RE.search(url):
                    continue
                images.append(urljoin(DEAL_URL, url))
            comments.append(
                {
                    "id": cid,
                    "author": author or "",
                    "text": text,
                    "timestamp": ts,
                    "images": list(dict.fromkeys(images)),
                }
            )
    return comments


def extract_comments(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    comments = extract_comments_from_dom(soup)
    comment_map = {c["id"]: c for c in comments if c.get("id")}
    fallback_comments = extract_comments_from_preloaded_state(html_text)
    for fb in fallback_comments:
        cid = fb["id"]
        existing = comment_map.get(cid)
        if existing:
            if not existing.get("author") and fb.get("author"):
                existing["author"] = fb["author"]
            if not existing.get("text") and fb.get("text"):
                existing["text"] = fb["text"]
            if not existing.get("timestamp") and fb.get("timestamp"):
                existing["timestamp"] = fb["timestamp"]
            fb_images = fb.get("images") or []
            if fb_images:
                merged = (existing.get("images") or []) + fb_images
                existing["images"] = list(dict.fromkeys(merged))
        else:
            comments.append(fb)
            comment_map[cid] = fb
    comments.sort(key=comment_sort_key)
    return comments


def build_comment_message(comment, title="Neuer Kommentar"):
    anchor = build_comment_link(comment["id"])
    lines = [
        f"<b>{html.escape(title)}</b>",
        f"Autor: {html.escape(comment.get('author') or 'Unbekannt')}",
        f"Zeit: {html.escape(comment.get('timestamp') or 'Unbekannt')}",
        f"<a href=\"{html.escape(anchor)}\">Zum Kommentar</a>",
    ]
    text = comment.get("text", "").strip()
    lines.append("")
    if text:
        lines.append("<b>Kommentar:</b>")
        lines.append(html.escape(text))
    else:
        lines.append("<i>Kein Text im Kommentar</i>")
    return "\n".join(lines)


def build_comment_image_caption(comment, idx, total, title):
    lines = [
        f"<b>{html.escape(title)}</b>",
        f"Autor: {html.escape(comment.get('author') or 'Unbekannt')}",
        f"Zeit: {html.escape(comment.get('timestamp') or 'Unbekannt')}",
        f"<a href=\"{html.escape(build_comment_link(comment['id']))}\">Zum Kommentar</a>",
    ]
    text = comment.get("text", "").strip()
    lines.append("")
    if text:
        snippet = trim_text(html.escape(text), 900)
        lines.append("<b>Kommentar:</b>")
        lines.append(snippet)
    else:
        lines.append("<i>Kein Text im Kommentar</i>")
    if total > 1:
        lines.append(f"Bild {idx}/{total}")
    caption = "\n".join(lines)
    if len(caption) > 1024:
        caption = trim_text(caption, 1024)
    return caption


def send_comment_notification(comment, title="Neuer Kommentar"):
    message = build_comment_message(comment, title=title)
    message_ok = send_telegram_message(message)
    images = comment.get("images") or []
    images_sent = 0
    if images:
        image_title = f"{title} - Bild"
        total = len(images)
        for idx, img_url in enumerate(images, 1):
            caption = build_comment_image_caption(comment, idx, total, image_title)
            if send_telegram_photo(img_url, caption):
                images_sent += 1
            time.sleep(0.7)
    return message_ok, images_sent

def send_telegram_photo(photo_url, caption):
    tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "photo": photo_url,
        "caption": caption[:1024],  # Telegram limit for caption in sendPhoto
        "parse_mode": "HTML"
    }
    r = session.post(tg_url, data=data, timeout=30)
    if not r.ok:
        logging.error("Telegram sendPhoto failed: %s | %s", r.status_code, r.text[:300])
    return r.ok

def send_telegram_message(text):
    tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text[:4096],
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    r = session.post(tg_url, data=data, timeout=30)
    if not r.ok:
        logging.error("Telegram sendMessage failed: %s | %s", r.status_code, r.text[:300])
    return r.ok

def extract_comments_from_dom(soup):
    """
    Return list of dicts: {id, author, text, images[], timestamp}
    Pepper-based sites usually render comments in <article data-comment-id="..."> or similar.
    We'll try multiple selectors for robustness.
    """
    comments = []
    # Try likely containers
    candidates = []
    candidates.extend(soup.select("article[data-comment-id]"))
    if not candidates:
        candidates.extend(soup.select("[data-comment-id]"))
    if not candidates:
        # Fallback: generic comment blocks
        candidates.extend(soup.select("div.comment, li.comment"))

    for el in candidates:
        cid = el.get("data-comment-id") or el.get("id")
        if not cid:
            # Some have id like 'comment-12345678'
            elem_id = el.get("id", "")
            if elem_id.startswith("comment-"):
                cid = elem_id.split("comment-")[-1]
        if not cid:
            continue  # skip if we can't identify uniquely

        # Author
        author = ""
        a1 = el.select_one(".user", ".user-name")
        if a1 and a1.get_text(strip=True):
            author = a1.get_text(strip=True)
        else:
            # Another common pepper selector:
            a2 = el.select_one("[data-user-name]")
            if a2:
                author = a2.get("data-user-name", "") or a2.get_text(strip=True)

        # Text content
        body = el.select_one(".comment__body") or el
        text = ""
        tb = body.select_one(".content, .text, .comment-body, .comment-content")
        if tb:
            text = tb.get_text(" ", strip=True)
        else:
            text = body.get_text(" ", strip=True)

        # Timestamp (best-effort)
        ts = ""
        tsel = el.select_one("time[datetime]") or el.select_one("time")
        if tsel:
            ts = tsel.get("datetime") or tsel.get_text(strip=True)

        # Images
        images = []
        # <img> tags (src or data-src / data-lazy / srcset first url)
        for img in el.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-lazy") or ""
            if not src and img.get("srcset"):
                # take first URL from srcset
                srcset = img.get("srcset", "")
                first = srcset.split(",")[0].strip().split(" ")[0]
                src = first
            if src and IMAGE_EXT_RE.search(src):
                images.append(urljoin(DEAL_URL, src))

        # Links to images
        for a in el.find_all("a", href=True):
            href = a["href"]
            if IMAGE_EXT_RE.search(href):
                images.append(urljoin(DEAL_URL, href))

        images = list(dict.fromkeys(images))  # dedupe, preserve order
        comments.append({
            "id": str(cid),
            "author": author,
            "text": text,
            "timestamp": ts,
            "images": images
        })
    return comments

def fetch_comments_html(url):
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return r.text

def build_comment_link(cid):
    # pepper anchors typically support #comment-<id>
    return f"{DEAL_URL}#comment-{cid}"

def send_startup_notification(state):
    message = STARTUP_MESSAGE or f"Monitoring gestartet: {DEAL_URL}"
    if send_telegram_message(message):
        logging.info("Startup message sent.")
    else:
        logging.warning("Startup message failed to send.")
    if STARTUP_IMAGE_URL:
        ok = send_telegram_photo(STARTUP_IMAGE_URL, message)
        if ok:
            logging.info("Startup image sent.")
        else:
            logging.warning("Startup image failed to send.")

    try:
        comments = fetch_recent_comments(limit=GRAPHQL_PAGE_LIMIT)
    except Exception as exc:
        logging.warning("Could not fetch latest comment for startup: %s", exc)
        return []

    if not comments:
        logging.info("No comments available to snapshot on startup.")
        return []

    latest = comments[-1]
    send_comment_notification(latest, title="Letzter Kommentar beim Start")

    for comment in comments:
        append_seen(state, comment["id"])
    save_state(state)
    return comments

def run_once(state, preloaded_comments=None):
    if preloaded_comments is not None:
        comments = preloaded_comments
    else:
        comments = fetch_recent_comments(limit=GRAPHQL_PAGE_LIMIT)

    if not comments:
        logging.info("No comments found (yet).")
        return

    seen = state.setdefault("seen_comment_ids", [])
    seen_set = set(seen)
    new_comments = [c for c in comments if c["id"] not in seen_set]

    if not new_comments:
        logging.info("No new comments.")
        return

    new_comments.sort(key=comment_sort_key)

    messages_sent = 0
    images_sent = 0
    for comment in new_comments:
        message_ok, comment_images_sent = send_comment_notification(comment)
        if message_ok:
            messages_sent += 1
        images_sent += comment_images_sent
        append_seen(state, comment["id"])

    save_state(state)
    logging.info(
        "Processed %d new comments (messages sent: %d, images sent: %d).",
        len(new_comments),
        messages_sent,
        images_sent,
    )

def main():
    logging.info("Monitoring: %s", DEAL_URL)
    state = load_state()
    preloaded = send_startup_notification(state)
    # After startup snapshot we only act on truly new comments.
    while True:
        try:
            run_once(state, preloaded_comments=preloaded)
            preloaded = None
            time.sleep(POLL_SECONDS)
        except requests.RequestException as e:
            logging.error("Network error: %s", e)
            time.sleep(min(180, POLL_SECONDS * 2))
        except Exception as e:
            logging.exception("Unexpected error: %s", e)
            time.sleep(min(180, POLL_SECONDS * 2))

if __name__ == "__main__":
    main()


"""
Import Telegram HTML export (messages.html) into SQLite channel_posts.

This is for "local mode" when the bot can't read the channel history via Bot API.
"""

from __future__ import annotations

import html
import os
import re
from datetime import datetime

from config import config
from database import save_post
from entity_extractor import extract_entities


_MSG_BLOCK_RE = re.compile(
    r'<div class="message[^"]*"[^>]*\bid="message(?P<id>\d+)"[^>]*>(?P<body>.*?)</div>\s*</div>\s*</div>',
    re.IGNORECASE | re.DOTALL,
)
_DATE_TITLE_RE = re.compile(r'<div class="date"[^>]*title="([^"]+)"', re.IGNORECASE)
_TEXT_RE = re.compile(r'<div class="text"[^>]*>(.*?)</div>', re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_html_text(raw: str) -> str:
    if not raw:
        return ""
    raw = raw.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = _TAG_RE.sub("", raw)
    text = html.unescape(text)
    # normalize whitespace
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_datetime(value: str) -> str:
    """
    Telegram export usually has: "2024-01-31 12:34:56" (local) in title.
    We'll store ISO string if we can parse, otherwise keep as-is.
    """
    v = (value or "").strip()
    if not v:
        return datetime.now().isoformat()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt).isoformat()
        except ValueError:
            continue
    return v


def import_messages_html(path: str | None = None, max_messages: int | None = None) -> int:
    """
    Imports messages from Telegram HTML export into DB.
    Returns number of *attempted* saves (duplicates are ignored by DB layer).
    """
    html_path = (path or config.MESSAGES_HTML_PATH or "").strip()
    if not html_path:
        return 0
    if not os.path.exists(html_path):
        return 0

    data = open(html_path, "r", encoding="utf-8", errors="ignore").read()

    saved = 0
    for match in _MSG_BLOCK_RE.finditer(data):
        msg_id = int(match.group("id"))
        body = match.group("body") or ""

        date_match = _DATE_TITLE_RE.search(body)
        date_str = _parse_datetime(date_match.group(1)) if date_match else datetime.now().isoformat()

        text_match = _TEXT_RE.search(body)
        text = _clean_html_text(text_match.group(1) if text_match else "")
        if not text:
            continue

        entities = extract_entities(text)
        entities_data = [{"type": e.type, "value": e.value} for e in entities]
        save_post(message_id=msg_id, text=text, date=date_str, entities=entities_data)
        saved += 1

        if max_messages is not None and saved >= max_messages:
            break

    return saved


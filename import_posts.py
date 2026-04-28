#!/usr/bin/env python3
"""
import_posts.py — Импорт истории канала из Telegram HTML-экспорта.

Использование:
    python import_posts.py messages.html

Скрипт парсит HTML-файл, экспортированный из Telegram Desktop,
и загружает все текстовые посты в базу знаний бота.
"""

import sys
import re
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("Устанавливаю BeautifulSoup...")
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "beautifulsoup4", "-q"])
    from bs4 import BeautifulSoup

# ─── Parse HTML ────────────────────────────────────────────────────────────────

def parse_messages_html(html_path: str) -> list[dict]:
    """Parse Telegram exported HTML and return list of post dicts."""
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")

    # Find all real message divs (id="messageN" not "message-N")
    message_divs = soup.find_all(
        "div",
        id=lambda x: x and re.match(r"^message\d+$", x or "")
    )

    posts = []
    for div in message_divs:
        msg_id_raw = div.get("id", "").replace("message", "")
        try:
            msg_id = int(msg_id_raw)
        except ValueError:
            continue

        text_div = div.find("div", class_="text")
        date_div = div.find("div", class_="date")

        if not text_div:
            continue

        # Get clean text (removes sticker links, keeps emoji text)
        text = text_div.get_text(" ", strip=True)
        # Remove sticker artifacts like "➿" repeated blocks
        text = re.sub(r"(➿\s*){3,}", "➿ ", text)
        text = text.strip()

        if not text or len(text) < 15:
            continue

        # Parse date
        date_raw = date_div.get("title", "") if date_div else ""
        if date_raw:
            # Format: "15.06.2025 23:39:41 UTC+02:00"
            try:
                date_clean = datetime.strptime(date_raw[:16], "%d.%m.%Y %H:%M").isoformat()
            except ValueError:
                date_clean = date_raw
        else:
            date_clean = datetime.now().isoformat()

        posts.append({
            "id": msg_id,
            "date": date_clean,
            "text": text,
        })

    return posts


# ─── Simple entity extractor (inline, no dependency on bot modules) ───────────

def quick_extract_entities(text: str) -> list[dict]:
    """Quick entity extraction for indexing purposes."""
    entities = []
    patterns = {
        "Email": r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
        "Website": r"https?://[^\s\"'<>]+",
        "Telegram": r"(?:t\.me/|@)([a-zA-Z][a-zA-Z0-9_]{3,31})",
        "Domain": r"\b(?:[a-zA-Z0-9\-]+\.)+(?:com|net|org|ru|io|co|me)\b",
    }
    for etype, pattern in patterns.items():
        for m in re.finditer(pattern, text):
            entities.append({"type": etype, "value": m.group(0)})
    return entities[:10]  # cap per post


# ─── Database import ───────────────────────────────────────────────────────────

def import_to_db(posts: list[dict], db_path: str = "overnetting.db") -> int:
    """Import posts into channel_posts table. Returns count of newly added."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS channel_posts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id  INTEGER UNIQUE NOT NULL,
            text        TEXT,
            date        TEXT NOT NULL,
            indexed_at  TEXT NOT NULL DEFAULT (datetime('now')),
            post_hash   TEXT UNIQUE NOT NULL,
            entities_json TEXT DEFAULT '[]'
        )
    """)
    conn.commit()

    added = 0
    skipped = 0

    for post in posts:
        post_hash = hashlib.md5(f"{post['id']}:{post['text']}".encode()).hexdigest()
        entities = quick_extract_entities(post["text"])

        try:
            conn.execute(
                """INSERT OR IGNORE INTO channel_posts
                   (message_id, text, date, post_hash, entities_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (post["id"], post["text"], post["date"],
                 post_hash, json.dumps(entities, ensure_ascii=False))
            )
            if conn.total_changes > 0:
                added += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"  ⚠️  Ошибка при добавлении поста {post['id']}: {e}")

    conn.commit()
    conn.close()
    return added


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        html_path = "messages.html"
    else:
        html_path = sys.argv[1]

    if not Path(html_path).exists():
        print(f"❌ Файл не найден: {html_path}")
        print("Использование: python import_posts.py messages.html")
        sys.exit(1)

    print(f"📂 Читаю файл: {html_path}")
    posts = parse_messages_html(html_path)
    print(f"✅ Найдено постов с текстом: {len(posts)}")

    if not posts:
        print("⚠️  Постов не найдено. Проверь формат файла.")
        return

    # Show preview
    print("\n📋 Превью первых 5 постов:")
    for p in posts[:5]:
        print(f"  [{p['id']}] {p['date'][:10]} — {p['text'][:90]}...")

    # Find DB
    db_candidates = ["overnetting.db", "bot/overnetting.db"]
    db_path = "overnetting.db"
    for candidate in db_candidates:
        if Path(candidate).exists():
            db_path = candidate
            break

    print(f"\n💾 Импортирую в базу: {db_path}")
    added = import_to_db(posts, db_path)
    total = len(posts)
    skipped = total - added

    print(f"\n🎉 Готово!")
    print(f"   Добавлено: {added} постов")
    print(f"   Пропущено (дубликаты): {skipped} постов")
    print(f"   Всего в файле: {total} постов")
    print(f"\n🤖 ShkoloDrive AI теперь использует эти посты как базу знаний.")


if __name__ == "__main__":
    main()

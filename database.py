import sqlite3
import json
import hashlib
import re
from difflib import SequenceMatcher
from datetime import datetime
from typing import Optional
from config import config


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
        -- Пользовательские расследования
        CREATE TABLE IF NOT EXISTS investigations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            title       TEXT    NOT NULL DEFAULT 'Untitled',
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            graph_path  TEXT,
            summary     TEXT
        );

        -- Найденные сущности
        CREATE TABLE IF NOT EXISTS entities (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            investigation_id INTEGER NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            entity_type     TEXT NOT NULL,
            value           TEXT NOT NULL,
            confidence      REAL NOT NULL DEFAULT 1.0,
            source          TEXT,
            added_at        TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(investigation_id, entity_type, value)
        );

        -- Связи между сущностями
        CREATE TABLE IF NOT EXISTS links (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            investigation_id INTEGER NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            entity_a_id     INTEGER NOT NULL REFERENCES entities(id),
            entity_b_id     INTEGER NOT NULL REFERENCES entities(id),
            link_type       TEXT NOT NULL,
            confidence      REAL NOT NULL DEFAULT 0.5,
            explanation     TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- Посты канала ShkoloDrive (база знаний)
        CREATE TABLE IF NOT EXISTS channel_posts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id  INTEGER UNIQUE NOT NULL,
            text        TEXT,
            date        TEXT NOT NULL,
            indexed_at  TEXT NOT NULL DEFAULT (datetime('now')),
            post_hash   TEXT UNIQUE NOT NULL,
            entities_json TEXT DEFAULT '[]'
        );

        -- Подписки: проверено ли членство
        CREATE TABLE IF NOT EXISTS subscription_cache (
            user_id     INTEGER PRIMARY KEY,
            is_member   INTEGER NOT NULL DEFAULT 0,
            checked_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS ai_user_settings (
            user_id     INTEGER PRIMARY KEY,
            provider    TEXT NOT NULL DEFAULT 'auto',
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS ai_daily_usage (
            user_id         INTEGER NOT NULL,
            usage_date      TEXT NOT NULL,
            cloud_requests  INTEGER NOT NULL DEFAULT 0,
            local_requests  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, usage_date)
        );

        UPDATE links
        SET entity_a_id = MIN(entity_a_id, entity_b_id),
            entity_b_id = MAX(entity_a_id, entity_b_id)
        WHERE entity_a_id > entity_b_id;

        DELETE FROM links
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM links
            GROUP BY investigation_id, entity_a_id, entity_b_id, link_type
        );

        CREATE INDEX IF NOT EXISTS idx_entities_inv  ON entities(investigation_id);
        CREATE INDEX IF NOT EXISTS idx_links_inv     ON links(investigation_id);
        CREATE INDEX IF NOT EXISTS idx_posts_date    ON channel_posts(date);
        CREATE INDEX IF NOT EXISTS idx_investigations_user ON investigations(user_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_links_unique
        ON links(investigation_id, entity_a_id, entity_b_id, link_type);
        """)


# ─── Investigations ────────────────────────────────────────────────────────────

def create_investigation(user_id: int, title: str = "Новое расследование") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO investigations (user_id, title) VALUES (?, ?)",
            (user_id, title)
        )
        return cur.lastrowid


def get_investigation(inv_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM investigations WHERE id=?", (inv_id,)).fetchone()


def list_investigations(user_id: int, limit: int = 10) -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM investigations WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()


def list_all_investigations() -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM investigations ORDER BY updated_at DESC"
        ).fetchall()


def update_investigation_graph(inv_id: int, graph_path: str, summary: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE investigations SET graph_path=?, summary=?, updated_at=? WHERE id=?",
            (graph_path, summary, datetime.now().isoformat(), inv_id)
        )


def delete_investigation(inv_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM investigations WHERE id=?", (inv_id,))


# ─── Entities ─────────────────────────────────────────────────────────────────

def upsert_entity(investigation_id: int, entity_type: str, value: str,
                  confidence: float = 1.0, source: str = None) -> int:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO entities (investigation_id, entity_type, value, confidence, source)
               VALUES (?,?,?,?,?)
               ON CONFLICT(investigation_id, entity_type, value) DO UPDATE
               SET confidence=MAX(excluded.confidence, confidence)""",
            (investigation_id, entity_type, value, confidence, source)
        )
        row = conn.execute(
            "SELECT id FROM entities WHERE investigation_id=? AND entity_type=? AND value=?",
            (investigation_id, entity_type, value)
        ).fetchone()
        return row["id"]


def get_entities(investigation_id: int) -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM entities WHERE investigation_id=?", (investigation_id,)
        ).fetchall()


# ─── Links ────────────────────────────────────────────────────────────────────

def add_link(investigation_id: int, a_id: int, b_id: int,
             link_type: str, confidence: float, explanation: str):
    left_id, right_id = sorted((a_id, b_id))
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO links
               (investigation_id, entity_a_id, entity_b_id, link_type, confidence, explanation)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(investigation_id, entity_a_id, entity_b_id, link_type) DO UPDATE
               SET confidence=MAX(excluded.confidence, confidence),
                   explanation=excluded.explanation,
                   created_at=datetime('now')""",
            (investigation_id, left_id, right_id, link_type, confidence, explanation)
        )
        conn.execute(
            """UPDATE investigations SET updated_at=? WHERE id=?""",
            (datetime.now().isoformat(), investigation_id)
        )


def clear_links(investigation_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM links WHERE investigation_id=?", (investigation_id,))
        conn.execute(
            """UPDATE investigations SET updated_at=? WHERE id=?""",
            (datetime.now().isoformat(), investigation_id)
        )


def get_links(investigation_id: int) -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM links WHERE investigation_id=?", (investigation_id,)
        ).fetchall()


# ─── Knowledge base ───────────────────────────────────────────────────────────

def save_post(message_id: int, text: str, date: str, entities: list = None):
    post_hash = hashlib.md5(f"{message_id}:{text}".encode()).hexdigest()
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO channel_posts (message_id, text, date, post_hash, entities_json)
               VALUES (?,?,?,?,?)""",
            (message_id, text, date, post_hash, json.dumps(entities or []))
        )


def get_all_posts() -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM channel_posts ORDER BY date DESC"
        ).fetchall()


def get_recent_posts(limit: int = 20) -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM channel_posts ORDER BY date DESC LIMIT ?", (limit,)
        ).fetchall()


def count_posts() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM channel_posts").fetchone()[0]


def _normalize_text(value: str) -> str:
    text = (value or "").lower()
    text = text.replace("ё", "е")
    text = re.sub(r"[\u200b\u200c\u200d\uFEFF]", "", text)  # zero-width
    text = re.sub(r"[^0-9a-zа-я_@.+\s-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokenize(text: str) -> list[str]:
    parts = [p for p in _normalize_text(text).split(" ") if p]
    return [p for p in parts if len(p) >= 2]


def _name_like_query_tokens(query_tokens: list[str]) -> bool:
    # Very lightweight heuristic: 2-3 alphabetic tokens => likely "Имя Фамилия"
    if not (2 <= len(query_tokens) <= 3):
        return False
    return all(re.fullmatch(r"[a-zа-я-]{2,}", t) for t in query_tokens)


def _score_post_match(query: str, query_tokens: list[str], post_text: str) -> float:
    if not post_text:
        return 0.0

    text_norm = _normalize_text(post_text)
    if not text_norm:
        return 0.0

    score = 0.0

    # Token coverage
    token_hits = sum(1 for t in query_tokens if t and t in text_norm)
    if query_tokens:
        score += 2.5 * (token_hits / len(query_tokens))

    # Name query boost: require both name parts to appear anywhere
    if _name_like_query_tokens(query_tokens):
        if token_hits >= min(2, len(query_tokens)):
            score += 1.5

    # Fuzzy similarity on normalized strings (bounded, cheap)
    q_norm = _normalize_text(query)
    if q_norm:
        score += 1.2 * SequenceMatcher(a=q_norm, b=text_norm[:2000]).ratio()

    return score


def search_channel_posts(query: str, limit: int = 5, scan_limit: int = 800) -> list[dict]:
    """
    Fuzzy search in channel_posts for local mode and hints.
    Returns list of dicts: {message_id, date, text, score}.
    """
    q = (query or "").strip()
    if not q:
        return []

    q_tokens = _tokenize(q)
    # Prefilter by LIKE if we have at least one meaningful token
    like_token = next((t for t in q_tokens if len(t) >= 4 and not t.startswith("@")), None)

    with get_conn() as conn:
        if like_token:
            rows = conn.execute(
                "SELECT message_id, text, date FROM channel_posts WHERE text LIKE ? ORDER BY date DESC LIMIT ?",
                (f"%{like_token}%", scan_limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT message_id, text, date FROM channel_posts ORDER BY date DESC LIMIT ?",
                (scan_limit,),
            ).fetchall()

    scored: list[dict] = []
    for row in rows:
        text = row["text"] or ""
        score = _score_post_match(q, q_tokens, text)
        if score <= 0:
            continue
        scored.append(
            {
                "message_id": int(row["message_id"]),
                "date": row["date"],
                "text": text,
                "score": float(score),
            }
        )

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[: max(1, int(limit))]


# ─── Subscription cache ───────────────────────────────────────────────────────

def cache_subscription(user_id: int, is_member: bool):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO subscription_cache (user_id, is_member, checked_at) VALUES (?,?, datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET is_member=excluded.is_member, checked_at=datetime('now')""",
            (user_id, 1 if is_member else 0)
        )


def get_cached_subscription(user_id: int, max_age_seconds: int = 300) -> Optional[bool]:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT is_member, checked_at FROM subscription_cache WHERE user_id=?
               AND (julianday('now') - julianday(checked_at)) * 86400 < ?""",
            (user_id, max_age_seconds)
        ).fetchone()
        return bool(row["is_member"]) if row else None


def get_ai_provider(user_id: int, default: str = "auto") -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT provider FROM ai_user_settings WHERE user_id=?",
            (user_id,)
        ).fetchone()
        return row["provider"] if row and row["provider"] else default


def set_ai_provider(user_id: int, provider: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO ai_user_settings (user_id, provider, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(user_id) DO UPDATE
               SET provider=excluded.provider, updated_at=datetime('now')""",
            (user_id, provider)
        )


def _today_usage_date() -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT date('now') AS d").fetchone()
    return row["d"] if row else datetime.now().strftime("%Y-%m-%d")


def get_ai_daily_usage(user_id: int) -> dict:
    usage_date = _today_usage_date()
    with get_conn() as conn:
        row = conn.execute(
            """SELECT cloud_requests, local_requests FROM ai_daily_usage
               WHERE user_id=? AND usage_date=?""",
            (user_id, usage_date),
        ).fetchone()
    if not row:
        return {"cloud_requests": 0, "local_requests": 0, "usage_date": usage_date}
    return {
        "cloud_requests": int(row["cloud_requests"]),
        "local_requests": int(row["local_requests"]),
        "usage_date": usage_date,
    }


def increment_ai_daily_usage(user_id: int, *, cloud: bool) -> dict:
    usage_date = _today_usage_date()
    column = "cloud_requests" if cloud else "local_requests"
    with get_conn() as conn:
        conn.execute(
            f"""INSERT INTO ai_daily_usage (user_id, usage_date, {column})
                VALUES (?, ?, 1)
                ON CONFLICT(user_id, usage_date) DO UPDATE
                SET {column} = {column} + 1""",
            (user_id, usage_date),
        )
    return get_ai_daily_usage(user_id)

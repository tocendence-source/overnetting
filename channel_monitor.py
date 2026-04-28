"""
Channel Monitor — автоматически индексирует посты канала ShkoloDrive.

При запуске:
  1. Загружает всю историю канала через Telegram Bot API (getUpdates / forwardFrom).
  2. Сохраняет новые посты в БД.
  3. Слушает новые посты через webhook/polling.
"""

import logging
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

from config import config
from database import save_post, count_posts
from entity_extractor import extract_entities
from channel_html_importer import import_messages_html

logger = logging.getLogger(__name__)


async def index_channel_history(bot: Bot, limit: int = 200):
    """
    Try to load recent posts from the ShkoloDrive channel.
    Note: bots can only read public channels or channels where they are members.
    """
    channel = config.CHANNEL_ID
    logger.info(f"[monitor] Starting channel indexing: {channel}")

    try:
        # Get channel info
        chat = await bot.get_chat(channel)
        logger.info(f"[monitor] Channel: {chat.title}")
    except Exception as e:
        logger.warning(f"[monitor] Cannot access channel {channel}: {e}")
        return 0

    # We use forwardFrom approach — send messages to ourselves or use
    # channel message IDs. Since bots can't read history directly,
    # we rely on the admin manually forwarding or on new incoming posts.
    # Here we set up the infrastructure and log the status.

    # Local import (Telegram HTML export) — helps when bot can't read history.
    imported = 0
    try:
        if config.MESSAGES_HTML_PATH:
            imported = import_messages_html(config.MESSAGES_HTML_PATH)
            if imported:
                logger.info(f"[monitor] Imported from messages.html: {imported}")
    except Exception as e:
        logger.warning(f"[monitor] messages.html import failed: {e}")

    indexed = count_posts()
    logger.info(f"[monitor] Currently indexed posts: {indexed} (+{imported} imported)")
    return indexed


async def process_channel_post(message) -> bool:
    """
    Process a new post from the ShkoloDrive channel.
    Called from the channel post handler in main bot.
    """
    if not message.text and not message.caption:
        return False

    text = message.text or message.caption or ""
    date_str = message.date.isoformat() if message.date else datetime.now().isoformat()

    # Extract entities from the post
    entities = extract_entities(text)
    entities_data = [{"type": e.type, "value": e.value} for e in entities]

    save_post(
        message_id=message.message_id,
        text=text,
        date=date_str,
        entities=entities_data,
    )

    logger.info(f"[monitor] Indexed post {message.message_id}: {len(entities)} entities")
    return True


async def check_subscription(bot: Bot, user_id: int, force: bool = False) -> bool:
    """
    Check if user is subscribed to the ShkoloDrive channel.
    Uses Telegram getChatMember API.
    """
    from database import get_cached_subscription, cache_subscription

    # Check cache first (5 minute TTL), ONLY if not forced
    if not force:
        cached = get_cached_subscription(user_id, max_age_seconds=300)
        if cached is not None:
            return cached

    try:
        member = await bot.get_chat_member(chat_id=config.CHANNEL_ID, user_id=user_id)
        is_member = member.status in ("member", "administrator", "creator")
        cache_subscription(user_id, is_member)
        return is_member
    except (TelegramForbiddenError, TelegramBadRequest):
        # Bot not in channel or channel doesn't exist
        return False
    except Exception as e:
        logger.warning(f"[monitor] Subscription check failed for {user_id}: {e}")
        return False
"""
Keyboards and message templates for OverNetting Bot.
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

BTN_NEW_INVESTIGATION = "🔍 Новое расследование"
BTN_MY_INVESTIGATIONS = "📂 Мои расследования"
BTN_AI = "🤖 ShkoloDrive AI"
BTN_ABOUT = "ℹ️ О боте"

AI_PROVIDER_LABELS = {
    "auto": "Auto",
    "gemini": "Gemini",
    "gemma": "Gemma",
    "local": "Local",
}


# ─── Main menu ────────────────────────────────────────────────────────────────

def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_NEW_INVESTIGATION)],
            [KeyboardButton(text=BTN_MY_INVESTIGATIONS), KeyboardButton(text=BTN_AI)],
            [KeyboardButton(text=BTN_ABOUT)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Введи данные для анализа...",
    )


# ─── Investigation actions ────────────────────────────────────────────────────

def investigation_kb(inv_id: int, has_graph: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="📊 Граф", callback_data=f"graph:{inv_id}"),
            InlineKeyboardButton(text="🤖 AI-подсказка", callback_data=f"ai_hint:{inv_id}"),
        ],
        [
            InlineKeyboardButton(text="💾 Сохранить", callback_data=f"save:{inv_id}"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh:{inv_id}"),
        ],
        [
            InlineKeyboardButton(text="📤 Экспорт PNG", callback_data=f"export_png:{inv_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete:{inv_id}"),
        ],
    ]
    if has_graph:
        buttons.insert(0, [
            InlineKeyboardButton(text="🔗 Только сильные связи", callback_data=f"strong_only:{inv_id}"),
            InlineKeyboardButton(text="🌐 Все связи", callback_data=f"all_links:{inv_id}"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def investigations_list_kb(investigations: list) -> InlineKeyboardMarkup:
    buttons = []
    for inv in investigations:
        title = inv["title"][:30]
        date = inv["updated_at"][:10] if inv["updated_at"] else "?"
        buttons.append([
            InlineKeyboardButton(
                text=f"📁 {title} ({date})",
                callback_data=f"open_inv:{inv['id']}"
            )
        ])
    buttons.append([InlineKeyboardButton(text="➕ Новое", callback_data="new_inv")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ─── AI subscription gate ─────────────────────────────────────────────────────

def subscribe_kb() -> InlineKeyboardMarkup:
    from config import config
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться на ШколоДрайв", url=config.CHANNEL_INVITE)],
        [InlineKeyboardButton(text="✅ Я подписался — проверить", callback_data="check_sub")],
    ])


def ai_provider_kb(current_provider: str = "auto") -> InlineKeyboardMarkup:
    def button(provider: str) -> InlineKeyboardButton:
        label = AI_PROVIDER_LABELS.get(provider, provider.title())
        if provider == current_provider:
            label = f"✅ {label}"
        return InlineKeyboardButton(text=label, callback_data=f"ai_provider:{provider}")

    return InlineKeyboardMarkup(inline_keyboard=[
        [button("auto"), button("gemini")],
        [button("gemma"), button("local")],
    ])


# ─── Admin keyboard ───────────────────────────────────────────────────────────

def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Переиндексировать канал", callback_data="admin_reindex"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
        ],
        [
            InlineKeyboardButton(text="🆕 Новые связи", callback_data="admin_new_links"),
        ],
    ])


# ─── Message templates ────────────────────────────────────────────────────────

WELCOME_TEXT = """
<b>OverNetting Bot</b> — OSINT-анализ и визуализация связей.

Отправь мне любые данные:
• никнейм, имя, фамилию
• номер телефона, почту
• Telegram @username или ссылку
• город, адрес, координаты
• IP, хэш, номер авто

Бот автоматически найдёт сущности, определит связи и построит граф.
""".strip()

AI_LOCKED_TEXT = """
<b>ShkoloDrive AI</b> доступен только подписчикам канала.

Подпишись на <a href="https://t.me/ShkoloDrive">@ShkoloDrive</a> и получи доступ к OSINT-помощнику.
""".strip()

AI_READY_TEXT = """
<b>ShkoloDrive AI</b>

Отправь вопрос обычным сообщением или используй команду /ai.
Чтобы вернуться к анализу данных, нажми «🔍 Новое расследование».
""".strip()

PROCESSING_TEXT = "🔍 Анализирую данные..."

NO_ENTITIES_TEXT = "Не удалось найти распознаваемые сущности. Попробуй другие данные."

ABOUT_TEXT = """
<b>OverNetting Bot v1.0</b>

Инструмент визуализации OSINT-связей.

Поддерживает: username, email, телефон, IP, домены, координаты, Telegram, соцсети, хэши, авто и многое другое.

<b>ShkoloDrive AI</b> — доступен подписчикам канала @ShkoloDrive.
""".strip()

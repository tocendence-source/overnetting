"""
OverNetting Bot - main entry point.

Stack: aiogram 3.x, Python 3.11+
"""

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, FSInputFile, Message

from ai_assistant import (
    AI_PROVIDER_AUTO,
    ask_ai,
    analyze_entities_with_ai,
    get_provider_label,
    normalize_provider,
)
from channel_monitor import check_subscription, index_channel_history, process_channel_post
from config import config
from database import (
    add_link,
    clear_links,
    count_posts,
    create_investigation,
    delete_investigation,
    get_ai_provider,
    get_conn,
    get_entities,
    get_investigation,
    get_links,
    init_db,
    list_all_investigations,
    list_investigations,
    set_ai_provider,
    update_investigation_graph,
    upsert_entity,
    search_channel_posts,
)
from entity_extractor import Entity, Link as LinkObj, analyze_relations, extract_entities
from graph_builder import build_graph_html, build_summary, html_to_png, build_posts_match_graph_html
from keyboards import (
    ABOUT_TEXT,
    AI_LOCKED_TEXT,
    AI_READY_TEXT,
    BTN_ABOUT,
    BTN_AI,
    BTN_MY_INVESTIGATIONS,
    BTN_NEW_INVESTIGATION,
    NO_ENTITIES_TEXT,
    PROCESSING_TEXT,
    WELCOME_TEXT,
    admin_kb,
    ai_provider_kb,
    investigation_kb,
    investigations_list_kb,
    main_menu_kb,
    subscribe_kb,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

bot = Bot(
    token=config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

MODE_ANALYSIS = "analysis"
MODE_AI = "ai"

MENU_TEXTS = {
    BTN_NEW_INVESTIGATION,
    BTN_MY_INVESTIGATIONS,
    BTN_AI,
    BTN_ABOUT,
}

_active_inv: dict[int, int] = {}
_user_mode: dict[int, str] = {}


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


def set_user_mode(user_id: int, mode: str):
    _user_mode[user_id] = mode


def get_user_mode(user_id: int) -> str:
    return _user_mode.get(user_id, MODE_ANALYSIS)


def get_user_ai_provider(user_id: int) -> str:
    return normalize_provider(get_ai_provider(user_id, AI_PROVIDER_AUTO))


async def get_or_create_investigation(user_id: int) -> int:
    inv_id = _active_inv.get(user_id)
    if inv_id and get_investigation(inv_id):
        return inv_id

    inv_id = create_investigation(user_id)
    _active_inv[user_id] = inv_id
    return inv_id


def _build_ai_context(inv_id: int | None, limit: int = 20) -> str:
    if not inv_id:
        return ""
    rows = get_entities(inv_id)
    return "\n".join(f"{row['entity_type']}: {row['value']}" for row in rows[:limit])


def _load_graph_payload(inv_id: int) -> tuple[list[Entity], list[LinkObj]]:
    entity_rows = get_entities(inv_id)
    link_rows = get_links(inv_id)

    entities: list[Entity] = []
    entity_map: dict[int, Entity] = {}

    for row in entity_rows:
        entity = Entity(row["entity_type"], row["value"], row["confidence"])
        entities.append(entity)
        entity_map[row["id"]] = entity

    links: list[LinkObj] = []
    for row in link_rows:
        left = entity_map.get(row["entity_a_id"])
        right = entity_map.get(row["entity_b_id"])
        if left and right:
            links.append(
                LinkObj(
                    left.node_id(),
                    right.node_id(),
                    row["link_type"],
                    row["confidence"],
                    row["explanation"] or "",
                )
            )

    return entities, links


def _recalculate_links(inv_id: int) -> tuple[list[Entity], list[LinkObj]]:
    entity_rows = get_entities(inv_id)
    entities: list[Entity] = []
    entity_db_ids: dict[str, int] = {}

    for row in entity_rows:
        entity = Entity(row["entity_type"], row["value"], row["confidence"])
        entities.append(entity)
        entity_db_ids[entity.node_id()] = row["id"]

    clear_links(inv_id)
    for link in analyze_relations(entities):
        a_id = entity_db_ids.get(link.a)
        b_id = entity_db_ids.get(link.b)
        if a_id and b_id:
            add_link(inv_id, a_id, b_id, link.link_type, link.confidence, link.explanation)

    return _load_graph_payload(inv_id)


def _build_graph_assets(
    inv_id: int,
    min_confidence: float | None = None,
    persist: bool = False,
) -> tuple[list[Entity], list[LinkObj], str, str]:
    entities, links = _load_graph_payload(inv_id)
    visible_links = [
        link for link in links
        if min_confidence is None or link.confidence >= min_confidence
    ]

    html_path = build_graph_html(entities, visible_links, inv_id)
    summary = build_summary(entities, visible_links)

    if persist:
        update_investigation_graph(inv_id, html_path, summary)

    return entities, visible_links, html_path, summary


def _cleanup_graph_files(inv_id: int):
    graph_dir = Path(config.GRAPH_OUTPUT_DIR)
    if not graph_dir.exists():
        return

    for pattern in (f"inv_{inv_id}_*.html", f"inv_{inv_id}_*.png"):
        for path in graph_dir.glob(pattern):
            try:
                path.unlink()
            except OSError as exc:
                logger.warning("Не удалось удалить файл графа %s: %s", path, exc)


async def _send_graph_preview(message: Message, inv_id: int, html_path: str, caption: str):
    png_path = await html_to_png(html_path)
    keyboard = investigation_kb(inv_id, has_graph=bool(get_links(inv_id)))

    if png_path and os.path.exists(png_path):
        await message.answer_photo(
            photo=FSInputFile(png_path),
            caption=caption,
            reply_markup=keyboard,
        )
        return

    await message.answer(caption, reply_markup=keyboard)
    if os.path.exists(html_path):
        await message.answer_document(
            FSInputFile(html_path, filename=f"graph_{inv_id}.html"),
            caption="Интерактивный граф (открыть в браузере)",
        )


def _build_entities_block(entities: list[Entity], title: str) -> str:
    if not entities:
        return ""

    entities_list = "\n".join(f"• {entity.display()}" for entity in entities[:12])
    suffix = f"\n…и ещё {len(entities) - 12}" if len(entities) > 12 else ""
    return f"\n\n<b>{title} ({len(entities)}):</b>\n{entities_list}{suffix}"


async def _answer_ai_question(message: Message, question: str):
    thinking = await message.answer("🤖 Думаю над ответом...")
    inv_id = _active_inv.get(message.from_user.id)
    provider = get_user_ai_provider(message.from_user.id)
    answer = await ask_ai(question, _build_ai_context(inv_id), provider=provider)
    label = get_provider_label(provider)
    await thinking.edit_text(f"🤖 <b>ShkoloDrive AI</b> <i>({label})</i>\n\n{answer}")

    # Extra: always send a small visualization after the text answer
    # based on similar channel posts (helps with FIO / fuzzy queries in local mode too).
    try:
        matches = search_channel_posts(question, limit=6)
        if matches:
            local_inv_id = inv_id or await get_or_create_investigation(message.from_user.id)
            html_path = build_posts_match_graph_html(question, matches, local_inv_id)
            png_path = await html_to_png(html_path)
            if png_path and os.path.exists(png_path):
                await message.answer_photo(FSInputFile(png_path), caption="Визуализация: похожие посты")
    except Exception as exc:
        logger.warning("Не удалось отправить визуализацию к ответу AI: %s", exc)


def _build_ai_ready_text(user_id: int) -> str:
    provider = get_user_ai_provider(user_id)
    label = get_provider_label(provider)
    return (
        f"{AI_READY_TEXT}\n\n"
        f"<b>Текущий режим:</b> {label}\n"
        f"Auto: Gemini → Gemma → Local fallback."
    )


async def _create_new_investigation(message: Message, user_id: int):
    inv_id = create_investigation(user_id)
    _active_inv[user_id] = inv_id
    set_user_mode(user_id, MODE_ANALYSIS)
    await message.answer(
        f"✅ Новое расследование #{inv_id} создано.\n\n"
        "Отправляй данные, и я начну анализировать связи."
    )


async def process_and_graph(message: Message, text: str, inv_id: int):
    """Core pipeline: extract -> link -> graph -> reply."""
    status = await message.answer(PROCESSING_TEXT)

    entities = extract_entities(text)
    if not entities:
        await status.delete()
        await message.answer(NO_ENTITIES_TEXT)
        return

    entity_db_ids: dict[str, int] = {}
    for entity in entities:
        db_id = upsert_entity(inv_id, entity.type, entity.value, entity.confidence, "user_input")
        entity_db_ids[entity.node_id()] = db_id

    for link in analyze_relations(entities):
        a_id = entity_db_ids.get(link.a)
        b_id = entity_db_ids.get(link.b)
        if a_id and b_id:
            add_link(inv_id, a_id, b_id, link.link_type, link.confidence, link.explanation)

    all_entities, all_links, html_path, summary = _build_graph_assets(inv_id, persist=True)

    await status.delete()

    caption = (
        f"<b>Расследование #{inv_id}</b>\n\n"
        f"{summary}"
        f"{_build_entities_block(entities, 'Новые сущности')}"
    )
    await _send_graph_preview(message, inv_id, html_path, caption)

    entities_text = "\n".join(f"{entity.type}: {entity.value}" for entity in entities)
    provider = get_user_ai_provider(message.from_user.id)
    hint = await analyze_entities_with_ai(entities_text, provider=provider)
    if hint:
        await message.answer(f"🤖 <b>ShkoloDrive AI:</b>\n{hint}")


@dp.message(CommandStart())
async def cmd_start(message: Message):
    set_user_mode(message.from_user.id, MODE_ANALYSIS)
    set_ai_provider(message.from_user.id, get_user_ai_provider(message.from_user.id))
    await message.answer(WELCOME_TEXT, reply_markup=main_menu_kb())


@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return

    await message.answer(
        f"<b>Панель администратора</b>\nПостов в базе: {count_posts()}",
        reply_markup=admin_kb(),
    )


@dp.message(F.text == BTN_ABOUT)
async def cmd_about(message: Message):
    await message.answer(ABOUT_TEXT)


@dp.message(F.text == BTN_MY_INVESTIGATIONS)
async def cmd_my_investigations(message: Message):
    investigations = list_investigations(message.from_user.id)
    if not investigations:
        await message.answer("У тебя пока нет сохранённых расследований.")
        return

    set_user_mode(message.from_user.id, MODE_ANALYSIS)
    await message.answer(
        f"<b>Твои расследования ({len(investigations)}):</b>",
        reply_markup=investigations_list_kb(investigations),
    )


@dp.message(F.text == BTN_NEW_INVESTIGATION)
async def cmd_new_investigation(message: Message):
    await _create_new_investigation(message, message.from_user.id)


@dp.message(F.text == BTN_AI)
async def cmd_ai(message: Message):
    is_member = await check_subscription(bot, message.from_user.id)
    if not is_member:
        await message.answer(AI_LOCKED_TEXT, reply_markup=subscribe_kb(), disable_web_page_preview=True)
        return

    set_user_mode(message.from_user.id, MODE_AI)
    await message.answer(
        _build_ai_ready_text(message.from_user.id),
        reply_markup=ai_provider_kb(get_user_ai_provider(message.from_user.id)),
    )


@dp.message(Command("ai"))
async def cmd_ai_question(message: Message):
    is_member = await check_subscription(bot, message.from_user.id)
    if not is_member:
        await message.answer(AI_LOCKED_TEXT, reply_markup=subscribe_kb(), disable_web_page_preview=True)
        return

    set_user_mode(message.from_user.id, MODE_AI)
    question = message.text.removeprefix("/ai").strip()
    if not question:
        await message.answer(
            _build_ai_ready_text(message.from_user.id),
            reply_markup=ai_provider_kb(get_user_ai_provider(message.from_user.id)),
        )
        return

    await _answer_ai_question(message, question)


@dp.message(F.text & ~F.text.startswith("/") & ~F.text.in_(MENU_TEXTS))
async def handle_text_input(message: Message):
    user_id = message.from_user.id

    if get_user_mode(user_id) == MODE_AI:
        is_member = await check_subscription(bot, user_id)
        if not is_member:
            await message.answer(AI_LOCKED_TEXT, reply_markup=subscribe_kb(), disable_web_page_preview=True)
            return

        await _answer_ai_question(message, message.text)
        return

    inv_id = await get_or_create_investigation(user_id)
    set_user_mode(user_id, MODE_ANALYSIS)
    await process_and_graph(message, message.text, inv_id)


@dp.callback_query(F.data == "new_inv")
async def cb_new_investigation(cb: CallbackQuery):
    await cb.answer()
    await _create_new_investigation(cb.message, cb.from_user.id)


@dp.callback_query(F.data.startswith("open_inv:"))
async def cb_open_investigation(cb: CallbackQuery):
    inv_id = int(cb.data.split(":")[1])
    _active_inv[cb.from_user.id] = inv_id
    set_user_mode(cb.from_user.id, MODE_ANALYSIS)

    inv = get_investigation(inv_id)
    if not inv:
        await cb.answer("Расследование не найдено.", show_alert=True)
        return

    summary = inv["summary"] or "Нет данных"
    await cb.message.edit_text(
        f"<b>Расследование #{inv_id}</b>\n<i>{inv['title']}</i>\n\n{summary}",
        reply_markup=investigation_kb(inv_id, has_graph=bool(inv["graph_path"])),
    )
    await cb.answer()


@dp.callback_query(F.data.startswith("graph:"))
async def cb_graph(cb: CallbackQuery):
    inv_id = int(cb.data.split(":")[1])
    inv = get_investigation(inv_id)
    if not inv or not inv["graph_path"]:
        await cb.answer("Граф ещё не построен.", show_alert=True)
        return

    html_path = inv["graph_path"]
    png_path = html_path.replace(".html", ".png")
    await cb.answer("Отправляю граф...")

    if os.path.exists(png_path):
        await cb.message.answer_photo(FSInputFile(png_path), caption=f"Граф #{inv_id}")
    elif os.path.exists(html_path):
        await cb.message.answer_document(
            FSInputFile(html_path, filename=f"graph_{inv_id}.html"),
            caption="Интерактивный граф (открыть в браузере)",
        )


@dp.callback_query(F.data.startswith("ai_hint:"))
async def cb_ai_hint(cb: CallbackQuery):
    is_member = await check_subscription(bot, cb.from_user.id)
    if not is_member:
        await cb.answer("Нужна подписка на канал.", show_alert=True)
        return

    inv_id = int(cb.data.split(":")[1])
    rows = get_entities(inv_id)
    if not rows:
        await cb.answer("Нет данных для анализа.", show_alert=True)
        return

    entities_text = "\n".join(f"{row['entity_type']}: {row['value']}" for row in rows[:20])
    await cb.answer("Анализирую...")
    provider = get_user_ai_provider(cb.from_user.id)
    hint = await analyze_entities_with_ai(entities_text, provider=provider)
    await cb.message.answer(f"🤖 <b>ShkoloDrive AI:</b>\n\n{hint or 'Подсказок пока нет.'}")


@dp.callback_query(F.data.startswith("save:"))
async def cb_save(cb: CallbackQuery):
    await cb.answer("Расследование уже сохранено в базе.", show_alert=True)


@dp.callback_query(F.data.startswith("refresh:"))
async def cb_refresh(cb: CallbackQuery):
    inv_id = int(cb.data.split(":")[1])
    if not get_investigation(inv_id):
        await cb.answer("Расследование не найдено.", show_alert=True)
        return

    await cb.answer("Обновляю связи...")
    entities, links = _recalculate_links(inv_id)
    entities, links, html_path, summary = _build_graph_assets(inv_id, persist=True)
    caption = (
        f"<b>Расследование #{inv_id}</b>\n\n"
        f"{summary}"
        f"{_build_entities_block(entities, 'Все сущности')}"
    )
    await _send_graph_preview(cb.message, inv_id, html_path, caption)


@dp.callback_query(F.data.startswith("strong_only:"))
async def cb_strong_only(cb: CallbackQuery):
    inv_id = int(cb.data.split(":")[1])
    if not get_investigation(inv_id):
        await cb.answer("Расследование не найдено.", show_alert=True)
        return

    await cb.answer("Показываю сильные связи...")
    entities, links, html_path, summary = _build_graph_assets(
        inv_id,
        min_confidence=config.STRONG_LINK_THRESHOLD,
        persist=False,
    )
    caption = (
        f"<b>Сильные связи #{inv_id}</b>\n\n"
        f"{summary}"
        f"{_build_entities_block(entities, 'Сущности')}"
    )
    await _send_graph_preview(cb.message, inv_id, html_path, caption)


@dp.callback_query(F.data.startswith("all_links:"))
async def cb_all_links(cb: CallbackQuery):
    inv_id = int(cb.data.split(":")[1])
    if not get_investigation(inv_id):
        await cb.answer("Расследование не найдено.", show_alert=True)
        return

    await cb.answer("Показываю все связи...")
    entities, links, html_path, summary = _build_graph_assets(inv_id, persist=False)
    caption = (
        f"<b>Все связи #{inv_id}</b>\n\n"
        f"{summary}"
        f"{_build_entities_block(entities, 'Сущности')}"
    )
    await _send_graph_preview(cb.message, inv_id, html_path, caption)


@dp.callback_query(F.data.startswith("delete:"))
async def cb_delete(cb: CallbackQuery):
    inv_id = int(cb.data.split(":")[1])
    inv = get_investigation(inv_id)
    if not inv:
        await cb.answer("Расследование уже удалено.", show_alert=True)
        return

    delete_investigation(inv_id)
    _cleanup_graph_files(inv_id)

    if _active_inv.get(cb.from_user.id) == inv_id:
        _active_inv.pop(cb.from_user.id, None)

    set_user_mode(cb.from_user.id, MODE_ANALYSIS)
    await cb.message.edit_text("🗑 Расследование удалено.")
    await cb.answer("Удалено.")


@dp.callback_query(F.data.startswith("export_png:"))
async def cb_export_png(cb: CallbackQuery):
    inv_id = int(cb.data.split(":")[1])
    inv = get_investigation(inv_id)
    if not inv or not inv["graph_path"]:
        await cb.answer("Граф не найден.", show_alert=True)
        return

    await cb.answer("Генерирую PNG...")
    png_path = await html_to_png(inv["graph_path"])
    if png_path and os.path.exists(png_path):
        await cb.message.answer_photo(FSInputFile(png_path), caption=f"PNG граф #{inv_id}")
    else:
        await cb.message.answer("Playwright не установлен или Chromium недоступен. Отправляю HTML.")
        await cb.message.answer_document(
            FSInputFile(inv["graph_path"], filename=f"graph_{inv_id}.html")
        )


@dp.callback_query(F.data == "check_sub")
async def cb_check_sub(cb: CallbackQuery):
    is_member = await check_subscription(bot, cb.from_user.id, force=True)
    if is_member:
        set_user_mode(cb.from_user.id, MODE_AI)
        await cb.message.edit_text(
            "✅ Подписка подтверждена.\n\n"
            f"{_build_ai_ready_text(cb.from_user.id)}",
            reply_markup=ai_provider_kb(get_user_ai_provider(cb.from_user.id)),
        )
        return

    await cb.answer("Telegram всё ещё не подтверждает подписку.", show_alert=True)


@dp.callback_query(F.data.startswith("ai_provider:"))
async def cb_ai_provider(cb: CallbackQuery):
    provider = normalize_provider(cb.data.split(":", 1)[1])
    set_ai_provider(cb.from_user.id, provider)
    set_user_mode(cb.from_user.id, MODE_AI)
    await cb.message.edit_text(
        _build_ai_ready_text(cb.from_user.id),
        reply_markup=ai_provider_kb(provider),
    )
    await cb.answer(f"Режим AI: {get_provider_label(provider)}")


@dp.callback_query(F.data == "admin_reindex")
async def cb_admin_reindex(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return

    await cb.answer("Начинаю индексацию...")
    count = await index_channel_history(bot)
    await cb.message.answer(f"Индексация завершена. Постов: {count}")


@dp.callback_query(F.data == "admin_stats")
async def cb_admin_stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return

    with get_conn() as conn:
        inv_count = conn.execute("SELECT COUNT(*) FROM investigations").fetchone()[0]
        entity_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        link_count = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
        post_count = conn.execute("SELECT COUNT(*) FROM channel_posts").fetchone()[0]

    await cb.message.answer(
        f"<b>Статистика OverNetting</b>\n\n"
        f"Расследований: {inv_count}\n"
        f"Сущностей: {entity_count}\n"
        f"Связей: {link_count}\n"
        f"Постов в базе: {post_count}"
    )
    await cb.answer()


@dp.callback_query(F.data == "admin_new_links")
async def cb_admin_new_links(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return

    investigations = list_all_investigations()
    if not investigations:
        await cb.answer("Нет расследований для обновления.", show_alert=True)
        return

    await cb.answer("Пересчитываю связи...")
    total_links = 0
    for investigation in investigations:
        _, links = _recalculate_links(investigation["id"])
        _build_graph_assets(investigation["id"], persist=True)
        total_links += len(links)

    await cb.message.answer(
        f"✅ Связи пересчитаны.\n"
        f"Расследований: {len(investigations)}\n"
        f"Текущих связей: {total_links}"
    )


@dp.channel_post()
async def handle_channel_post(message: Message):
    """Auto-index new posts from the configured channel."""
    channel_id = str(config.CHANNEL_ID).strip()
    chat_username = getattr(message.chat, "username", None)
    matches_channel = False

    if channel_id.startswith("@"):
        matches_channel = str(chat_username or "").lower() == channel_id.lstrip("@").lower()
    else:
        matches_channel = str(message.chat.id) == channel_id

    if matches_channel:
        await process_channel_post(message)


async def on_startup(bot: Bot):
    init_db()
    logger.info("Database initialized.")
    await index_channel_history(bot)
    logger.info("OverNetting Bot started.")


async def main():
    dp.startup.register(on_startup)
    logger.info("Starting polling...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())

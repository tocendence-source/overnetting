"""
ShkoloDrive AI - OSINT assistant.
"""

import hashlib
import logging
import re
import warnings
from datetime import datetime, timedelta

warnings.simplefilter("ignore", FutureWarning)

import google.generativeai as genai

from config import config
from database import get_recent_posts, search_channel_posts
from entity_extractor import extract_entities

logger = logging.getLogger(__name__)

AI_PROVIDER_AUTO = "auto"
AI_PROVIDER_GEMINI = "gemini"
AI_PROVIDER_GEMMA = "gemma"
AI_PROVIDER_LOCAL = "local"

AI_PROVIDER_LABELS = {
    AI_PROVIDER_AUTO: "Auto",
    AI_PROVIDER_GEMINI: "Gemini 💰",
    AI_PROVIDER_GEMMA: "Gemma 🆓",
    AI_PROVIDER_LOCAL: "Local 📚",
}

GEMINI_MODELS = (
    "models/gemini-2.5-flash",
    "models/gemini-flash-latest",
    "models/gemini-2.0-flash",
)

# Gemma 3 IDs are not available on all API keys; Gemma 2 works on AI Studio.
GEMMA_MODELS = (
    "models/gemma-2-27b-it",
    "models/gemma-2-9b-it",
    "models/gemma-2-2b-it",
)

_quota_blocked_until: dict[str, datetime] = {}


if config.GOOGLE_API_KEY:
    genai.configure(api_key=config.GOOGLE_API_KEY)


def normalize_provider(provider: str | None) -> str:
    value = (provider or "").strip().lower()
    if value in AI_PROVIDER_LABELS:
        return value
    return AI_PROVIDER_AUTO


def get_provider_label(provider: str | None) -> str:
    return AI_PROVIDER_LABELS.get(normalize_provider(provider), "Auto")


def _is_model_lookup_error(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = (
        "404",
        "not found",
        "is not supported",
        "unsupported",
        "unknown model",
    )
    return any(marker in text for marker in markers)


def _is_quota_error(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = (
        "429",
        "quota",
        "rate limit",
        "resource exhausted",
        "billing details",
        "retry in",
    )
    return any(marker in text for marker in markers)


def _extract_retry_after_seconds(exc: Exception) -> int | None:
    match = re.search(r"retry in\s+(\d+(?:\.\d+)?)s", str(exc).lower())
    if not match:
        return None
    return max(1, int(float(match.group(1))))


def _quota_cooldown_active(model_name: str) -> bool:
    blocked_until = _quota_blocked_until.get(model_name)
    return blocked_until is not None and datetime.now() < blocked_until


def _quota_cooldown_seconds_left(model_name: str) -> int:
    blocked_until = _quota_blocked_until.get(model_name)
    if blocked_until is None or datetime.now() >= blocked_until:
        return 0
    return max(1, int((blocked_until - datetime.now()).total_seconds()))


def _set_quota_cooldown(model_name: str, exc: Exception):
    retry_after = _extract_retry_after_seconds(exc) or 900
    blocked_until = datetime.now() + timedelta(seconds=retry_after)
    previous = _quota_blocked_until.get(model_name)
    if previous is None or blocked_until > previous:
        _quota_blocked_until[model_name] = blocked_until


def _configured_model_candidates() -> list[str]:
    configured = (config.AI_MODEL or "").strip()
    if not configured:
        return []

    candidates = [configured]
    if not configured.startswith("models/"):
        candidates.append(f"models/{configured}")
    return candidates


def _model_candidates_for_provider(provider: str) -> list[str]:
    provider = normalize_provider(provider)
    configured = _configured_model_candidates()

    if provider == AI_PROVIDER_GEMINI:
        return list(dict.fromkeys(configured + list(GEMINI_MODELS)))

    if provider == AI_PROVIDER_GEMMA:
        return list(dict.fromkeys(GEMMA_MODELS))

    if provider == AI_PROVIDER_AUTO:
        return list(dict.fromkeys(configured + list(GEMINI_MODELS) + list(GEMMA_MODELS)))

    return []


def _get_model(model_name: str, system_instruction: str | None = None) -> genai.GenerativeModel:
    kwargs = {"model_name": model_name}
    if system_instruction and "gemma" not in model_name.lower():
        kwargs["system_instruction"] = system_instruction
    return genai.GenerativeModel(
        **kwargs,
    )


def _extract_response_text(response) -> str:
    try:
        text = response.text
        if text:
            return text.strip()
    except Exception:
        pass

    try:
        parts: list[str] = []
        for candidate in response.candidates or []:
            content = getattr(candidate, "content", None)
            if not content:
                continue
            for part in content.parts or []:
                part_text = getattr(part, "text", None)
                if part_text:
                    parts.append(part_text)
        return " ".join(parts).strip()
    except Exception:
        return ""


def _posts_to_context_chunks(posts: list, snippet_len: int = 600) -> list[str]:
    chunks: list[str] = []
    for post in posts:
        if isinstance(post, dict):
            date_str = (post.get("date") or "")[:10] or "?"
            text = (post.get("text") or "").strip()[:snippet_len]
            mid = post.get("message_id")
        else:
            date_str = (post["date"] or "")[:10] if post["date"] else "?"
            text = (post["text"] or "").strip()[:snippet_len]
            mid = post["message_id"] if "message_id" in post.keys() else None

        if not text:
            continue
        prefix = f"[#{mid} {date_str}]" if mid else f"[{date_str}]"
        chunks.append(f"{prefix} {text}")
    return chunks


def _build_relevant_posts_context(question: str = "", limit: int = 5) -> str:
    """Context from channel DB: search by question, else recent posts."""
    try:
        if question.strip():
            matches = search_channel_posts(question, limit=limit)
            if matches:
                chunks = _posts_to_context_chunks(matches)
                return "Релевантные посты канала (по запросу):\n\n" + "\n\n".join(chunks)

        posts = get_recent_posts(limit=30)
        if not posts:
            return "База знаний пуста. Канал еще не проиндексирован."

        chunks = _posts_to_context_chunks(posts)
        return "Последние посты канала:\n\n" + "\n\n".join(chunks)
    except Exception as exc:
        logger.error("Ошибка при сборке контекста из БД: %s", exc)
        return "Ошибка доступа к базе знаний."


def _build_knowledge_context(question: str = "") -> str:
    return _build_relevant_posts_context(question)


def _pick_hint(options: list[str], seed_text: str, salt: str) -> str:
    if not options:
        return ""
    digest = hashlib.md5(f"{salt}:{seed_text}".encode("utf-8", errors="ignore")).hexdigest()
    return options[int(digest[:8], 16) % len(options)]


def _extract_search_terms(text: str) -> list[str]:
    terms = re.findall(r"[A-Za-zА-Яа-яЁё0-9_@.+-]{4,}", text.lower())
    stopwords = {
        "проверь", "проверить", "поиск", "найти", "нужно", "можно", "через", "после",
        "только", "какой", "какая", "какие", "делать", "этого", "этот", "ответ",
        "данные", "человека", "человек", "аккаунт", "помоги", "пожалуйста", "нужен",
    }
    unique = []
    for term in terms:
        if term not in stopwords and term not in unique:
            unique.append(term)
    return unique[:6]


def _build_knowledge_hint(seed_text: str) -> str:
    terms = _extract_search_terms(seed_text)
    if not terms:
        return ""

    try:
        posts = get_recent_posts(limit=40)
    except Exception:
        return ""

    best_post = None
    best_score = 0
    for post in posts:
        text = (post["text"] or "").lower()
        score = sum(1 for term in terms if term in text)
        if score > best_score:
            best_score = score
            best_post = post

    if not best_post or best_score == 0:
        return ""

    matched_terms = [term for term in terms if term in (best_post["text"] or "").lower()][:3]
    date_str = best_post["date"][:10] if best_post["date"] else "?"
    joined = ", ".join(matched_terms)
    return f"Из базы канала: сверяйте запрос с постами за {date_str} по теме {joined}."


def _build_local_posts_hint(question: str) -> str:
    matches = search_channel_posts(question, limit=3)
    if not matches:
        return ""

    lines: list[str] = []
    for m in matches:
        mid = m["message_id"]
        date = (m["date"] or "")[:10]
        snippet = (m["text"] or "").strip().replace("\n", " ")
        if len(snippet) > 140:
            snippet = snippet[:138] + "…"
        link = ""
        if config.CHANNEL_PUBLIC_USERNAME:
            link = f" (t.me/{config.CHANNEL_PUBLIC_USERNAME}/{mid})"
        lines.append(f"- #{mid} {date}{link}\n  {snippet}")

    return "Похожие посты в базе:\n" + "\n".join(lines)


def _build_local_fallback(question: str = "", context: str = "") -> str:
    combined_text = "\n".join(part for part in (question, context) if part)
    seed_text = combined_text or "local-fallback"
    text_lower = combined_text.lower()
    entities = extract_entities(combined_text)
    entity_types = {entity.type for entity in entities}
    hints: list[str] = []
    email_context = any(word in text_lower for word in ("email", "почт", "mail@", "@gmail", "@yandex", "@outlook"))
    social_context = any(word in text_lower for word in ("telegram", "телеграм", "username", "ник", "профиль", "канал", "t.me/"))
    email_domains = {
        "@" + entity.value.split("@", 1)[1].split(".", 1)[0].lower()
        for entity in entities
        if entity.type == "Email" and "@" in entity.value
    }

    social_options = [
        "🔍 Проверьте совпадающие username и их вариации в Telegram, соцсетях и поиске по точной фразе.",
        "Начните с поиска того же ника в Telegram, VK, Instagram, TikTok и по кавычкам в обычном поиске.",
        "Сверьте одинаковые username между мессенджерами и соцсетями, а затем проверьте связанные ссылки в био.",
    ]
    email_options = [
        "Сопоставьте email с утечками, доменом почты и публичными профилями с той же локальной частью адреса.",
        "Проверьте локальную часть email отдельно, затем домен и повторения адреса в старых регистрациях и профилях.",
        "Сначала пробейте email по утечкам, затем ищите совпадения ника из адреса в соцсетях и мессенджерах.",
    ]
    phone_options = [
        "Начните с обратного поиска по номеру и сравните его с мессенджерами, объявлениями и утечками.",
        "Сначала проверьте номер в обратных поисках и объявлениях, затем ищите привязанные аккаунты в мессенджерах.",
        "Проверьте, где номер светился в объявлениях, доставках, чатах и старых базах, а потом связывайте с username.",
    ]
    domain_options = [
        "Проверьте WHOIS, passive DNS, историю через веб-архив и связанные субдомены.",
        "Начните с WHOIS и DNS-истории, затем переходите к сертификатам, архивам и связанным доменам.",
        "Сверьте домен через архивы, passive DNS и сертификаты, чтобы найти старые панели, поддомены и контакты.",
    ]
    ip_options = [
        "Проверьте ASN, географию, passive DNS и соседние хосты на том же диапазоне.",
        "Сначала определите ASN и географию, затем посмотрите passive DNS и соседние сервисы на этом IP.",
        "Проверьте IP через ASN, reverse DNS и соседние узлы, чтобы понять провайдера и связанные домены.",
    ]
    geo_options = [
        "Сопоставьте точку с картами, панорамами, кадастром и локальными организациями рядом.",
        "Начните с карт и панорам, затем проверьте кадастр, адресные реестры и объекты по соседству.",
        "Сверьте координаты с панорамами, кадастром и локальными карточками организаций вокруг точки.",
    ]
    person_options = [
        "Начните с устойчивого идентификатора: username, email, телефон или профиль, а потом сводите совпадения между площадками.",
        "Сначала найдите один подтверждаемый идентификатор человека, затем стройте связи только через повторяющиеся совпадения.",
        "Опорной точкой сделайте контакт или профиль, а дальше проверяйте одинаковые ники, фото, гео и круг связей.",
    ]
    photo_options = [
        "Сделайте обратный поиск по изображению (Google Lens, Yandex, TinEye), затем проверьте EXIF и геометки на кадре.",
        "Начните с reverse image search и анализа метаданных файла, потом сопоставьте фон, вывески и объекты с картами и архивами.",
        "Проверьте фото через поиск по картинке, извлеките EXIF/GPS, определите место по панорамам и сверьте детали с открытыми источниками.",
    ]
    company_options = [
        "Проверьте домен, юрназвание, контактные email и соцсети компании, а затем связывайте их между собой.",
        "Начните с юрназвания и домена, потом ищите сотрудников, контакты, публикации и связанные сайты.",
        "Сверьте компанию по домену, регистрационным данным, соцсетям и повторяющимся контактам на сайтах.",
    ]

    has_social_entities = any(
        entity.type in {"Telegram", "Social"} and entity.value.lower() not in email_domains
        for entity in entities
    ) or any(
        entity.type == "Username" and entity.value.lower() not in email_domains
        for entity in entities
    ) and (social_context or not email_context)

    if has_social_entities:
        hints.append(_pick_hint(social_options, seed_text, "social"))
    if "Email" in entity_types:
        hints.append(_pick_hint(email_options, seed_text, "email"))
    if "Phone" in entity_types:
        hints.append(_pick_hint(phone_options, seed_text, "phone"))
    if {"Domain", "Website"} & entity_types:
        hints.append(_pick_hint(domain_options, seed_text, "domain"))
    if "IP" in entity_types:
        hints.append(_pick_hint(ip_options, seed_text, "ip"))
    if {"Coordinates", "City", "Address"} & entity_types:
        hints.append(_pick_hint(geo_options, seed_text, "geo"))

    if not hints and any(word in text_lower for word in ("человек", "личность", "профиль", "имя", "фамил", "ник")):
        hints.append(_pick_hint(person_options, seed_text, "person"))
    if not hints and any(word in text_lower for word in ("компан", "организац", "фирм", "сайт", "домен")):
        hints.append(_pick_hint(company_options, seed_text, "company"))
    if not hints and any(word in text_lower for word in ("телеграм", "telegram", "канал", "username", "@")):
        hints.append(_pick_hint(social_options, seed_text, "social-text"))
    if not hints and any(word in text_lower for word in ("почт", "email", "@gmail", "@mail", "@yandex")):
        hints.append(_pick_hint(email_options, seed_text, "email-text"))
    if any(word in text_lower for word in ("фото", "фотограф", "изображен", "картинк", "скрин", "снимок", "picture", "image")):
        hints.insert(0, _pick_hint(photo_options, seed_text, "photo"))

    if not hints:
        hints.append(_pick_hint(person_options, seed_text, "default-person"))
        hints.append(
            "После этого ищите повторяющиеся идентификаторы между платформами и фиксируйте только подтверждаемые связи."
        )

    # Add "similar posts" hint first (this is the main value of local mode)
    posts_hint = _build_local_posts_hint(question or seed_text)
    if posts_hint:
        hints.append(posts_hint)

    knowledge_hint = _build_knowledge_hint(seed_text)
    if knowledge_hint:
        hints.append(knowledge_hint)

    unique_hints = []
    for hint in hints:
        if hint and hint not in unique_hints:
            unique_hints.append(hint)

    return "\n\n".join(unique_hints[:3])


def _build_local_answer(provider: str, question: str = "", context: str = "", reason: str = "") -> str:
    provider_label = get_provider_label(provider)
    prefix = f"⚠️ {provider_label} временно недоступен. " if reason else ""
    if provider == AI_PROVIDER_LOCAL:
        prefix = "🧭 Local режим. "
    return prefix + _build_local_fallback(question, context)


def _model_family_label(model_name: str) -> str:
    if "gemma" in model_name.lower():
        return "Gemma"
    return "Gemini"


async def _generate_text(
    prompt: str,
    system_instruction: str,
    provider: str,
    generation_config: genai.types.GenerationConfig | None = None,
) -> tuple[str, str]:
    last_exc: Exception | None = None

    for model_name in _model_candidates_for_provider(provider):
        if _quota_cooldown_active(model_name):
            logger.info(
                "Пропускаю %s: активен cooldown %s сек.",
                model_name,
                _quota_cooldown_seconds_left(model_name),
            )
            continue

        try:
            model = _get_model(model_name, system_instruction=system_instruction)
            request_prompt = prompt
            if "gemma" in model_name.lower():
                request_prompt = f"{system_instruction}\n\n{prompt}"
            response = await model.generate_content_async(
                request_prompt,
                generation_config=generation_config,
            )
            text = _extract_response_text(response)
            if text:
                return text, model_name

            logger.warning("%s returned an empty response", model_name)
        except Exception as exc:
            last_exc = exc
            if _is_quota_error(exc):
                _set_quota_cooldown(model_name, exc)
                logger.warning("%s quota exceeded for %s", _model_family_label(model_name), model_name)
                continue

            logger.warning("%s request failed: %s", model_name, exc)
            if _is_model_lookup_error(exc):
                continue
            raise

    if last_exc:
        raise last_exc

    raise RuntimeError("No available model candidates for this provider.")


async def _generate_with_fallback(
    prompt: str,
    system_instruction: str,
    provider: str,
    generation_config: genai.types.GenerationConfig | None = None,
) -> tuple[str, str]:
    """Try requested provider; on Gemma/model errors fall back to Gemini."""
    try:
        return await _generate_text(prompt, system_instruction, provider, generation_config)
    except Exception as exc:
        if provider == AI_PROVIDER_GEMINI or not _is_model_lookup_error(exc):
            raise
        logger.warning("Провайдер %s недоступен (%s), пробую Gemini.", provider, exc)
        return await _generate_text(
            prompt,
            system_instruction,
            AI_PROVIDER_GEMINI,
            generation_config,
        )


SYSTEM_PROMPT = """Ты - ShkoloDrive AI, эксперт-помощник по OSINT.
Твоя задача: на основе имеющихся знаний направлять исследователя.

ПРАВИЛА:
1. Не давай готовых ответов. Указывай только СЛЕДУЮЩУЮ точку поиска (инструмент, метод, реестр).
2. Стиль: лаконичный, профессиональный, без лишних слов.
3. Используй базу знаний ниже для поиска специфических методов, проверенных каналом ShkoloDrive.
4. Максимум 3-5 предложений.
5. Эмодзи запрещены, кроме одного 🔍 в начале.

БАЗА ЗНАНИЙ (посты канала):
{knowledge}
"""


async def ask_ai(user_question: str, user_context: str = "", provider: str = AI_PROVIDER_AUTO) -> str:
    provider = normalize_provider(provider)

    if provider == AI_PROVIDER_LOCAL:
        return _build_local_answer(provider, user_question, user_context)

    if not config.GOOGLE_API_KEY:
        return (
            "⚠️ API-ключ Gemini не настроен. "
            f"{_build_local_fallback(user_question, user_context)}"
        )

    knowledge = _build_knowledge_context(user_question)
    system = SYSTEM_PROMPT.format(knowledge=knowledge)

    prompt_parts = []
    if user_context:
        prompt_parts.append(f"ДАННЫЕ РАССЛЕДОВАНИЯ:\n{user_context}")
    prompt_parts.append(f"ВОПРОС ПОЛЬЗОВАТЕЛЯ: {user_question}")
    full_prompt = "\n\n".join(prompt_parts)

    try:
        text, model_name = await _generate_with_fallback(
            full_prompt,
            system_instruction=system,
            provider=provider,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=1024,
                temperature=0.7,
            ),
        )
        return text
    except Exception as exc:
        if _is_quota_error(exc) or _is_model_lookup_error(exc):
            logger.warning("%s недоступен: %s", get_provider_label(provider), exc)
            return _build_local_answer(
                provider,
                user_question,
                user_context,
                reason="quota" if _is_quota_error(exc) else "models",
            )

        logger.error("Ошибка AI (%s): %s", get_provider_label(provider), exc)
        return _build_local_answer(provider, user_question, user_context, reason="error")


async def analyze_entities_with_ai(
    entities_text: str,
    provider: str = AI_PROVIDER_AUTO,
) -> str:
    provider = normalize_provider(provider)
    if not entities_text:
        return ""

    if provider == AI_PROVIDER_LOCAL:
        return _build_local_fallback(context=entities_text)

    if not config.GOOGLE_API_KEY:
        return _build_local_fallback(context=entities_text)

    knowledge = _build_knowledge_context(entities_text)
    system = SYSTEM_PROMPT.format(knowledge=knowledge)
    prompt = (
        f"Я нашел следующие зацепки (сущности):\n{entities_text}\n\n"
        "Проанализируй их. Какой метод поиска из базы знаний будет наиболее эффективен сейчас?"
    )

    try:
        text, model_name = await _generate_with_fallback(
            prompt,
            system_instruction=system,
            provider=provider,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=512,
                temperature=0.5,
            ),
        )
        return text
    except Exception as exc:
        if _is_quota_error(exc) or _is_model_lookup_error(exc):
            logger.warning("%s недоступен для анализа сущностей: %s", get_provider_label(provider), exc)
            return _build_local_fallback(context=entities_text)

        logger.error("Ошибка при анализе сущностей (%s): %s", get_provider_label(provider), exc)
        return _build_local_fallback(context=entities_text)

"""
Daily AI usage limits and provider hints for ShkoloDrive AI.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_assistant import (
    AI_PROVIDER_AUTO,
    AI_PROVIDER_GEMINI,
    AI_PROVIDER_GEMMA,
    AI_PROVIDER_LOCAL,
    AI_PROVIDER_LABELS,
    normalize_provider,
)
from config import config
from database import get_ai_daily_usage, increment_ai_daily_usage


@dataclass(frozen=True)
class ProviderQuota:
    key: str
    label: str
    daily_limit: int  # 0 = unlimited
    uses_cloud_api: bool
    cost_note: str
    recommend_note: str


def _parse_limit(raw: str, default: int) -> int:
    try:
        return max(0, int((raw or str(default)).strip()))
    except ValueError:
        return default


def cloud_daily_limit() -> int:
    return _parse_limit(config.AI_DAILY_CLOUD_LIMIT, 20)


def local_daily_limit() -> int:
    return _parse_limit(config.AI_DAILY_LOCAL_LIMIT, 100)


PROVIDER_QUOTAS: dict[str, ProviderQuota] = {
    AI_PROVIDER_LOCAL: ProviderQuota(
        key=AI_PROVIDER_LOCAL,
        label="Local",
        daily_limit=local_daily_limit(),
        uses_cloud_api=False,
        cost_note="бесплатно, без внешнего API",
        recommend_note="без ограничений по API; лимит только на нагрузку бота",
    ),
    AI_PROVIDER_GEMMA: ProviderQuota(
        key=AI_PROVIDER_GEMMA,
        label="Gemma",
        daily_limit=cloud_daily_limit(),
        uses_cloud_api=True,
        cost_note="бесплатный tier Google (квота ключа)",
        recommend_note="до ~15–20 запросов в день на пользователя",
    ),
    AI_PROVIDER_GEMINI: ProviderQuota(
        key=AI_PROVIDER_GEMINI,
        label="Gemini",
        daily_limit=cloud_daily_limit(),
        uses_cloud_api=True,
        cost_note="платный API Google",
        recommend_note="экономно: до ~10–15 запросов в день",
    ),
    AI_PROVIDER_AUTO: ProviderQuota(
        key=AI_PROVIDER_AUTO,
        label="Auto",
        daily_limit=cloud_daily_limit(),
        uses_cloud_api=True,
        cost_note="сначала Gemini/Gemma, при сбое — Local",
        recommend_note="до ~15–20 облачных запросов в день",
    ),
}


def uses_cloud_api(provider: str | None) -> bool:
    return PROVIDER_QUOTAS[normalize_provider(provider)].uses_cloud_api


def get_provider_quota(provider: str | None) -> ProviderQuota:
    return PROVIDER_QUOTAS[normalize_provider(provider)]


def _limit_text(used: int, limit: int) -> str:
    if limit <= 0:
        return f"{used}/∞"
    return f"{used}/{limit}"


def get_usage_snapshot(user_id: int) -> dict[str, int]:
    return get_ai_daily_usage(user_id)


def check_ai_allowed(user_id: int, provider: str | None, *, is_admin: bool = False) -> tuple[bool, str | None]:
    if is_admin:
        return True, None

    provider = normalize_provider(provider)
    quota = get_provider_quota(provider)
    usage = get_usage_snapshot(user_id)

    if quota.uses_cloud_api:
        used = usage["cloud_requests"]
        limit = cloud_daily_limit()
        if limit > 0 and used >= limit:
            return False, (
                f"⚠️ Дневной лимит облачного AI исчерпан ({used}/{limit}).\n\n"
                "Gemini и Gemma расходуют платный/квотный API Google.\n"
                "Переключитесь на <b>Local</b> — ответы из базы канала, без API.\n"
                "Лимит обновится после полуночи UTC."
            )
        return True, None

    used = usage["local_requests"]
    limit = local_daily_limit()
    if limit > 0 and used >= limit:
        return False, (
            f"⚠️ Дневной лимит Local исчерпан ({used}/{limit}).\n"
            "Попробуйте завтра или выберите Gemma/Gemini (облако)."
        )
    return True, None


def record_ai_usage(user_id: int, provider: str | None) -> None:
    provider = normalize_provider(provider)
    if uses_cloud_api(provider):
        increment_ai_daily_usage(user_id, cloud=True)
    else:
        increment_ai_daily_usage(user_id, cloud=False)


def format_models_guide() -> str:
    cloud_lim = cloud_daily_limit()
    local_lim = local_daily_limit()
    local_lim_s = "без лимита" if local_lim <= 0 else f"до {local_lim}/день"

    return (
        "<b>Модели и лимиты</b>\n"
        f"• <b>Local</b> — {local_lim_s}, {PROVIDER_QUOTAS[AI_PROVIDER_LOCAL].cost_note}\n"
        f"• <b>Gemma</b> — облако {cloud_lim}/день, {PROVIDER_QUOTAS[AI_PROVIDER_GEMMA].cost_note}\n"
        f"• <b>Gemini</b> — облако {cloud_lim}/день, {PROVIDER_QUOTAS[AI_PROVIDER_GEMINI].cost_note}\n"
        f"• <b>Auto</b> — облако {cloud_lim}/день, при ошибке → Local\n\n"
        "<i>Рекомендация: обычные вопросы — Local; Gemini — только когда нужен «умный» ответ.</i>"
    )


def format_usage_status(user_id: int, current_provider: str | None) -> str:
    usage = get_usage_snapshot(user_id)
    cloud_lim = cloud_daily_limit()
    local_lim = local_daily_limit()
    provider = normalize_provider(current_provider)
    quota = get_provider_quota(provider)

    lines = [
        "<b>Сегодня (ваши запросы):</b>",
        f"🌐 Облако (Auto/Gemini/Gemma): {_limit_text(usage['cloud_requests'], cloud_lim)}",
    ]
    if local_lim > 0:
        lines.append(f"📚 Local: {_limit_text(usage['local_requests'], local_lim)}")
    else:
        lines.append(f"📚 Local: {usage['local_requests']} (без лимита)")

    lines.append(f"\n<b>Текущий режим:</b> {AI_PROVIDER_LABELS.get(provider, provider)}")
    lines.append(f"<i>{quota.recommend_note.capitalize()}.</i>")
    return "\n".join(lines)

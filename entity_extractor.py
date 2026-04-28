"""
Entity Extractor — распознаёт сущности из произвольного текста.

Поддерживает:
  Person, Username, Phone, Email, City, Country, Address,
  Domain, Website, Telegram, Social, Photo, Coordinates,
  Date, Organization, Vehicle, IP, Hash, Document
"""

import re
from dataclasses import dataclass, field
from typing import Optional


# ─── Entity definition ────────────────────────────────────────────────────────

ENTITY_META = {
    "Person":       {"color": "#7EB8F7", "shape": "circle",    "icon": "👤"},
    "Username":     {"color": "#A78BFA", "shape": "ellipse",   "icon": "🔷"},
    "Phone":        {"color": "#34D399", "shape": "box",       "icon": "📞"},
    "Email":        {"color": "#F87171", "shape": "database",  "icon": "📧"},
    "City":         {"color": "#FBBF24", "shape": "triangle",  "icon": "🏙️"},
    "Country":      {"color": "#FB923C", "shape": "triangle",  "icon": "🌍"},
    "Address":      {"color": "#E879F9", "shape": "diamond",   "icon": "📍"},
    "Domain":       {"color": "#60A5FA", "shape": "square",    "icon": "🌐"},
    "Website":      {"color": "#38BDF8", "shape": "square",    "icon": "🔗"},
    "Telegram":     {"color": "#2EAADC", "shape": "hexagon",   "icon": "✈️"},
    "Social":       {"color": "#818CF8", "shape": "ellipse",   "icon": "📱"},
    "Photo":        {"color": "#F472B6", "shape": "image",     "icon": "🖼️"},
    "Coordinates":  {"color": "#4ADE80", "shape": "dot",       "icon": "📡"},
    "Date":         {"color": "#94A3B8", "shape": "box",       "icon": "📅"},
    "Organization": {"color": "#FB923C", "shape": "star",      "icon": "🏢"},
    "Vehicle":      {"color": "#A3E635", "shape": "square",    "icon": "🚗"},
    "IP":           {"color": "#F9A8D4", "shape": "database",  "icon": "🖧"},
    "Hash":         {"color": "#6EE7B7", "shape": "box",       "icon": "#️⃣"},
    "Document":     {"color": "#FDE68A", "shape": "box",       "icon": "📄"},
}


@dataclass
class Entity:
    type: str
    value: str
    confidence: float = 1.0
    source: str = "regex"
    meta: dict = field(default_factory=dict)

    def node_id(self) -> str:
        return f"{self.type}::{self.value}"

    def display(self) -> str:
        icon = ENTITY_META.get(self.type, {}).get("icon", "•")
        return f"{icon} {self.value}"


# ─── Regex patterns ───────────────────────────────────────────────────────────

_PHONE_RE = re.compile(
    r"(?<!\d)(\+?\d[\d\s\-().]{6,14}\d)(?!\d)"
)
_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)
_URL_RE = re.compile(
    r"https?://[^\s\"'<>]+"
)
_DOMAIN_RE = re.compile(
    r"\b(?:[a-zA-Z0-9\-]+\.)+(?:com|net|org|ru|io|co|me|info|biz|xyz|app|dev|ai)\b"
)
_TG_RE = re.compile(
    r"(?:t\.me/|telegram\.me/|(?<![\w.%+\-])@)([a-zA-Z][a-zA-Z0-9_]{3,31})"
)
_IP_RE = re.compile(
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b"
)
_HASH_RE = re.compile(
    r"\b[0-9a-fA-F]{32,64}\b"
)
_COORDS_RE = re.compile(
    r"(-?\d{1,3}\.\d{3,})\s*[,;]\s*(-?\d{1,3}\.\d{3,})"
)
_DATE_RE = re.compile(
    r"\b(\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}|\d{4}[./\-]\d{1,2}[./\-]\d{1,2})\b"
)
_VEHICLE_RU_RE = re.compile(
    r"\b(?:"
    r"[АВЕКМНОРСТУХABEKMHOPCTYX]{1}\d{3}[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\d{2,3}"
    r"|"
    r"[A-ZА-ЯЁ]{2}\d{4}[A-ZА-ЯЁ]{2}"
    r")\b",
    re.IGNORECASE
)
_USERNAME_RE = re.compile(
    r"(?<![\w.%+\-])@([a-zA-Z][a-zA-Z0-9_.]{2,30})(?![a-zA-Z0-9_.])"
)
_PERSON_RE = re.compile(
    r"\b([A-ZА-ЯЁ][a-zа-яё]{1,30}(?:[-'][A-ZА-ЯЁ]?[a-zа-яё]{1,30})?"
    r"(?:\s+[A-ZА-ЯЁ][a-zа-яё]{1,30}(?:[-'][A-ZА-ЯЁ]?[a-zа-яё]{1,30})?){1,2})\b"
)

# Social network patterns
_SOCIAL_RE = re.compile(
    r"(?:instagram\.com|vk\.com|twitter\.com|x\.com|facebook\.com|"
    r"tiktok\.com|youtube\.com|linkedin\.com)/([^\s/\"'<>?&]{2,50})",
    re.IGNORECASE
)

# Russian cities (short list, extend as needed)
_RU_CITIES = {
    "москва", "санкт-петербург", "питер", "спб", "новосибирск", "екатеринбург",
    "казань", "нижний новгород", "челябинск", "омск", "самара", "уфа",
    "красноярск", "ростов-на-дону", "воронеж", "пермь", "волгоград", "краснодар",
    "саратов", "тюмень", "тольятти", "ижевск", "барнаул", "иркутск",
    "ульяновск", "хабаровск", "ярославль", "владивосток", "махачкала", "томск",
    # UA
    "киев", "харьков", "одесса", "днепр", "запорожье", "львов",
    # BY
    "минск", "гомель", "брест",
    # International
    "london", "new york", "berlin", "paris", "dubai", "istanbul",
}

_PERSON_STOPWORDS = {
    "и", "или", "но", "для", "его", "ее", "её", "твой", "твою", "мой", "моя",
    "как", "что", "где", "кто", "это", "этот", "эта", "тут", "там", "меня",
    "тебя", "нас", "вас", "дорогие", "друзья", "итак", "сейчас", "первый",
    "второй", "новое", "расследование", "проверь", "проверьте", "найди", "найдите",
}

_LABELED_FIELD_ALIASES = {
    "никнейм": "Username",
    "username": "Username",
    "логин": "Username",
    "имя": "FirstName",
    "фамилия": "LastName",
    "отчество": "MiddleName",
    "telegram": "Telegram",
    "телеграм": "Telegram",
    "почта": "Email",
    "email": "Email",
    "e-mail": "Email",
    "телефон": "Phone",
    "номер": "Phone",
    "город": "City",
    "адрес": "Address",
    "координаты": "Coordinates",
    "ip": "IP",
    "ip-адрес": "IP",
    "хэш": "Hash",
    "hash": "Hash",
    "номер авто": "Vehicle",
    "авто": "Vehicle",
    "машина": "Vehicle",
}


def _looks_like_person_name(candidate: str, known_cities: set[str]) -> bool:
    parts = [part.strip(" .,!?:;\"'()[]{}") for part in candidate.split()]
    if len(parts) < 2 or len(parts) > 3:
        return False

    normalized_parts = [part.lower() for part in parts]
    if any(part in _PERSON_STOPWORDS for part in normalized_parts):
        return False

    if candidate.lower() in known_cities:
        return False

    if any(len(part) < 2 for part in parts):
        return False

    return True


def _normalize_labeled_value(entity_type: str, value: str) -> str:
    normalized = value.strip(" \t\n\r-–—•")
    if entity_type in {"Username", "Telegram"} and normalized and not normalized.startswith("@"):
        normalized = f"@{normalized}"
    return normalized


def _extract_labeled_fields(text: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    named_parts: dict[str, str] = {}
    entities: list[tuple[str, str]] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = line.lstrip("•*- ").strip()
        if ":" not in line:
            continue

        label, value = line.split(":", 1)
        field_key = _LABELED_FIELD_ALIASES.get(label.strip().lower())
        normalized_value = value.strip()
        if not field_key or not normalized_value:
            continue

        if field_key in {"FirstName", "LastName", "MiddleName"}:
            named_parts[field_key] = normalized_value
            continue

        entities.append((field_key, _normalize_labeled_value(field_key, normalized_value)))

    return named_parts, entities


def extract_entities(text: str) -> list[Entity]:
    """Extract all recognizable entities from free text."""
    found: list[Entity] = []
    seen: set[str] = set()

    def add(e: Entity):
        key = e.node_id()
        if key not in seen:
            seen.add(key)
            found.append(e)

    named_parts, labeled_entities = _extract_labeled_fields(text)
    for entity_type, value in labeled_entities:
        add(Entity(entity_type, value, 0.96, source="labeled"))

    full_name_parts = [
        named_parts.get("FirstName", "").strip(),
        named_parts.get("MiddleName", "").strip(),
        named_parts.get("LastName", "").strip(),
    ]
    full_name = " ".join(part for part in full_name_parts if part)
    if full_name and len(full_name.split()) >= 2:
        add(Entity("Person", full_name, 0.98, source="labeled"))

    # Telegram handles first (before generic @username)
    for m in _TG_RE.finditer(text):
        add(Entity("Telegram", f"@{m.group(1)}", 0.95))

    # Social network links
    for m in _SOCIAL_RE.finditer(text):
        full = m.group(0)
        add(Entity("Social", full, 0.95))
        # Also extract the URL
        add(Entity("Website", f"https://{full}", 0.85))

    # URLs
    for m in _URL_RE.finditer(text):
        url = m.group(0)
        if "t.me/" in url or "telegram.me/" in url:
            continue  # already caught
        if any(s in url for s in ["instagram.com", "vk.com", "twitter.com",
                                   "facebook.com", "tiktok.com", "youtube.com"]):
            continue  # social already caught
        add(Entity("Website", url, 0.9))

    # Emails
    for m in _EMAIL_RE.finditer(text):
        add(Entity("Email", m.group(0).lower(), 1.0))

    # Phones
    for m in _PHONE_RE.finditer(text):
        raw = m.group(1).strip()
        if "." in raw:
            continue
        digits = re.sub(r"\D", "", raw)
        if 7 <= len(digits) <= 15:
            add(Entity("Phone", raw, 0.9))

    # IPs
    for m in _IP_RE.finditer(text):
        parts = [int(x) for x in m.group(0).split(".")]
        if all(0 <= p <= 255 for p in parts):
            add(Entity("IP", m.group(0), 1.0))

    # Hashes
    for m in _HASH_RE.finditer(text):
        # avoid matching phone numbers or IDs
        val = m.group(0)
        if len(val) in (32, 40, 64):
            add(Entity("Hash", val, 0.8))

    # Coordinates
    for m in _COORDS_RE.finditer(text):
        lat, lon = float(m.group(1)), float(m.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            add(Entity("Coordinates", f"{lat}, {lon}", 1.0))

    # Domains (standalone, not already caught as URL)
    for m in _DOMAIN_RE.finditer(text):
        dom = m.group(0).lower()
        if not any(e.value.endswith(dom) for e in found if e.type in ("Website", "Email")):
            add(Entity("Domain", dom, 0.75))

    # Dates
    for m in _DATE_RE.finditer(text):
        add(Entity("Date", m.group(1), 0.85))

    # Vehicle plates (RU)
    for m in _VEHICLE_RU_RE.finditer(text):
        add(Entity("Vehicle", m.group(0).upper(), 0.9))

    # Usernames (@handle) not yet matched as Telegram
    for m in _USERNAME_RE.finditer(text):
        handle = f"@{m.group(1)}"
        if not any(e.value == handle for e in found):
            add(Entity("Username", handle, 0.7))

    # Person names: 2-3 capitalized words, e.g. "Иван Иванов" or "John Smith"
    known_cities = {city.lower() for city in _RU_CITIES}
    for m in _PERSON_RE.finditer(text):
        person = m.group(1).strip()
        if _looks_like_person_name(person, known_cities):
            add(Entity("Person", person, 0.72))

    # Cities
    text_lower = text.lower()
    for city in _RU_CITIES:
        pattern = re.compile(r"\b" + re.escape(city) + r"\b", re.IGNORECASE)
        if pattern.search(text_lower):
            add(Entity("City", city.capitalize(), 0.65))

    return found


# ─── Relation analysis ────────────────────────────────────────────────────────

@dataclass
class Link:
    a: str   # node_id
    b: str   # node_id
    link_type: str
    confidence: float
    explanation: str


def analyze_relations(entities: list[Entity]) -> list[Link]:
    """Detect logical links between extracted entities."""
    links: list[Link] = []

    def username_base(val: str) -> str:
        """Strip @, digits, underscores to get root."""
        v = val.lstrip("@").lower()
        return re.sub(r"[\d_.\-]", "", v)

    def add(a, b, lt, conf, expl):
        if a.node_id() != b.node_id():
            links.append(Link(a.node_id(), b.node_id(), lt, conf, expl))

    usernames = [e for e in entities if e.type in ("Username", "Telegram", "Social")]
    emails    = [e for e in entities if e.type == "Email"]
    phones    = [e for e in entities if e.type == "Phone"]
    domains   = [e for e in entities if e.type in ("Domain", "Website")]
    persons   = [e for e in entities if e.type == "Person"]
    profileish = [e for e in entities if e.type in ("Username", "Telegram", "Social", "Email", "Phone")]
    locationish = [e for e in entities if e.type in ("City", "Address", "Coordinates")]
    vehicles = [e for e in entities if e.type == "Vehicle"]

    # Same username base across platforms
    for i, a in enumerate(usernames):
        for b in usernames[i+1:]:
            ba, bb = username_base(a.value), username_base(b.value)
            if ba and bb and ba == bb:
                add(a, b, "Совпадение username", 0.92,
                    f"Одинаковая основа '{ba}' в разных сервисах")
            elif ba and bb and (ba in bb or bb in ba) and len(ba) >= 4:
                conf = 0.65 + 0.1 * (min(len(ba), len(bb)) / max(len(ba), len(bb)))
                add(a, b, "Похожий username", min(conf, 0.84),
                    f"Схожие никнеймы: '{a.value}' и '{b.value}'")

    # Email username matches handle
    for em in emails:
        local = em.value.split("@")[0].lower()
        local_base = re.sub(r"[\d_.\-]", "", local)
        for un in usernames:
            ub = username_base(un.value)
            if ub and local_base and (local_base == ub or local_base in ub or ub in local_base):
                conf = 0.88 if local_base == ub else 0.62
                add(em, un, "Email ↔ Username", conf,
                    f"Локальная часть почты '{local}' совпадает с ником '{un.value}'")

    # Email domain matches domain/website
    for em in emails:
        dom = em.value.split("@")[1].lower()
        for d in domains:
            if dom in d.value or d.value in dom:
                add(em, d, "Email → Домен", 1.0,
                    f"Почта принадлежит домену {d.value}")

    # Phone linked to Telegram (heuristic — same account)
    for ph in phones:
        for tg in [e for e in entities if e.type == "Telegram"]:
            add(ph, tg, "Телефон → Telegram", 0.72,
                "Telegram-аккаунт регистрируется на номер телефона")

    # Person linked to profile/contact entities from the same input blob
    for person in persons:
        for item in profileish:
            add(person, item, "Персона → Контакт", 0.76,
                f"Контакт '{item.value}' указан рядом с персоной '{person.value}'")
        for item in locationish:
            add(person, item, "Персона → Локация", 0.68,
                f"Локация '{item.value}' указана рядом с персоной '{person.value}'")
        for item in vehicles:
            add(person, item, "Персона → Транспорт", 0.66,
                f"Транспорт '{item.value}' указан рядом с персоной '{person.value}'")

    # Multiple emails sharing same surname pattern
    if len(emails) >= 2:
        for i, ea in enumerate(emails):
            for eb in emails[i+1:]:
                la, lb = ea.value.split("@")[0], eb.value.split("@")[0]
                if la[:4].lower() == lb[:4].lower() and la != lb:
                    add(ea, eb, "Возможная связь по фамилии", 0.58,
                        f"Похожие локальные части: '{la}' и '{lb}'")

    return links

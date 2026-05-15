import os
from dataclasses import dataclass
from dotenv import load_dotenv

# Загружаем переменные из файла .env
load_dotenv()

@dataclass
class Config:
    # Telegram
    # Теперь бот сначала ищет токен в .env, и только если его нет — берет заглушку
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
    
    # ID канала (теперь приоритет на числовое значение из .env)
    CHANNEL_ID: str = os.getenv("CHANNEL_ID", "@ShkoloDrive")
    CHANNEL_INVITE: str = os.getenv("CHANNEL_INVITE", "https://t.me/+OI5UGXchMRg1NDY6")
    
    # Список ID администраторов (инициализируем как пустой список)
    ADMIN_IDS: list = None

    # AI
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
    
    # Рабочая модель Gemini по умолчанию
    AI_MODEL: str = os.getenv("AI_MODEL", "models/gemini-2.5-flash")

    # Дневные лимиты AI на пользователя (0 = без лимита для Local)
    AI_DAILY_CLOUD_LIMIT: str = os.getenv("AI_DAILY_CLOUD_LIMIT", "20")
    AI_DAILY_LOCAL_LIMIT: str = os.getenv("AI_DAILY_LOCAL_LIMIT", "100")

    # Database (SQLite file path; do not use Railway Postgres DATABASE_URL here)
    DB_PATH: str = os.getenv("DB_PATH", os.getenv("SQLITE_PATH", "overnetting.db"))

    # Local knowledge base (Telegram export)
    # Путь к файлу messages.html (экспорт чата/канала Telegram в HTML).
    # Если задан и файл существует — он будет импортирован в channel_posts.
    MESSAGES_HTML_PATH: str = os.getenv("MESSAGES_HTML_PATH", "")

    # Для формирования ссылок на сообщения: https://t.me/<username>/<message_id>
    # Можно оставить пустым — тогда бот будет показывать только message_id.
    CHANNEL_PUBLIC_USERNAME: str = os.getenv("CHANNEL_PUBLIC_USERNAME", "").lstrip("@")

    # Graph
    GRAPH_OUTPUT_DIR: str = os.getenv("GRAPH_OUTPUT_DIR", "graphs")
    GRAPH_WIDTH: str = "100%"
    GRAPH_HEIGHT: str = "750px"

    # Confidence thresholds
    STRONG_LINK_THRESHOLD: float = 0.80
    MEDIUM_LINK_THRESHOLD: float = 0.55

    def __post_init__(self):
        # Обработка ADMIN_IDS из строки (например, "123,456") в список [123, 456]
        if self.ADMIN_IDS is None:
            raw = os.getenv("ADMIN_IDS", "")
            if raw:
                self.ADMIN_IDS = [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]
            else:
                self.ADMIN_IDS = []
        
        # Создаем папку для графов, если её нет
        os.makedirs(self.GRAPH_OUTPUT_DIR, exist_ok=True)

config = Config()

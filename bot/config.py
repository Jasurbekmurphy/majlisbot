import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    webapp_url: str
    web_host: str
    web_port: int
    db_path: str
    tz: ZoneInfo
    max_gps_accuracy: float  # metr: bundan yomon aniqlikdagi joylashuv qabul qilinmaydi
    init_data_ttl: int  # soniya: Mini App ochilgandan keyin shuncha vaqt ichida skanerlash kerak


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN .env faylida ko'rsatilmagan")

    admin_ids = frozenset(
        int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x
    )
    if not admin_ids:
        raise SystemExit("ADMIN_IDS .env faylida ko'rsatilmagan (masalan: ADMIN_IDS=123456789)")

    webapp_url = os.getenv("WEBAPP_URL", "").strip()
    if not webapp_url.startswith("https://"):
        raise SystemExit("WEBAPP_URL https:// bilan boshlanishi kerak (masalan: https://davomat.example.uz/app)")

    return Config(
        bot_token=token,
        admin_ids=admin_ids,
        webapp_url=webapp_url,
        web_host=os.getenv("WEB_HOST", "127.0.0.1"),
        web_port=int(os.getenv("WEB_PORT", "8080")),
        db_path=os.getenv("DB_PATH", "davomat.db"),
        tz=ZoneInfo(os.getenv("TIMEZONE", "Asia/Tashkent")),
        max_gps_accuracy=float(os.getenv("MAX_GPS_ACCURACY", "300")),
        init_data_ttl=int(os.getenv("INIT_DATA_TTL", "3600")),
    )

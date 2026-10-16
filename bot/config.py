import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


@dataclass
class Config:
    bot_token: str
    admin_ids: set[int] = field(default_factory=set)
    db_path: str = "data/bot.sqlite3"
    default_tz: int = 3
    trial_days: int = 15
    price_rub: int = 299
    sub_days: int = 30
    payment_provider_token: str = ""
    subscription_enabled: bool = True


def _int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def load_config() -> Config:
    load_dotenv()
    admin_ids = {
        int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x
    }
    return Config(
        bot_token=os.getenv("BOT_TOKEN", "").strip(),
        admin_ids=admin_ids,
        db_path=os.getenv("DB_PATH", "data/bot.sqlite3").strip(),
        default_tz=_int("DEFAULT_TZ", 3),
        trial_days=_int("TRIAL_DAYS", 15),
        price_rub=_int("PRICE_RUB", 299),
        sub_days=_int("SUB_DAYS", 30),
        payment_provider_token=os.getenv("PAYMENT_PROVIDER_TOKEN", "").strip(),
        subscription_enabled=os.getenv("SUBSCRIPTION", "on").strip().lower() not in ("off", "0", "no", "false"),
    )

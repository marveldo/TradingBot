from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from datetime import datetime , timezone

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    ACCOUNT_LOGIN_ID: int
    ACCOUNT_PASSWORD: str
    ACCOUNT_SERVER: str
    ACCOUNT_BROKER: str

    BASE_DIR: Path = BASE_DIR
    TIMEFRAMES: list[str] = ["W1","D1", "H4", "H1", "M15"]
    SYMBOLS: list[str] = ["EURUSD", "XAUUSD", "BTCUSD"]
    START_DATE: datetime = datetime(2019,1,1 , tzinfo=timezone.utc)
    END_DATE: datetime = datetime.now(timezone.utc)


settings = Settings()

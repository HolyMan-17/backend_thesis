import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))


class Settings(BaseSettings):
    DB_USER: str
    DB_PASSWORD: str
    DB_HOST: str = "127.0.0.1"
    DB_PORT: str = "3306"
    DB_NAME: str

    MQTT_USER: str
    MQTT_PASS: str
    MQTT_HOST: str = "127.0.0.1"
    MQTT_PORT: int = 1883

    AUTH0_DOMAIN: str = "thesisbroker.us.auth0.com"
    AUTH0_AUDIENCE: str = "https://api.thesisbroker.com"
    AUTH0_ISSUER: str = "https://thesisbroker.us.auth0.com/"
    AUTH0_JWKS_URI: str = "https://thesisbroker.us.auth0.com/.well-known/jwks.json"
    BACKEND_SYNC_SECRET: str = ""

    RECOMMENDATION_SCAN_INTERVAL: int = 60
    RECOMMENDATION_SUSTAINED_RISKY_MIN: int = 5
    RECOMMENDATION_OSCILLATION_WINDOW_MIN: int = 30
    RECOMMENDATION_OSCILLATION_THRESHOLD: int = 5
    RECOMMENDATION_RECOVERY_SAFE_MIN: int = 5
    RECOMMENDATION_VOLTAGE_BROWNOUT: float = 105.0
    RECOMMENDATION_VOLTAGE_SAG_COUNT: int = 3
    RECOMMENDATION_RECOVERY_LOOKBACK_HOURS: int = 24

    AI_CONTROL_RISKY_THRESHOLD_MIN: int = 2
    AI_CONTROL_GRACE_PERIOD_MIN: int = 5
    AI_CONTROL_OVERRIDE_COOLDOWN_MIN: int = 30

    class Config:
        env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
        env_file_encoding = "utf-8"

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"mysql+aiomysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset=utf8mb4"
        )


settings = Settings()
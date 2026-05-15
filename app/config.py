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

    MQTT_USER: str = "esp-gateway"
    MQTT_PASS: str = "wUbcJJiZcLqV3dDo2r9e"
    MQTT_HOST: str = "127.0.0.1"
    MQTT_PORT: int = 1883

    AUTH0_DOMAIN: str = "thesisbroker.us.auth0.com"
    AUTH0_AUDIENCE: str = "https://api.thesisbroker.com"
    AUTH0_ISSUER: str = "https://thesisbroker.us.auth0.com/"
    AUTH0_JWKS_URI: str = "https://thesisbroker.us.auth0.com/.well-known/jwks.json"
    BACKEND_SYNC_SECRET: str = ""

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
import hashlib
import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./medi_nudge.db"
    JWT_SECRET_KEY: str = "changeme-replace-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 360
    OPENAI_API_KEY: str = ""
    LLM_BASE_URL: str = ""
    LLM_MODEL: str = "gpt-4o"
    ELEVENLABS_API_KEY: str = ""
    ELEVENLABS_DEFAULT_VOICE_FEMALE: str = ""  # e.g. Rachel voice ID
    ELEVENLABS_DEFAULT_VOICE_MALE: str = ""    # e.g. Antoni voice ID
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_BOT_USERNAME: str = ""  # e.g. MediNudgeBot (without @)
    TELEGRAM_WEBHOOK_SECRET: str = ""
    WARNING_DAYS: int = 3
    ESCALATION_DAYS: int = 14
    MAX_NUDGE_ATTEMPTS: int = 3
    MISSED_DOSE_ESCALATION_THRESHOLD: int = 3  # consecutive misses before caregiver notified
    MEDIA_STORAGE_PATH: str = "./media"

    # AWS — optional; when set, enables S3 image storage and ECS-compatible config
    AWS_S3_BUCKET_NAME: str = ""
    AWS_REGION: str = "ap-southeast-1"

    # CORS — comma-separated list of allowed origins
    ALLOWED_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # Scheduler — set to False on API tasks; scheduler runs in a dedicated ECS task
    SCHEDULER_ENABLED: bool = True

    # Twilio — optional; enables SMS/WhatsApp caregiver invite delivery
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM_NUMBER: str = ""  # E.164, e.g. +6512345678 or whatsapp:+14155238886

    # Apple Push Notification service — injected from AWS Secrets Manager in ECS
    APNS_PRIVATE_KEY: str = ""
    APNS_KEY_ID: str = ""
    APNS_TEAM_ID: str = ""
    APNS_TOPIC: str = ""
    APNS_ENVIRONMENT: str = "production"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()


def hash_sha256(value: str) -> str:
    """Hash a sensitive string with SHA-256. Never store the original."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

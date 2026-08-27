"""
Central application settings, loaded from environment variables.
Works locally (via .env) and on Vercel (via project env vars).
"""
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Core
    APP_NAME: str = "ProfSchedule AI"

    # --- Android (Trusted Web Activity) ---
    # The package name of the Play listing, and the SHA-256 fingerprints of the
    # certificates that sign it. Both are needed for /.well-known/assetlinks.json,
    # which is how Chrome decides the Android app may open this site without a
    # URL bar. Get the fingerprints from the Play Console (App integrity ->
    # App signing) -- there are usually two: the upload key and Play's own.
    # Comma-separated, colon-separated hex, e.g. "AA:BB:...:FF,11:22:...:99".
    ANDROID_PACKAGE_NAME: str = ""
    ANDROID_SHA256_FINGERPRINTS: str = ""
    ENV: str = "development"
    SECRET_KEY: str = "dev-secret-change-me"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    JWT_ALGORITHM: str = "HS256"

    # Database
    DATABASE_URL: str = "sqlite:///./profschedule.db"

    # AI provider (provider-agnostic; any OpenAI-compatible endpoint works)
    AI_PROVIDER: str = "openai"  # openai | nvidia | ollama | none
    AI_API_KEY: Optional[str] = None
    AI_MODEL: str = "gpt-4o-mini"
    AI_BASE_URL: Optional[str] = None  # override for OpenAI-compatible endpoints

    # Reminders / cron
    CRON_SECRET: Optional[str] = None  # shared secret to protect the cron endpoint

    # On persistent hosts (Render, Railway, a VM) deliver reminders from an
    # in-process loop. Leave off for serverless (Vercel), where background
    # tasks don't survive between invocations — use Vercel Cron there.
    ENABLE_BACKGROUND_SCHEDULER: bool = False
    REMINDER_POLL_SECONDS: int = 60

    # First-admin bootstrap. Hosts without shell access (Render's free tier,
    # most PaaS free plans) can't run create_admin.py, and there is no
    # self-service way to become an admin — so allow one to be provisioned
    # from the environment at startup. Idempotent: promotes the account if it
    # already exists, creates it if BOOTSTRAP_ADMIN_PASSWORD is also given.
    BOOTSTRAP_ADMIN_EMAIL: Optional[str] = None
    BOOTSTRAP_ADMIN_PASSWORD: Optional[str] = None

    # Email (optional, for email reminders)
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM: Optional[str] = None

    # Web Push (VAPID). Generate a keypair with:  python generate_vapid_keys.py
    VAPID_PUBLIC_KEY: Optional[str] = None
    VAPID_PRIVATE_KEY: Optional[str] = None
    VAPID_CONTACT_EMAIL: Optional[str] = None

    # Absolute base URL, used to build the subscribable calendar feed link
    # and the Google OAuth redirect.
    PUBLIC_BASE_URL: Optional[str] = None

    # Google Sign-In + Calendar sync (Google Cloud Console -> Credentials).
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    # Requesting the calendar scope lets us write events into the professor's
    # own Google Calendar, so Google delivers the phone notification.
    GOOGLE_CALENDAR_SYNC: bool = True

    DEFAULT_TIMEZONE: str = "Asia/Kolkata"


@lru_cache
def get_settings() -> Settings:
    return Settings()

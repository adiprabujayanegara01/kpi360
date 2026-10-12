import os
from datetime import timedelta
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _db_url():
    url = os.getenv("DATABASE_URL")
    if not url:
        os.makedirs(os.path.join(BASE_DIR, "instance"), exist_ok=True)
        return "sqlite:///" + os.path.join(BASE_DIR, "instance", "kpi360.db")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    return url


def _bool(name, default="0"):
    return os.getenv(name, default).lower() in ("1", "true", "yes", "on")


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = _db_url()
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE")
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=int(os.getenv("SESSION_MINUTES", "60")))
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", os.path.join(BASE_DIR, "instance", "uploads"))
    REPORT_FOLDER = os.getenv("REPORT_FOLDER", os.path.join(BASE_DIR, "instance", "reports"))
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024
    ALLOWED_UPLOAD_EXT = {"pdf", "png", "jpg", "jpeg", "xlsx", "csv"}
    REGISTRATION_ENABLED = _bool("REGISTRATION_ENABLED")
    MAX_FAILED_LOGINS = 5
    LOCKOUT_MINUTES = 15
    HSTS = _bool("SESSION_COOKIE_SECURE")
    # AI
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "none").lower()
    LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-5-5")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    LLM_ANONYMIZE = _bool("LLM_ANONYMIZE", "1")
    LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "40"))
    PROMPT_VERSION = "insight-v1"
    # Email
    MAIL_SERVER = os.getenv("MAIL_SERVER", "")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_SENDER = os.getenv("MAIL_SENDER", "kpi360@example.com")
    REPORTS_SYNC = False
    TESTING = False


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    WTF_CSRF_ENABLED = False
    REPORTS_SYNC = True
    LLM_PROVIDER = "none"
    SECRET_KEY = "test-secret"

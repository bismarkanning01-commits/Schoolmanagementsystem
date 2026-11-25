import os


class Config:
    APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()

    SECRET_KEY = os.environ.get("SECRET_KEY")
    if not SECRET_KEY:
        if APP_ENV == "production":
            raise RuntimeError("SECRET_KEY must be configured in production.")
        SECRET_KEY = "local-development-only-change-before-deploying"

    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    if not SQLALCHEMY_DATABASE_URI:
        if APP_ENV == "production":
            raise RuntimeError("DATABASE_URL must be configured in production.")
        SQLALCHEMY_DATABASE_URI = "sqlite:///school.db"

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_SECURE = APP_ENV == "production"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

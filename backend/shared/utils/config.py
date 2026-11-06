"""Lexora HRM — shared configuration (env-driven, 12-factor)."""
import os


class Settings:
    # Identity / security
    JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-insecure-key-change-me")
    JWT_ALG = "HS256"
    ACCESS_EXPIRE_MIN = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    REFRESH_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

    # Data
    DATABASE_URL = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@postgres:5432/lexora"
    )
    REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

    # Pool caps (per-service; keeps a single pod from saturating shared Postgres)
    POOL_SIZE = int(os.getenv("SQLALCHEMY_POOL_SIZE", "5"))
    MAX_OVERFLOW = int(os.getenv("SQLALCHEMY_MAX_OVERFLOW", "3"))
    POOL_TIMEOUT = int(os.getenv("SQLALCHEMY_POOL_TIMEOUT", "10"))
    POOL_RECYCLE = int(os.getenv("SQLALCHEMY_POOL_RECYCLE", "1800"))

    # Internal service URLs (all containers listen on :8000, distinguished by host)
    AUTH_URL = os.getenv("AUTH_SERVICE_URL", "http://auth-service:8000")
    EMPLOYEE_URL = os.getenv("EMPLOYEE_SERVICE_URL", "http://employee-service:8000")
    ONBOARDING_URL = os.getenv("ONBOARDING_SERVICE_URL", "http://onboarding-service:8000")
    PAYROLL_URL = os.getenv("PAYROLL_SERVICE_URL", "http://payroll-service:8000")
    TIME_URL = os.getenv("TIME_SERVICE_URL", "http://time-attendance-service:8000")
    LEAVE_URL = os.getenv("LEAVE_SERVICE_URL", "http://leave-service:8000")
    APPROVALS_URL = os.getenv("APPROVALS_SERVICE_URL", "http://approvals-service:8000")
    NOTIFICATION_URL = os.getenv("NOTIFICATION_SERVICE_URL", "http://notification-service:8000")
    AI_PLATFORM_URL = os.getenv("AI_PLATFORM_SERVICE_URL", "http://ai-platform-service:8000")
    OPS_URL = os.getenv("OPS_SERVICE_URL", "http://ops-service:8000")
    COMPLIANCE_URL = os.getenv("COMPLIANCE_SERVICE_URL", "http://compliance-service:8000")

    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3200").split(",")


settings = Settings()

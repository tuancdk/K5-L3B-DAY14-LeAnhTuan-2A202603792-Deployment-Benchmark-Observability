"""
Config — 12-Factor: mọi cấu hình đọc từ biến môi trường.

  • Không có secret trong code: AGENT_API_KEY mặc định rỗng → auth từ chối mọi request
    (fail closed), không có "key mặc định" nào để đoán.
  • Giá trị được đọc khi tạo Settings (lúc import), nên đổi env phải restart process —
    đúng với cách platform (Railway/Render/Docker) inject env khi khởi động container.
"""
import os
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    # Server
    port: int = field(default_factory=lambda: _env_int("PORT", 8000))
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())

    # Security
    agent_api_key: str = field(default_factory=lambda: os.getenv("AGENT_API_KEY", ""))

    # Redis — state dùng chung (rate limit, cost) cho mọi worker / instance
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))

    # Rate limit & cost guard
    rate_limit_per_minute: int = field(default_factory=lambda: _env_int("RATE_LIMIT_PER_MINUTE", 10))
    daily_budget_usd: float = field(default_factory=lambda: _env_float("DAILY_BUDGET_USD", 1.0))


settings = Settings()

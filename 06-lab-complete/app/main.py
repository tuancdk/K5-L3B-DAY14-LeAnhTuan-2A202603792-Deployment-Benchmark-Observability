"""
Production AI Agent — Part 6 Final Project.

Luồng của POST /ask:
    verify_api_key (401) → check_rate_limit (429) → check_budget (402)
    → gọi LLM → record_cost → trả kết quả

Chạy:  uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2
Kiểm tra: python check_production_ready.py
"""
import json
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import ask as llm_ask


# ─────────────────────────────────────────────────────────
# Structured logging (tiêu chí 10) — mỗi dòng log là 1 JSON object
#
# Logger "agent" có handler riêng và propagate=False → không phụ thuộc việc ai đó
# (uvicorn, thư viện khác) đã cấu hình root logger trước hay chưa.
# json.dumps tự escape dấu " và xuống dòng → luôn ra JSON hợp lệ, kể cả traceback.
# ─────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def _setup_logger() -> logging.Logger:
    log = logging.getLogger("agent")
    log.setLevel(settings.log_level)
    log.propagate = False
    if not log.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        log.addHandler(handler)
    return log


logger = _setup_logger()


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": fields})


# ─────────────────────────────────────────────────────────
# Lifespan — startup / graceful shutdown (tiêu chí 11)
#
# Khi nhận SIGTERM (docker stop, platform redeploy), uvicorn:
#   1. ngừng nhận kết nối mới
#   2. chờ các request đang chạy xong
#   3. chạy phần sau `yield` bên dưới (dọn dẹp) rồi thoát với exit code 0
# Không tự đăng ký signal handler → không bao giờ "nuốt" mất handler của uvicorn.
# ─────────────────────────────────────────────────────────
START_TIME = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_event("startup", environment=settings.environment, port=settings.port,
              rate_limit_per_minute=settings.rate_limit_per_minute,
              daily_budget_usd=settings.daily_budget_usd,
              api_key_configured=bool(settings.agent_api_key))  # không bao giờ log giá trị key
    yield
    log_event("shutdown", uptime_seconds=round(time.time() - START_TIME, 1))
    r.close()  # đóng connection pool Redis


app = FastAPI(title="Production AI Agent", version="1.0.0", lifespan=lifespan,
              docs_url=None if settings.environment == "production" else "/docs")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        if request.url.path not in ("/health", "/ready"):  # bỏ log probe cho đỡ nhiễu
            log_event("request", request_id=request_id, method=request.method,
                      path=request.url.path, status=status,
                      ms=round((time.perf_counter() - start) * 1000, 1))


# ─────────────────────────────────────────────────────────
# Xử lý lỗi — trả mã lỗi rõ ràng, KHÔNG lộ stack trace cho client
# (input sai → 422 do FastAPI/Pydantic tự trả, chỉ gồm tên field và lý do)
# ─────────────────────────────────────────────────────────
@app.exception_handler(redis.RedisError)
async def redis_error_handler(request: Request, exc: redis.RedisError):
    log_event("redis_error", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Service temporarily unavailable"},
                        headers={"Retry-After": "5"})


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.error("unhandled_error", exc_info=exc, extra={"fields": {"path": request.url.path}})
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ─────────────────────────────────────────────────────────
# Health & readiness (tiêu chí 4, 5)
# ─────────────────────────────────────────────────────────
@app.get("/health")
def health():
    """Liveness: process còn chạy là 200 — KHÔNG phụ thuộc Redis (Redis chết không nên
    làm platform restart agent liên tục)."""
    return {"status": "ok", "environment": settings.environment,
            "uptime_seconds": round(time.time() - START_TIME, 1)}


@app.get("/ready")
def ready():
    """Readiness: chỉ nhận traffic khi Redis kết nối được (rate limit / cost cần Redis)."""
    if not redis_healthy():
        raise HTTPException(status_code=503, detail="Not ready: Redis unavailable")
    return {"status": "ready"}


# ─────────────────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=100)

    @field_validator("question")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question must not be blank")
        return v


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)  # ~4 ký tự / token


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    check_rate_limit(api_key)   # 429 khi vượt RATE_LIMIT_PER_MINUTE
    check_budget()              # 402 khi hết DAILY_BUDGET_USD

    answer = llm_ask(body.question)

    input_tokens, output_tokens = _estimate_tokens(body.question), _estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)
    log_event("llm_call", input_tokens=input_tokens, output_tokens=output_tokens,
              cost_usd=round(cost, 8), question_chars=len(body.question))  # không log nội dung câu hỏi

    return {
        "question": body.question,
        "answer": answer,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cost_usd": round(cost, 8)},
    }


@app.get("/")
def root():
    return {"app": "Production AI Agent", "environment": settings.environment,
            "endpoints": ["GET /health", "GET /ready", "POST /ask (X-API-Key)"]}

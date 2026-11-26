# ============================================================
# Production Dockerfile — Part 6
#   • Multi-stage: builder cài dependencies, runtime chỉ copy kết quả
#   • Base slim, chạy bằng user non-root, có HEALTHCHECK
#   • Lắng nghe theo biến PORT (mặc định 8000), uvicorn là PID 1 → nhận SIGTERM
# ============================================================

# ── Stage 1: builder ─────────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build

# Copy requirements TRƯỚC code → đổi code không phải cài lại dependencies (layer cache)
COPY requirements.txt .
# --prefix=/install: gom toàn bộ package vào 1 thư mục để copy sang stage runtime
RUN pip install --prefix=/install -r requirements.txt


# ── Stage 2: runtime ─────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

# User non-root, không có shell đăng nhập
RUN groupadd --system app && useradd --system --gid app --no-create-home --shell /usr/sbin/nologin app

# Package đã cài ở builder → /usr/local (nơi Python tìm mặc định, user nào cũng đọc được)
COPY --from=builder /install /usr/local

WORKDIR /app
COPY --chown=app:app app/ ./app/
COPY --chown=app:app utils/ ./utils/

USER app

EXPOSE 8000

# Docker tự gọi /health; dùng Python có sẵn (image slim không có curl)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.getenv('PORT', '8000'), timeout=3)"]

# `exec` → uvicorn thay thế sh và trở thành PID 1, nhận SIGTERM trực tiếp khi `docker stop`.
# --timeout-graceful-shutdown 8: chờ request đang chạy tối đa 8s, xong trước khi Docker SIGKILL (10s).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2 --timeout-graceful-shutdown 8"]

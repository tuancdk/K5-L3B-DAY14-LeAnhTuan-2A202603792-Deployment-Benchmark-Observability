"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

  • redis.from_url() chỉ tạo connection pool, KHÔNG kết nối ngay → app khởi động được
    cả khi Redis chưa sẵn sàng (/health vẫn 200, /ready trả 503).
  • Timeout ngắn: Redis chết thì request lỗi nhanh (→ 503), không treo worker.
"""
import redis

from app.config import settings

r = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
    health_check_interval=30,
)


def redis_healthy() -> bool:
    """Dùng cho GET /ready: True nếu PING được Redis."""
    try:
        return bool(r.ping())
    except redis.RedisError:
        return False

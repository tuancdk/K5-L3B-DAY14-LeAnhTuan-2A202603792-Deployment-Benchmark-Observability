"""
Rate limiting (tiêu chí 7) — sliding window 60 giây, state trong Redis.

Mỗi API key có 1 sorted set: member = id request, score = timestamp.
  1. ZREMRANGEBYSCORE  xoá request cũ hơn 60s
  2. ZADD              thêm request hiện tại
  3. ZCARD             đếm số request trong cửa sổ
  4. EXPIRE            key tự xoá khi không còn dùng
4 lệnh chạy trong 1 transaction (MULTI/EXEC) → 2 worker/instance gửi cùng lúc không
thể cùng đọc "còn chỗ" rồi cùng vượt giới hạn. Vượt → xoá request vừa thêm, trả 429.

State nằm trong Redis nên mọi worker/instance dùng chung và vẫn còn sau khi restart agent.
"""
import hashlib
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60


def _redis_key(key: str) -> str:
    # Không lưu API key thô trong Redis — chỉ lưu hash
    return "ratelimit:" + hashlib.sha256(key.encode()).hexdigest()[:24]


def check_rate_limit(key: str) -> None:
    limit = settings.rate_limit_per_minute
    rkey = _redis_key(key)
    now = time.time()
    member = f"{now:.6f}-{uuid.uuid4().hex[:8]}"

    pipe = r.pipeline(transaction=True)
    pipe.zremrangebyscore(rkey, 0, now - WINDOW_SECONDS)
    pipe.zadd(rkey, {member: now})
    pipe.zcard(rkey)
    pipe.expire(rkey, WINDOW_SECONDS + 1)
    _, _, count, _ = pipe.execute()

    if count > limit:
        r.zrem(rkey, member)  # request bị từ chối không chiếm chỗ trong cửa sổ
        oldest = r.zrange(rkey, 0, 0, withscores=True)
        retry_after = int(oldest[0][1] + WINDOW_SECONDS - now) + 1 if oldest else WINDOW_SECONDS
        raise HTTPException(
            status_code=429,
            detail={"error": "Rate limit exceeded", "limit": limit,
                    "window_seconds": WINDOW_SECONDS, "retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after),
                     "X-RateLimit-Limit": str(limit),
                     "X-RateLimit-Remaining": "0"},
        )

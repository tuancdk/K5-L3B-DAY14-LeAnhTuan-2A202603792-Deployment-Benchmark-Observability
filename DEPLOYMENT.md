# Deployment Information

## Public URL

https://day14-agent-orwe.onrender.com

## Platform

**Render** (Blueprint `06-lab-complete/render.yaml`, region Singapore, gói Free)

| Resource | Loại | Ghi chú |
|---|---|---|
| `day14-agent` | Web Service, runtime Docker | Build từ `06-lab-complete/Dockerfile`, health check `/health`, auto-deploy từ nhánh `main` |
| `day14-redis` | Key Value (Valkey 8, tương thích Redis) | State của rate limit và cost guard; chỉ cho kết nối nội bộ |

Code đang chạy: commit `5b411cd` (Live).

> Gói Free tự tắt service khi không có traffic. Request đầu tiên sau đó mất khoảng 30 giây (đo được 27.5s) để khởi động lại.

## Test Commands

Đặt `BASE=https://day14-agent-orwe.onrender.com` và `AGENT_API_KEY` = key lấy từ tab *Environment* của service trên Render (không ghi key vào repo).

### Health check

```bash
curl https://day14-agent-orwe.onrender.com/health
# {"status":"ok","environment":"production","uptime_seconds":1.3}
```

### 4 lệnh tự kiểm (CP6)

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả chạy ngày 2026-11-26:

```text
200
200
401
200
```

### API test (có xác thực)

```bash
curl -X POST "$BASE/ask" \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "What is deployment?"}'
# {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","usage":{"input_tokens":4,"output_tokens":19,"cost_usd":1.2e-05}}
```

### Rate limit (10 request / phút / API key)

```bash
for i in $(seq 1 12); do curl -sS -o /dev/null -w '%{http_code} ' -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"test"}'; done
# 200 200 200 200 200 200 200 200 429 429 429 429   (2 request trước đó đã tính vào cửa sổ 60s)
```

### Script chấm với URL public

```text
$ python check_production_ready.py --public-url https://day14-agent-orwe.onrender.com
     ✅ Public URL: GET https://day14-agent-orwe.onrender.com/health → 200
```

## Environment Variables Set

Tất cả đặt trên Render qua Blueprint/dashboard, không ghi giá trị secret trong code:

| Biến | Giá trị / nguồn |
|---|---|
| `PORT` | Render tự cấp; Dockerfile nghe theo `${PORT:-8000}` |
| `ENVIRONMENT` | `production` |
| `AGENT_API_KEY` | `generateValue: true`, Render tự sinh chuỗi ngẫu nhiên (44 ký tự) |
| `REDIS_URL` | `fromService` → `connectionString` của Key Value `day14-redis` |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.0` |
| `LOG_LEVEL` | không đặt → mặc định `INFO` |

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)

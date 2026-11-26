# Day 14 Lab — Mission Answers

> Môi trường: Windows 11, Python 3.12.3 (venv `.venv`), Docker Desktop 28.1.1.

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns trong `01-localhost-vs-production/develop/app.py`

| # | Anti-pattern | Dòng code | Hậu quả |
|---|---|---|---|
| 1 | Hardcode secret | L17 `OPENAI_API_KEY = "sk-hardcoded-fake-key-never-do-this"`, L18 `DATABASE_URL = "postgresql://admin:password123@..."` | Push lên GitHub là lộ key và mật khẩu DB; muốn đổi key phải sửa code và deploy lại |
| 2 | Log ra secret | L34 `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` | Key nằm trong log, ai đọc được log là có key (đã thấy thật khi chạy, xem 1.2) |
| 3 | Config cứng, không đọc env | L21–22 `DEBUG = True`, `MAX_TOKENS = 500` | Không đổi được hành vi giữa dev/staging/prod nếu không sửa code |
| 4 | `print()` thay vì logging có cấu trúc | L33, L38 | Không có level, timestamp, không parse được bằng log aggregator; còn log cả nội dung câu hỏi/câu trả lời |
| 5 | Không có health check | Không có `/health` (L42 chỉ là comment) | Platform không biết agent còn sống để restart; gọi thử `/health` trả **404** |
| 6 | Bind `localhost` | L51 `host="localhost"` | Trong container, chỉ nhận kết nối từ chính container → bên ngoài không gọi được |
| 7 | Port cố định | L52 `port=8000` | Railway/Render cấp port qua biến `PORT`, app sẽ không nghe đúng port |
| 8 | Bật reload trong production | L53 `reload=True` | Tốn tài nguyên theo dõi file, chạy thêm tiến trình reloader; không phải chế độ chạy production |
| 9 | Không xử lý shutdown | Không có lifespan / SIGTERM | Tắt đột ngột, request đang chạy có thể bị cắt ngang |

### Exercise 1.2: Chạy bản develop

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 422 Unprocessable Entity
{"detail":[{"type":"missing","loc":["query","question"],"msg":"Field required","input":null}]}

$ curl -i http://localhost:8000/health
HTTP/1.1 404 Not Found
```

Log server (secret bị in ra ngoài):

```text
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [4212] using WatchFiles
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.
```

**Quan sát:** app chạy được, nhưng chưa production-ready. Nó chỉ nghe `localhost`, bật reloader, in secret ra log và không có `/health`. App nhận `question` qua query string nên gửi JSON sẽ bị 422.

### Exercise 1.3: So sánh develop và production

Kết quả chạy bản production (`cp .env.example .env` rồi `python app.py`):

```text
GET  /health                     → 200 {"status":"ok","uptime_seconds":2.1,"version":"1.0.0","environment":"development",...}
GET  /ready                      → 200 {"ready":true}
POST /ask {"question":"Hello"}   → 200 {"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock)...","model":"gpt-4o-mini"}
POST /ask {}                     → 422 {"detail":"question field is required"}
Log: Uvicorn running on http://0.0.0.0:8000
```

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong code (L17–22) | `config.py` đọc env (`os.getenv`), có `.env.example` | Cùng một code chạy được ở mọi môi trường; secret không nằm trong git |
| Secrets | Ghi thẳng key, còn in ra log | `OPENAI_API_KEY`, `AGENT_API_KEY` từ env; `validate()` bắt buộc có `AGENT_API_KEY` khi `ENVIRONMENT=production` | Không lộ key; fail sớm khi thiếu cấu hình quan trọng |
| Host / Port | `localhost:8000` cố định | `HOST=0.0.0.0`, `PORT` từ env | Chạy được trong container và trên platform cấp port động |
| Health check | Không có (`/health` → 404) | `/health` (liveness) + `/ready` (readiness, 503 khi chưa sẵn sàng) | Platform biết khi nào restart, load balancer biết khi nào gửi traffic |
| Logging | `print()`, log cả secret | `logging` + JSON (`json.dumps`), không log secret, chỉ log độ dài câu hỏi | Dễ tìm kiếm/lọc trong log aggregator, không lộ dữ liệu nhạy cảm |
| Shutdown | Tắt đột ngột | `lifespan` dọn dẹp khi tắt + handler SIGTERM | Request đang chạy được hoàn thành, không mất dữ liệu |
| Reload / Debug | `reload=True` luôn bật | Chỉ reload khi `DEBUG=true` | Production không chạy reloader thừa |
| Input | Query param, không validate | Body JSON, thiếu `question` → 422 có thông báo rõ | Lỗi rõ ràng cho client |
| CORS | Không cấu hình | `ALLOWED_ORIGINS` từ env | Chỉ cho phép frontend hợp lệ gọi API |

**Một điểm phát hiện thêm khi chạy:** log của bản production không ra JSON như mong đợi. Log thực tế là `WARNING:root:OPENAI_API_KEY not set — using mock LLM`, và không có dòng `{"event": "startup", ...}`. Nguyên nhân là `config.py` gọi `logging.warning()` lúc import (trong `validate()`). Lời gọi đó làm Python tự cấu hình root logger với format mặc định và level WARNING. Vì vậy `logging.basicConfig(...)` trong `app.py` chạy sau không còn tác dụng: các log INFO dạng JSON bị nuốt mất. Bài học: phải cấu hình logging **trước** mọi lời gọi log, hoặc dùng `basicConfig(force=True)`.

**Chưa kiểm tra được:** graceful shutdown bằng SIGTERM, vì Windows không gửi được SIGTERM cho process. Phần này sẽ kiểm tra qua `docker stop` ở Part 5/6.

---

## Part 2: Docker

### Exercise 2.1: Đọc `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11`, bản Python đầy đủ (Debian + compiler + nhiều tool), nên image nặng.
2. **Working directory:** `/app` (`WORKDIR /app`). Mọi lệnh `COPY`/`RUN`/`CMD` sau đó chạy trong thư mục này.
3. **Tại sao COPY requirements.txt trước:** Docker cache theo từng layer, layer nào đổi thì mọi layer sau nó phải build lại. Copy `requirements.txt` rồi `pip install` trước, sau đó mới copy code. Khi chỉ sửa `app.py`, layer cài dependencies (bước chậm nhất) vẫn lấy từ cache. Nếu `COPY . .` trước thì mỗi lần sửa code là phải cài lại toàn bộ package.
4. **CMD vs ENTRYPOINT:** `CMD` là lệnh/tham số mặc định, bị ghi đè khi chạy `docker run <image> <lệnh khác>`. `ENTRYPOINT` là chương trình cố định luôn được chạy; tham số của `docker run` (hoặc `CMD`) được nối thêm vào sau nó. Dockerfile này dùng `CMD ["python", "app.py"]` (exec form) nên có thể ghi đè để debug, vd `docker run -it my-agent:develop sh`.

### Exercise 2.2: Build và chạy image develop

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
$ docker run -d --name p2-develop -p 8000:8000 my-agent:develop

$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.5,"container":true}

$ curl -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker?"
HTTP/1.1 200 OK
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
```

(Lần build đầu bị Docker Hub trả `429 Too Many Requests` do pull ẩn danh; sau khi `docker login` thì build được.)

### Exercise 2.3: Multi-stage build và kích thước image

`02-docker/production/Dockerfile`:

- **Stage 1 (`builder`)**: từ `python:3.11-slim`, cài `gcc`, `libpq-dev` (build tools), rồi `pip install --user` dependencies vào `/root/.local`. Stage này chỉ dùng để build, không được deploy.
- **Stage 2 (`runtime`)**: bắt đầu lại từ `python:3.11-slim` sạch, chỉ `COPY --from=builder /root/.local` (các package đã cài) cùng `main.py` và `utils/`. Image chạy bằng user non-root `appuser`, có `HEALTHCHECK`, CMD chạy uvicorn với 2 worker.
- **Tại sao nhỏ hơn:** image cuối không chứa compiler, header, apt cache và các file trung gian của stage builder. Base `slim` cũng nhỏ hơn nhiều so với `python:3.11` đầy đủ.

Kích thước đo bằng `docker images my-agent`:

```text
REPOSITORY   TAG        IMAGE ID       CREATED         SIZE
my-agent     advanced   614c38684f5a   2 seconds ago   240MB
my-agent     develop    07c5c80d27c1   2 minutes ago   1.67GB
```

- Develop: **1670 MB**
- Production: **240 MB**
- Chênh lệch: (1670 − 240) / 1670 ≈ **85.6%** nhỏ hơn

(Số đo trên máy mình khác số ước lượng trong README (~800 MB / ~160 MB). Docker Desktop đang dùng containerd image store nên `docker images` báo kích thước đã giải nén. `docker image inspect --format '{{.Size}}'` báo 426 MB và 57 MB, là kích thước nén.)

### Exercise 2.4: Docker Compose stack

Chạy: `touch .env.local && docker compose -p k5-day14-docker up -d --build` trong `02-docker/production/`.

Services được start:

```text
k5-day14-docker-agent-1    Up (healthy)   8000/tcp                       ← không public
k5-day14-docker-nginx-1    Up             0.0.0.0:80->80/tcp, 443->443
k5-day14-docker-qdrant-1   Up (healthy)   6333-6334/tcp
k5-day14-docker-redis-1    Up (healthy)   6379/tcp
```

Sơ đồ:

```text
                 ┌────────────────────────────── network "internal" (bridge) ─────────────────────────┐
 Client ──:80──▶ │ Nginx (reverse proxy + LB,  ──▶  agent:8000 (FastAPI, uvicorn 2 workers)          │
                 │  rate limit 10 r/s/IP,              │  REDIS_URL=redis://redis:6379/0               │
                 │  security headers)                  │  QDRANT_URL=http://qdrant:6333                │
                 │                                     ├──▶ redis:6379  (session cache / rate limit)   │
                 │                                     └──▶ qdrant:6333 (vector DB cho RAG)            │
                 └────────────────────────────────────────────────────────────────────────────────────┘
```

Các service giao tiếp thế nào:
- Chỉ **Nginx** mở cổng ra host (80/443). Agent không map port ra ngoài, chỉ nhận traffic qua Nginx (`proxy_pass http://agent_backend` → `agent:8000`).
- Các container cùng network `internal` gọi nhau bằng **tên service** (Docker DNS): `agent`, `redis`, `qdrant`.
- `depends_on ... condition: service_healthy`: agent chỉ start sau khi Redis và Qdrant đã healthy. Nginx start sau agent.
- Ghi chú: `main.py` của bài mẫu nhận `REDIS_URL`, `QDRANT_URL` nhưng chưa thực sự dùng Redis/Qdrant.

Test qua Nginx:

```text
$ curl -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
{"status":"ok","uptime_seconds":4.1,"version":"2.0.0","timestamp":"2026-11-26T08:28:51.609263"}

$ curl -i -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

Header `Server: nginx` và các security header cho thấy request đã đi qua Nginx. Log Nginx có dòng `"POST /ask HTTP/1.1" 200`.

Debug: xem log bằng `docker compose -p k5-day14-docker logs agent`. Trong log có dòng `{"time":"...","level":"INFO","msg":"{"event": "request", "q_len": 21}"}`. Dòng này **không phải JSON hợp lệ**, vì chuỗi JSON được nhét vào trong format string mà không escape dấu `"`. Đây là lưu ý khi tự viết JSON formatter ở Part 6.

Dọn: `docker compose -p k5-day14-docker down -v`.

---

## Part 3: Cloud Deployment

> Theo `CHECKPOINTS.md`, lớp K5 làm Part 3 sau Part 6 và deploy chính agent Part 6 (`06-lab-complete/`).

### Exercise 3.2: Deploy Render (nền tảng chính)

- **URL:** https://day14-agent-orwe.onrender.com
- Render → New → Blueprint → repo này, Blueprint path `06-lab-complete/render.yaml`. Blueprint tạo web service `day14-agent` (Docker) và Key Value `day14-redis` (Valkey 8). `AGENT_API_KEY` do Render tự sinh, `REDIS_URL` lấy từ connection string của Key Value.
- Chi tiết, lệnh test và biến môi trường: [DEPLOYMENT.md](DEPLOYMENT.md). Ảnh: `screenshots/dashboard.png`, `running.png`, `test.png`.

4 lệnh tự kiểm của CP6 trên URL public:

```text
GET  /health                 → 200
GET  /ready                  → 200   (Redis trên Render kết nối được)
POST /ask không có key       → 401
POST /ask có X-API-Key       → 200
```

Request đầu tiên mất 27.5s vì gói Free của Render tắt service khi không có traffic (cold start).

### Nền tảng còn lại: so sánh `railway.toml` và `render.yaml`

| | `railway.toml` | `render.yaml` |
|---|---|---|
| Bản chất | *Config as code* cho **một service** | *Blueprint* (Infrastructure as Code) cho **cả stack** |
| Phạm vi | Chỉ cách build/deploy service chứa file | Khai báo nhiều resource: web service + Key Value (Redis), region, plan |
| Build | `[build] builder = "DOCKERFILE"`, `dockerfilePath` | `runtime: docker`, `rootDir`, `dockerfilePath` |
| Health check | `healthcheckPath = "/health"`, `healthcheckTimeout` | `healthCheckPath: /health` |
| Restart | `restartPolicyType = "ON_FAILURE"`, số lần retry | Render tự quản lý |
| Biến môi trường / secret | Không khai báo trong file; đặt bằng `railway variables` hoặc dashboard | Khai báo trong file. Secret dùng `generateValue: true` (tự sinh) hoặc `sync: false` (nhập tay), không ghi giá trị |
| Redis | Tạo Redis riêng trong project, rồi tham chiếu `REDIS_URL` trên dashboard | Khai báo luôn `type: keyvalue` và nối bằng `fromService ... property: connectionString` |
| Deploy | `railway up` từ CLI (upload code trên máy) | Đọc từ GitHub, `autoDeploy` mỗi khi push |
| Định dạng | TOML | YAML |

**Vì sao khác:** Railway coi mỗi service là một đơn vị, và cấu hình dùng chung của project (biến, Redis) quản lý qua dashboard/CLI. Render Blueprint mô tả toàn bộ hạ tầng trong git, nên tái tạo được cả stack từ một file và thay đổi được review như code. Đổi lại, file dài hơn và cần cú pháp riêng cho secret.

### Exercise 3.3: Cloud Run CI/CD (`03-cloud-deployment/production-cloud-run/`)

`cloudbuild.yaml` là pipeline Cloud Build chạy mỗi khi push lên `main`, gồm 4 bước nối tiếp nhau (`waitFor`):
1. **test**: trong `python:3.11-slim`, cài requirements + pytest rồi chạy `pytest tests/`. Fail là dừng cả pipeline.
2. **build**: build Docker image, gắn 2 tag `$COMMIT_SHA` và `latest`, dùng `--cache-from` image `latest` để tận dụng layer cache.
3. **push**: đẩy image lên Container Registry `gcr.io/$PROJECT_ID/ai-agent`.
4. **deploy**: `gcloud run deploy` image đúng `$COMMIT_SHA` lên Cloud Run (asia-southeast1, 1–10 instance, 512Mi, 1 CPU, timeout 60s). `OPENAI_API_KEY` lấy từ Secret Manager.

`service.yaml` mô tả cùng service theo kiểu khai báo (Knative): autoscale 1–10 instance, mỗi instance tối đa 80 request đồng thời. Secret lấy qua `secretKeyRef`, liveness probe `/health`, startup probe `/ready`.

Nhận xét: thư mục mẫu chỉ có 2 file config, không có `tests/`, `requirements.txt` hay `Dockerfile`. Chạy nguyên trạng thì pipeline fail ngay bước 1; phải đặt 2 file này vào thư mục gốc của project thật. Ngoài ra `minScale: 1` giúp tránh cold start (khác gói Free của Render ở trên) nhưng tốn tiền 24/7.

---

## Part 4: API Security

### Exercise 4.1: API Key authentication (`04-api-gateway/develop/app.py`)

- **Key được check ở đâu?** Trong dependency `verify_api_key()`. Header được đọc bằng `APIKeyHeader(name="X-API-Key", auto_error=False)`, và endpoint `/ask` gắn `Depends(verify_api_key)`. Key đúng lấy từ biến môi trường `AGENT_API_KEY`.
- **Sai key thì sao?** Thiếu header → **401** `Missing API key`. Có header nhưng sai → **403** `Invalid API key.`. Request bị chặn trước khi gọi LLM. (Part 6 yêu cầu cả hai trường hợp đều trả 401.)
- **Rotate key thế nào?** Key nằm trong env, không nằm trong code. Đặt `AGENT_API_KEY` mới trên platform/`.env` rồi restart (hoặc redeploy) app. Để không làm gián đoạn client, có thể cho app chấp nhận tạm 2 key (cũ + mới) trong thời gian chuyển đổi, đợi client đổi xong thì bỏ key cũ. Lưu ý: code có giá trị mặc định `demo-key-change-in-production` khi thiếu env, production nên fail ngay thay vì dùng key mặc định.

Chạy: `AGENT_API_KEY=secret-key-123`, `uvicorn app:app --port 8000` (bỏ `reload=True` của `python app.py`, app giữ nguyên).

```text
# Không có key
$ curl -i http://localhost:8000/ask -X POST -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

# Sai key
$ curl -i http://localhost:8000/ask -X POST -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

# Có key đúng
$ curl -i http://localhost:8000/ask -X POST -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

### Exercise 4.2: JWT authentication (`04-api-gateway/production/auth.py`)

**JWT flow:**
1. Client gửi `username/password` tới `POST /auth/token`. `authenticate_user()` kiểm tra với `DEMO_USERS`; sai thì trả 401 `Invalid credentials`.
2. `create_token()` tạo payload `{sub, role, iat, exp = now + 60 phút}`, ký bằng HMAC-SHA256 (`HS256`) với `JWT_SECRET`.
3. Client gửi `Authorization: Bearer <token>` ở mọi request.
4. `verify_token()` gọi `jwt.decode()` để kiểm tra chữ ký và hạn dùng, rồi lấy `username`, `role` từ token. Server không cần tra database (stateless). Thiếu token → 401. Token hết hạn → 401. Token giả/sửa → 403.

Lưu ý bảo mật: `JWT_SECRET` có giá trị mặc định cứng trong code (`super-secret-change-in-production-please`). Nếu quên đặt env thì ai biết chuỗi này cũng tự ký được token, nên production phải bắt buộc có env.

```text
$ curl -i http://localhost:8000/auth/token -X POST -H "Content-Type: application/json" -d '{"username": "student", "password": "demo123"}'
HTTP/1.1 200 OK
{"access_token":"<TOKEN_ĐÃ_CHE>","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer <ĐÃ_CHE>..."}

# Sai mật khẩu
{"detail":"Invalid credentials"}

# /ask không token
HTTP/1.1 401 Unauthorized
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

# /ask token giả
HTTP/1.1 403 Forbidden
{"detail":"Invalid token."}

# /ask với token hợp lệ
$ curl -i http://localhost:8000/ask -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 200 OK
x-content-type-options: nosniff
x-frame-options: DENY
{"question":"Explain JWT","answer":"Đây là câu trả lời từ AI agent (mock)...","usage":{"requests_remaining":9,"budget_remaining_usd":2.1e-05}}
```

### Exercise 4.3: Rate limiting (`04-api-gateway/production/rate_limiter.py`)

- **Algorithm:** sliding window (dạng *sliding window log*). Mỗi user có một `deque` chứa timestamp các request. Mỗi lần check thì bỏ các timestamp cũ hơn 60 giây, rồi đếm số còn lại. Đủ giới hạn thì trả 429, chưa đủ thì thêm timestamp mới.
- **Limit:** user thường (`student`) **10 request / 60 giây**. Admin (`teacher`) 100 request / 60 giây.
- **Bypass cho admin:** trong `app.py`, `limiter = rate_limiter_admin if role == "admin" else rate_limiter_user`, tức role được đọc từ JWT. Admin không được bỏ qua hoàn toàn mà dùng limiter riêng với hạn mức 100/phút. Muốn bypass hẳn thì bỏ qua lệnh `limiter.check()` khi `role == "admin"`.
- **Hạn chế:** state nằm trong RAM của process (`dict` + `deque`). Chạy nhiều worker/instance thì mỗi process đếm riêng, restart là mất. Production cần chuyển sang Redis (Part 6 làm việc này).

Vòng lặp 20 request bằng token của `student`:

```text
Request 1: HTTP/1.1 200 OK
...
Request 9: HTTP/1.1 200 OK
Request 10: HTTP/1.1 429 Too Many Requests  {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":54}}
...
Request 20: HTTP/1.1 429 Too Many Requests  {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":49}}
```

Response 429 đầy đủ:

```text
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795681922
retry-after: 54
{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":54}}
```

Request thứ 10 của vòng lặp đã bị chặn, vì request ở Exercise 4.2 cũng tính vào cửa sổ 60 giây: tổng cộng 10 request được chấp nhận, đúng giới hạn. `/me/usage` sau đó báo `"requests":10`.

### Exercise 4.4: Cost guard

**Cách `cost_guard.py` hiện tại hoạt động:**
- `check_budget(user)` được gọi **trước** khi gọi LLM. Tổng chi phí toàn hệ thống ≥ $10/ngày thì trả **503** (ngắt toàn bộ service). Chi phí của user ≥ $1/ngày thì trả **402 Payment Required**. Khi user dùng ≥ 80% budget thì ghi log cảnh báo.
- `record_usage(user, input_tokens, output_tokens)` được gọi **sau** khi gọi LLM. Hàm này cộng token, tính cost theo giá gpt-4o-mini ($0.15 / 1M input, $0.60 / 1M output) và cộng vào tổng global.
- Reset theo ngày: mỗi bản ghi lưu `day`, sang ngày mới thì tạo bản ghi mới.
- Lỗi nhỏ phát hiện khi chạy: field `budget_remaining_usd` trong response `/ask` thực ra trả **số đã dùng** (`usage.total_cost_usd` = 2.1e-05), không phải số còn lại.
- Hạn chế: state trong RAM, giống rate limiter.

**Implement `check_budget` với Redis ($10/tháng/user, reset đầu tháng):**

```python
import redis
from datetime import datetime, timezone

r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
MONTHLY_BUDGET_USD = float(os.getenv("MONTHLY_BUDGET_USD", "10"))

def check_budget(user_id: str, estimated_cost: float) -> bool:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    key = f"budget:{user_id}:{month}"          # key theo tháng → sang tháng mới tự là key mới (reset)
    new_total = r.incrbyfloat(key, estimated_cost)   # cộng nguyên tử → an toàn khi nhiều instance
    r.expire(key, 32 * 24 * 3600)               # tự xoá sau ~1 tháng, không rác Redis
    if new_total > MONTHLY_BUDGET_USD:
        r.incrbyfloat(key, -estimated_cost)     # hoàn lại phần vừa giữ chỗ
        return False                            # → endpoint trả HTTP 402
    return True
```

Khác với lời giải mẫu (`GET` rồi mới `INCRBYFLOAT`), bản trên **cộng trước rồi kiểm tra**. Hai request đồng thời ở hai instance không thể cùng đọc thấy "còn budget" rồi cùng vượt ngưỡng, vì `INCRBYFLOAT` là thao tác nguyên tử trong Redis.

**Vì sao cần cả rate limit lẫn cost guard:** rate limit bảo vệ **tốc độ** (chống spam, quá tải). Cost guard bảo vệ **tiền**: một user gọi đúng 10 req/phút nhưng mỗi request là prompt rất dài vẫn có thể đốt hết ngân sách. Hai lớp chặn hai rủi ro khác nhau.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks (`05-scaling-reliability/develop/app.py`)

- `/health` (**liveness**): "process còn sống không?". Trả 200 cùng uptime, version, environment và `checks` (memory qua `psutil`, nếu có). Platform gọi định kỳ; non-200 hoặc timeout thì **restart** container.
- `/ready` (**readiness**): "đã sẵn sàng nhận traffic chưa?". Trả 503 khi đang khởi động, đang shutdown (`_is_ready = False`) hoặc dependency (DB, Redis) chưa kết nối. Load balancer **ngừng gửi traffic** vào instance đó nhưng **không restart** nó.
- Vì sao tách hai cái: nếu `/health` kiểm tra luôn Redis thì khi Redis chết, platform sẽ restart *mọi* instance liên tục mà không giải quyết được gì. Redis chết là lỗi của dependency, nên chỉ cần ngừng nhận traffic (readiness), process vẫn sống (liveness).

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":1.0,"version":"1.0.0","environment":"development","timestamp":"2026-11-26T08:34:22.088569+00:00","checks":{"memory":{"status":"ok","note":"psutil not installed"}}}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true,"in_flight_requests":1}        ← chính request /ready đang được đếm

$ curl -i -X POST "http://localhost:8000/ask?question=Long%20task"
HTTP/1.1 200 OK
{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
```

### Exercise 5.2: Graceful shutdown

Cơ chế trong `develop/app.py`:
1. uvicorn nhận SIGTERM, **ngừng nhận request mới** (đóng socket listen).
2. Chờ các request đang chạy xong (`timeout_graceful_shutdown=30`).
3. Chạy phần sau `yield` của `lifespan`: `_is_ready = False` (để `/ready` trả 503), chờ `_in_flight_requests` về 0 (tối đa 30s), đóng kết nối.
4. Process thoát với exit code 0.

Handler `handle_sigterm` tự đăng ký chỉ để log. Lúc server chạy, uvicorn thay handler đó bằng handler của nó; khi shutdown xong, uvicorn gọi lại handler cũ. Vì vậy dòng `Received signal 15` xuất hiện **cuối cùng** trong log.

Test: Windows không gửi được SIGTERM, nên chạy chính `develop/app.py` trong container `python:3.11-slim` (mount code), gửi `POST /ask` rồi 30 ms sau `docker kill -s TERM`:

```text
[client] HTTP 200, 0.162305s                     ← request đang chạy vẫn hoàn thành
Running=false ExitCode=0                          ← thoát êm

INFO:     172.17.0.1:51296 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
INFO:     Shutting down
INFO:     Waiting for application shutdown.
2026-11-26 08:35:15,251 INFO 🔄 Graceful shutdown initiated...
2026-11-26 08:35:15,251 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [1]
2026-11-26 08:35:15,251 INFO Received signal 15 — uvicorn will handle graceful shutdown
```

### Exercise 5.3: Stateless design (`05-scaling-reliability/production/app.py`)

- Anti-pattern: `conversation_history = {}` trong RAM. Mỗi instance (và mỗi worker) có bộ nhớ riêng, nên request tiếp theo của cùng user rơi vào instance khác sẽ **không thấy history**. Restart hay scale down là mất sạch.
- Bản production lưu session trong **Redis**: key `session:<session_id>`, `SETEX` với TTL 1 giờ, giữ tối đa 20 message. Instance nào cũng đọc/ghi cùng một chỗ, nên có thể scale ngang và kill instance bất kỳ.
- Lưu ý: code tự fallback về dict trong RAM nếu không kết nối được Redis lúc khởi động (`USE_REDIS = False`). Bản demo này tiện, nhưng production nên fail (hoặc `/ready` = 503) thay vì âm thầm chạy chế độ không scale được. Response có field `storage` để kiểm tra.

### Exercise 5.4: Load balancing

Chạy trong `05-scaling-reliability/production/`: `docker compose -p k5-day14-scaling up -d --build --scale agent=3` (cổng **8080**, endpoint **`/chat`**).

```text
k5-day14-scaling-agent-1: Up (healthy) 8000/tcp
k5-day14-scaling-agent-2: Up (healthy) 8000/tcp
k5-day14-scaling-agent-3: Up (healthy) 8000/tcp
k5-day14-scaling-nginx-1: Up 0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1: Up (healthy) 6379/tcp
```

10 request qua Nginx, Nginx chia **round-robin** đều cho 3 instance:

```text
Request 1 -> instance-54124d redis
Request 2 -> instance-e93bf5 redis
Request 3 -> instance-01b2f6 redis
Request 4 -> instance-54124d redis
Request 5 -> instance-e93bf5 redis
Request 6 -> instance-01b2f6 redis
Request 7 -> instance-54124d redis
Request 8 -> instance-e93bf5 redis
Request 9 -> instance-01b2f6 redis
Request 10 -> instance-54124d redis
```

### Exercise 5.5: Test stateless

`python test_stateless.py`: 5 câu trong cùng một session, mỗi câu do một instance khác nhau phục vụ, history vẫn đủ:

```text
Session ID: ccf07f80-3b8e-45aa-8782-65c21dd727ff
Request 1: [instance-01b2f6]   Q: What is Docker?
Request 2: [instance-54124d]   Q: Why do we need containers?
Request 3: [instance-e93bf5]   Q: What is Kubernetes?
Request 4: [instance-01b2f6]   Q: How does load balancing work?
Request 5: [instance-54124d]   Q: What is Redis used for?
Instances used: {'instance-e93bf5', 'instance-01b2f6', 'instance-54124d'}
✅ All requests served despite different instances!
--- Conversation History ---
Total messages: 10
```

**Giải thích:** request 1 ghi session vào Redis. Request 2 tới instance khác nhưng đọc được đúng session đó từ Redis, rồi nối thêm. Không instance nào giữ state riêng, nên 10 message (5 câu hỏi + 5 trả lời) đều còn nguyên.

**Thử thêm: tắt 1 instance giữa chừng** (script không tự làm bước này). `docker stop k5-day14-scaling-agent-2` (instance `instance-01b2f6`), rồi gửi tiếp vào cùng session:

```text
Request 6–9  -> HTTP 200 (cả 4)
Request 10 -> instance-e93bf5
Request 11 -> instance-54124d
Request 12 -> instance-e93bf5
Request 13 -> instance-54124d
history count = 20

agent-2: ExitCode=0, log "Instance instance-01b2f6 shutting down" → tắt êm
nginx: upstream timed out ... while connecting to upstream "http://172.18.0.3:8000/chat"
```

- Session vẫn tiếp tục trên 2 instance còn lại, không mất history.
- Nginx còn giữ IP của instance đã tắt (upstream chỉ phân giải DNS lúc khởi động). Lần đầu gửi tới nó bị timeout, sau đó nhờ `proxy_next_upstream error timeout http_503` Nginx thử instance khác, nên client vẫn nhận 200, chỉ chậm hơn ở request đó.
- `turn` đứng ở 11 vì history bị giới hạn 20 message (10 lượt), nên số lượt tính từ history không tăng nữa.

Dọn: `docker compose -p k5-day14-scaling down -v`.

---

## Part 6: Final Project — `check_production_ready.py`

Chạy đầy đủ (có Docker) trong `06-lab-complete/` trên Windows 11 + Docker Desktop:

```text
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ⚠️  Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 11/12 tiêu chí đạt · 42/42 checks passed
  ⚠️  Chưa kiểm tra được: Graceful shutdown
================================================================

     ✅ Docker: image < 500 MB — 56 MB
     ✅ Docker: `docker stop` tắt êm (exit code 0, < 10s) — exit code 0, 0.9s
```

**Về mục ⚠️ Graceful shutdown:** máy làm bài chạy Python Windows. Script tự đánh dấu ⚠️ cho bài test SIGTERM vì *"Windows không gửi được SIGTERM"* (xem `README.md` mục *Lưu ý khi làm bài* số 2). Không có check nào ❌. Phần Docker của tiêu chí này đã ✅ (`docker stop` → exit code 0 trong 0.9s).

Để tự kiểm chứng bài test SIGTERM, mình chạy chính `check_production_ready.py` (không sửa) trong container Linux `python:3.11-slim`, với Redis riêng (`--skip-docker --redis-url ...`, vì phần Docker đã kiểm tra ở trên):

```text
⚠️  11. Graceful shutdown
     ✅ Code xử lý SIGTERM / lifespan shutdown
     ✅ Khi chạy: SIGTERM lúc đang có request → request vẫn 200, process tự thoát — exit code 0
     ⚠️  Docker: `docker stop` tắt êm (exit code 0, < 10s) — chưa chạy (--skip-docker)
...
  Result: 10/12 tiêu chí đạt · 39/39 checks passed
  ⚠️  Chưa kiểm tra được: Dockerfile (multi-stage, < 500 MB), Graceful shutdown   ← chỉ vì --skip-docker
```

Gộp hai lần chạy, mọi check của 12 tiêu chí đều ✅, không có check nào ❌.

### Test thủ công qua `docker compose up` (agent + Redis)

```text
/health            → 200 {"status":"ok","environment":"development","uptime_seconds":0.3}
/ready             → 200 {"status":"ready"}
/ask không key     → 401 {"detail":"Invalid or missing API key. Include header: X-API-Key: <key>"}
/ask sai key       → 401 {"detail":"Invalid or missing API key. Include header: X-API-Key: <key>"}
/ask key đúng      → 200 {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server...","usage":{...}}
body sai field     → 422 {"detail":[{"type":"missing","loc":["body","question"],"msg":"Field required",...}]}
question rỗng      → 422 {"detail":[{"type":"value_error","loc":["body","question"],"msg":"Value error, question must not be blank",...}]}
JSON hỏng          → 422 {"detail":[{"type":"json_invalid","loc":["body",12],"msg":"JSON decode error",...}]}
rate limit 15 lần  → 200 ×9, 429 ×6   (request /ask trước đó đã tính 1 → đúng 10/phút)
Redis bị stop      → /health 200, /ready 503 {"detail":"Not ready: Redis unavailable"},
                     /ask 503 {"detail":"Service temporarily unavailable"} (không lộ stack trace)
Redis chạy lại     → /ready 200
```

Log JSON của agent (1 dòng):

```json
{"ts": "2026-11-26T08:49:17.166497+00:00", "lvl": "INFO", "logger": "agent", "event": "request", "request_id": "0b21bf44a2ab", "method": "POST", "path": "/ask", "status": 503, "ms": 8004.5}
```

### Thiết kế chính

| Thành phần | Cách làm | Vì sao |
|---|---|---|
| `config.py` | Dataclass đọc env bằng `os.getenv`; `AGENT_API_KEY` mặc định rỗng | 12-factor; không có "key mặc định" nào trong code |
| `auth.py` | `APIKeyHeader(auto_error=False)`, `secrets.compare_digest`; thiếu/sai/server chưa cấu hình key → 401 | `auto_error=True` sẽ trả 403/422; fail closed; chống timing attack |
| `rate_limiter.py` | Sorted set Redis, 1 transaction `ZREMRANGEBYSCORE → ZADD → ZCARD → EXPIRE`; vượt thì `ZREM` + 429 + `Retry-After` | Thêm trước rồi đếm → 2 worker không cùng lọt qua giới hạn; key Redis là hash của API key, không lưu key thô |
| `cost_guard.py` | `INCRBYFLOAT` vào key `cost:YYYY-MM-DD` (UTC), TTL 2 ngày; tổng ≥ `DAILY_BUDGET_USD` → 402 | Nguyên tử khi nhiều instance; sang ngày mới tự reset |
| `redis_client.py` | `redis.from_url` (không kết nối lúc import), timeout 2s | App khởi động được khi chưa có Redis; Redis chết thì lỗi nhanh |
| Logging | `JsonFormatter` dùng `json.dumps`, logger `agent` có handler riêng, `propagate=False` | Tránh 2 lỗi đã gặp ở Part 1/Part 2 (basicConfig bị vô hiệu, JSON lồng không escape) |
| Lỗi | Handler `RedisError` → 503, `Exception` → 500 chung chung; 422 do Pydantic | Mã lỗi rõ, không lộ stack trace; traceback chỉ nằm trong log |
| Shutdown | `lifespan` (log + đóng pool Redis), không tự đăng ký SIGTERM; `--timeout-graceful-shutdown 8` | Không chặn mất handler của uvicorn; xong trước khi Docker SIGKILL ở giây thứ 10 |
| Dockerfile | Builder `pip install --prefix=/install` → runtime copy sang `/usr/local`; user `app`; `HEALTHCHECK` bằng Python; `CMD sh -c "exec uvicorn ... --port ${PORT:-8000} --workers 2"` | Image 56 MB; không cần `PYTHONUSERBASE`; `exec` → uvicorn là PID 1 nhận SIGTERM |
| Compose | `agent` (build, `env_file .env required:false`, `REDIS_URL=redis://redis:6379/0`, `depends_on: service_healthy`) + `redis:7-alpine` có healthcheck, AOF | Một lệnh chạy cả stack; agent chỉ start khi Redis sẵn sàng |

---

## Part 7 (ghi chú thêm — báo cáo chính ở [BENCHMARK_REPORT.md](BENCHMARK_REPORT.md))

### Exercise 7.1: Đọc metrics

Output `GET /metrics` của agent v2 sau 1 request `/ask` (trích):

```text
http_requests_total{method="POST",route="/ask",status="200"} 1.0                 ← Counter
agent_in_flight_requests 0.0                                                      ← Gauge
agent_info{model="mock-llm-small",version="v2"} 1.0                               ← Gauge (info)
http_request_duration_seconds_bucket{le="0.3",method="POST",route="/ask"} 2.0     ← Histogram
llm_tokens_total{model="mock-llm-small",type="input"} 91.0                        ← Counter
llm_cost_usd_total{model="mock-llm-small"} 4.6649999999999996e-05                 ← Counter
agent_question_length_chars_bucket{le="50.0"} 1.0                                 ← Histogram (metric tự thêm)
agent_question_length_chars_sum 40.0
```

1. **Counter:** `http_requests_total`, `llm_tokens_total` (chỉ tăng, dùng với `rate()`). **Gauge:** `agent_in_flight_requests` (lên xuống theo số request đang xử lý). **Histogram:** `http_request_duration_seconds` (`_bucket`/`_sum`/`_count`, tính percentile bằng `histogram_quantile()`).
2. Nếu label `route` dùng URL thật (`request.url.path`), mỗi URL lạ mà bot quét (`/wp-admin`, `/.env`, `/a1b2c3`...) sẽ tạo một **time series mới** cho mỗi tổ hợp `method × route × status`. Với histogram, mỗi series còn nhân thêm ~13 bucket. Hàng nghìn URL ngẫu nhiên làm **cardinality bùng nổ**: Prometheus tốn RAM/disk, query chậm, thậm chí OOM. Kẻ tấn công còn có thể cố tình làm sập hệ thống giám sát. Dùng `route.path` (template, vd `/ask`) và gom URL không khớp vào `unmatched` giữ số series cố định.

**Nhiệm vụ:** đã thêm `agent_question_length_chars` (Histogram, bucket 20/50/100/200/500/1000/2000) vào `develop/app.py`, gọi `QUESTION_LENGTH.observe(len(body.question))` trong `/ask`. Dùng Histogram thay vì label `length=...` vì độ dài có vô số giá trị.

---

## AI log

| # | Hỏi AI gì | Nhận được gì | Tự sửa / tự kiểm tra thế nào |
|---|---|---|---|
| 1 | (Claude Code) Đọc repo, tóm tắt yêu cầu bài và các tiêu chí của script chấm | Tóm tắt các file nộp, 12 tiêu chí, những chỗ dễ trượt | Đối chiếu lại với `RUBRIC.md`, `CHECKPOINTS.md`, `check_production_ready.py` |
| 2 | (Claude Code) Chạy Part 1 develop/production và soạn câu trả lời CP1 | Lệnh chạy, output curl, bảng anti-pattern và bảng so sánh | Output là từ lệnh chạy thật trên máy; đối chiếu số dòng trong `develop/app.py`; tự kiểm lại nguyên nhân log không ra JSON |
| 3 | (Claude Code) Build/chạy image Part 2, chạy compose stack, soạn câu trả lời CP2 | Lệnh build/run, xử lý lỗi Docker Hub 429 (cần `docker login`), sơ đồ stack | Kích thước image lấy từ `docker images` trên máy; tự tính % chênh lệch; đối chiếu sơ đồ với `docker-compose.yml` |
| 4 | (Claude Code) Chạy Part 4 develop/production, vòng lặp rate limit, giải thích JWT/rate limit/cost guard | Output curl 401/403/200/429 (token đã che), code `check_budget` với Redis | Output từ lệnh chạy thật; tự đếm vì sao 429 xuất hiện ở request thứ 10; tự kiểm lại tính nguyên tử của `INCRBYFLOAT` |
| 5 | (Claude Code) Chạy Part 5: health/ready, test SIGTERM trong container Linux, stack 3 instance, `test_stateless.py`, tắt 1 instance | Lệnh chạy, output, giải thích liveness/readiness, stateless, failover của Nginx | Output từ lệnh chạy thật; tự đối chiếu log Nginx để hiểu vì sao request vẫn 200 khi 1 instance chết |
| 6 | (Claude Code) Viết code Part 6 cho các TODO trong `06-lab-complete/` (config, auth, rate limiter, cost guard, redis client, main, Dockerfile, compose, .dockerignore, .env.example, railway.toml, render.yaml) | Code hoàn chỉnh kèm comment giải thích | Chạy `check_production_ready.py` (static → skip-docker → đầy đủ) sau mỗi bước; chạy script trong container Linux để kiểm tra SIGTERM; test tay qua `docker compose` các ca 401/422/429/503; đọc lại từng file để giải thích được với Lab Coach |
| 7 | (Claude Code) Kiểm tra deploy Render qua Claude in Chrome, chạy 4 lệnh CP6, soạn `DEPLOYMENT.md`, so sánh `railway.toml`/`render.yaml`, mô tả pipeline Cloud Run | URL, kết quả 200/200/401/200, bảng so sánh, tóm tắt pipeline | Tự deploy Blueprint trên Render; tự lấy key từ dashboard (không dán vào chat); đối chiếu bảng so sánh với 2 file config; đọc lại `cloudbuild.yaml`, `service.yaml` |
| 8 | (Claude Code) CP7: thêm metric 7.1, chạy load test 4 mức + 3 mức `MOCK_SLOW_RATE`, eval v1/v2, sửa `KNOWLEDGE_BASE`, viết `.github/workflows/eval.yml`, điền mục 1–3 `BENCHMARK_REPORT.md` | Lệnh chạy, file JSON, nhận xét | Mọi số trong báo cáo trích bằng script từ `reports/*.json`; tự kiểm lại phép tính (Little's law, tỉ lệ cost/câu đúng); xử lý lỗi encoding Windows bằng `PYTHONUTF8=1` (không sửa script) |

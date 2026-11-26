# Benchmark & Observability Report — Day 14

> Mọi con số lấy từ file report thật: `reports/*.json` (load test, eval), `benchmark-results/*.csv` (Locust) và ảnh trong `screenshots/`.

**Họ tên:** Lê Anh Tuấn
**Ngày chạy:** 2026-11-26
**Môi trường:** Laptop Windows 11 Home, Intel Core i7-1360P (12 nhân / 16 luồng), RAM 16 GB, Python 3.12.3, Docker Desktop 28.1.1. Agent develop chạy 1 process uvicorn; `load_test.py` chạy trên cùng máy.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | LLM mock mất ~120–180 ms + retrieval ~20 ms → p50 ≈ 200 ms. 500 ms (gấp ~2.5 lần p50) đủ chỗ cho jitter và hàng đợi khi tải vừa, nhưng vẫn bắt được khi tail latency lan lên p95 (đo được ở `MOCK_SLOW_RATE=0.1`: p95 = 1677.8 ms). Không đặt cho p99 vì mock cố tình có ~3% request chậm 1.5 s |
| Error rate `/ask` | < 2 % | Upstream LLM mock lỗi ~1% (`MOCK_ERROR_RATE=0.01`), đo được 0.33–1.33% ở tải bình thường. 2% chừa error budget gấp đôi mức nền; alert production đặt lỏng hơn (5% trong 1 phút) để tránh báo động vì spike ngắn |
| Accuracy trên golden dataset | ≥ 85 % (CI) · mục tiêu 100 % | Golden dataset chỉ có 23 câu, sai 1 câu = −4.3 điểm. 85% cho phép tối đa 3 câu sai mà vẫn chặn được bản tệ hẳn (v1 = 4.3%); kèm điều kiện **không regression** so với baseline nên câu đã đúng không được phép sai lại |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)

300 request mỗi mức, `MOCK_SLOW_RATE=0.03` (mặc định). Nguồn: `reports/load_v2_c{1,10,50,100}.json`.

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.43 | 202.5 | 236.2 | 1685.0 | 0.67% (2 × 503) | ✅ PASS |
| 10 | 35.76 | 201.4 | 466.4 | 1724.4 | 0.33% (1 × 503) | ✅ PASS |
| 50 | 93.49 | 213.8 | 579.6 | 1742.8 | 1.00% (3 × 503) | ❌ FAIL (p95) |
| 100 | 39.32 | 906.6 | 5612.8 | 6132.1 | 2.00% (6 × 503) | ❌ FAIL (p95) |

**Nhận xét:** RPS **không tăng tuyến tính**. Theo Little's law, RPS ≈ concurrency / latency trung bình:
- c=1: 1 / 0.226 s ≈ 4.4 req/s, khớp với 4.43 đo được. Với 1 request tại một thời điểm, throughput chỉ bị giới hạn bởi latency.
- c=10: RPS tăng gần ×8 (35.76) vì agent async: trong lúc chờ LLM (`asyncio.sleep`), event loop phục vụ request khác. Latency gần như không đổi (p50 201 ms).
- c=50: RPS vẫn tăng (93.49) nhưng chỉ còn ×2.6 so với c=10. p95 lên 579.6 ms, **vượt SLO**: request bắt đầu xếp hàng.
- c=100: **RPS giảm xuống 39.32**, p50 906.6 ms, p95 5612.8 ms. Agent đã quá tải. Toàn bộ agent chạy trên **1 process, 1 event loop**: mỗi request phải parse JSON, cập nhật metrics, ghi log JSON, tất cả tranh nhau 1 nhân CPU. Thêm việc `load_test.py` chạy cùng máy cũng tốn CPU. Khi quá tải, request xếp hàng dài nên latency tăng vọt và throughput tụt. Muốn tăng nữa phải **scale ngang** (thêm worker/instance), không phải tăng concurrency.

Kết luận: với 1 process, điểm làm việc an toàn để giữ p95 < 500 ms là khoảng **concurrency ≤ 10–30**.

### 2.2 Tail latency

`--requests 300 --concurrency 20`, khởi động lại v2 với từng giá trị. Nguồn: `reports/load_v2_slow_{0,0.03,0.1}.json`.

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 214.5 | 437.7 | 475.8 |
| 0.03 (mặc định) | 231.1 | 443.7 | 1665.1 |
| 0.1 | 340.5 | 1677.8 | 1748.4 |

(p50 gần như không đổi: 203.3 / 198.1 / 198.4 ms. Max: 479.9 / 1716.9 / 1970.1 ms.)

**Nhận xét:**
- **p99 nhạy nhất**: chỉ 3% request chậm đã đẩy p99 từ 475.8 lên **1665.1 ms (×3.5)**, trong khi avg chỉ tăng 16.6 ms (+7.7%). Khi 10% request chậm (vượt quá 5%), cả **p95** cũng nhảy lên 1677.8 ms, còn avg mới tăng 126 ms (+59%).
- Average **che giấu** nhóm người dùng bị chậm: 3% request chờ ~1.7 s bị "pha loãng" bởi 97% request nhanh. Ở `0.03`, avg 231 ms trông rất ổn, nhưng cứ 100 người thì có ~3 người chờ gần 2 giây.
- Vì vậy SLO đặt theo **percentile (p95/p99)**. Percentile nói thẳng "x% người dùng nhận phản hồi nhanh hơn ngưỡng", còn average có thể ổn ngay cả khi một nhóm người dùng có trải nghiệm rất tệ.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

Nguồn: `reports/eval_v1.json`, `reports/eval_v2.json` (baseline v1), `reports/eval_v2_fixed.json` (baseline v2, `--min-accuracy 1.0`).

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 91.3% (21/23) | 100.0% (23/23) |
| Avg tokens / câu | 56.4 | 109.0 | 113.8 |
| Tổng cost (USD) | 0.000394 | 0.000759 | 0.000792 |
| Cost / câu đúng (USD) | 0.00039435 | 0.00003616 | 0.00003442 |
| Quality gate | FAIL (accuracy < 80%) | PASS (≥ 80%, 0 regression so với v1) | PASS (≥ 100%, 0 regression so với v2) |

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `scaling-03` (Kubernetes HPA scale dựa trên chỉ số nào?) | `cpu`, `memory`, `custom metrics` | Retrieval không tìm được tài liệu nào nên agent trả câu chung chung. Thêm entry vào `KNOWLEDGE_BASE` với trigger `["hpa", "kubernetes", "autoscal"]` và nội dung về HPA scale theo CPU, memory hoặc custom metrics (RPS, độ dài hàng đợi) |
| `security-02` (JWT khác gì API key?) | `token`, `hết hạn`, `chữ ký` | Thêm entry trigger `["jwt", "json web token"]`: API key là chuỗi cố định không tự hết hạn; JWT là token có chữ ký số, chứa user và thời điểm hết hạn, server xác minh chữ ký không cần tra DB |

Không sửa `golden_dataset.jsonl`. Kiểm tra regression: trigger mới chỉ chứa từ khoá không xuất hiện trong 21 câu còn lại, nên retrieval của chúng không đổi. Eval cuối báo `Câu bị regression : 0`, exit code 0. Cùng bản sửa đã chép sang `production/utils/mock_llm.py` để stack Docker ở Bước 2 cũng đạt 100%.

**Quality gate trong CI:** `.github/workflows/eval.yml` chạy trên mọi PR đụng tới `develop/`. Job bật agent v2 với `MOCK_ERROR_RATE=0` (tránh fail ngẫu nhiên), rồi chạy `run_eval.py --min-accuracy 0.85 --baseline reports/eval_v2_fixed.json`. `run_eval.py` trả **exit code 1** khi accuracy dưới ngưỡng hoặc có câu regression, làm step fail, job fail, và PR bị chặn merge (khi bật branch protection "Require status checks"). Đã thử: chạy v1 với `--baseline reports/eval_v2.json` thì gate báo 20 câu regression, `exit code = 1`.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**
- **Chất lượng:** v2 (có retrieval/RAG) tăng accuracy từ 4.3% lên 91.3%, và 100% sau khi bổ sung tài liệu. Chất lượng phụ thuộc trực tiếp vào độ phủ của knowledge base: 2 câu sai đều do retrieval không tìm được tài liệu, không phải do model.
- **Chi phí:** v2 tốn **~1.9 lần token mỗi câu** (109.0 so với 56.4) vì prompt chứa thêm context, nên tổng cost gấp ~1.9 lần. Nhưng **cost / câu đúng rẻ hơn ~10.9 lần** (0.00003616 so với 0.00039435 USD). Trả lời sai thì rẻ cũng vô ích, nên đây là chỉ số đúng để so 2 phiên bản.
- **Latency:** v2 thêm bước retrieval (~20 ms) và prompt dài hơn (+0.2 ms/token). Trong eval, p50 của v2 cao hơn v1 khoảng 30 ms (206.1 so với 175.4 ms). Riêng p95 trong eval dao động mạnh (v1 1651.0 ms, v2 223.6 ms, v2 sau sửa 1716.1 ms) chỉ vì 23 mẫu quá ít: 1 request rơi vào nhóm "slow" 3% là p95 nhảy lên ~1.7 s. **Latency phải đo bằng load test, không phải bằng eval.**
- Kết luận: v2 đáng giá. Đổi khoảng 2 lần token và vài chục ms để có câu trả lời đúng gấp ~21 lần.

---

## 4. Observability stack (production)

Stack chạy bằng `docker compose -p k5-day14-observability ...` trong `production/`. Alert rules qua unit test: `promtool test rules /p/alert_rules_test.yml` → `SUCCESS`.

Hai sự cố gặp khi dựng stack (đã xử lý):
1. **Agent crash `ModuleNotFoundError: No module named 'uvicorn'`.** `production/Dockerfile` cài package bằng `pip install --user` rồi copy vào `/home/agent/.local`, nhưng user `agent` được tạo với home `/app` (`useradd -d /app`), nên Python tìm package ở `/app/.local`. Sửa bằng cách thêm `ENV PYTHONUSERBASE=/home/agent/.local`.
2. **`docker compose run locust` tự bật lại agent theo `deploy.replicas: 2`**, bỏ qua `--scale agent=1` trước đó (`run` khởi động cả các service phụ thuộc: locust → nginx → agent). Lần chạy "scale_1" đầu tiên vì vậy thực chất chạy 2 instance, đã bỏ kết quả đó. Các lần benchmark/thí nghiệm sau đều dùng `run --rm --no-deps locust ...` và kiểm tra `count(up{job="agent"} == 1)` trước khi chạy.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải: [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png)
- [x] Một trace chậm trong Jaeger: [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png)
- [x] Một trace lỗi trong Jaeger (có event `exception`): [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png)
- [x] Trang Alerts của Prometheus với **2 alert cùng firing** (`AgentHighErrorRate` + `AgentHighLatencyP95`): [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png)

**Đọc dashboard (Exercise 7.4)**, ảnh chụp lúc Locust 100 user đang tăng tải:

| Panel | Giá trị |
|---|---|
| Traffic | 59.5 req/s |
| Error rate | 0.94% (lỗi 503 từ LLM mock ~1%) |
| Latency p95 | 374.15 ms |
| Saturation (in-flight) | 14 |
| Instances up | 2 (ảnh chụp đúng lúc sự cố số 2 ở trên: compose đã tự bật thêm 1 agent) |
| LLM cost (1h) | $0.1301 |

1. **p95 và avg:** trong lần benchmark 1 instance, Locust đo POST `/ask` có avg 243.7 ms, p50 200 ms, p95 280 ms, p99 1700 ms (`scale_1_stats.csv`). Dashboard lúc chụp báo p95 374.15 ms, vì cửa sổ `rate(...[1m])` rơi vào giai đoạn ramp-up. avg và p50 gần nhau, nhưng p99 gấp ~7 lần avg: phần lớn request nhanh và đồng đều, còn một nhóm nhỏ (~3% "slow" của LLM) chậm ~1.7 s. Khoảng cách avg ↔ p95/p99 chính là **tail latency** mà average che mất.
2. **Requests/s theo instance:** với 3 instance, 6 request liên tiếp qua Nginx chia đều 2/2/2 (`X-Served-By` 172.18.0.4 / .8 / .9). Nginx round-robin chia đều vì các instance giống nhau và request có thời gian xử lý tương đương.
3. **Input tokens/s > output tokens/s:** prompt gồm system prompt + **tài liệu retrieve được (context RAG)** + câu hỏi (vd trace chậm: input 74, output 37 token), còn câu trả lời ngắn. Thí nghiệm E xác nhận: bỏ retrieval (v1) thì tổng tokens/s giảm từ 1863.7 xuống 1032.3.
4. **`curl http://localhost:8080/metrics` → 404:** `nginx.conf` có `location = /metrics { return 404; }`. `/metrics` lộ thông tin nội bộ (route, lưu lượng, version, chi phí), nên chỉ Prometheus được scrape **trực tiếp từng agent trong mạng Docker** (`dns_sd_configs` → `agent:8000`), không đi qua cổng public.

### 4.2 Thí nghiệm "phá hệ thống"

Tải nền: Locust headless 30 user (`run --rm --no-deps`), 3 instance agent. Mỗi lần đổi `.env` rồi `docker compose up -d --no-deps --scale agent=3 agent`. Thời gian đo bằng script hỏi `/api/v1/rules` mỗi 3–5 giây, tính từ lúc chạy lệnh đổi cấu hình (gồm cả thời gian restart container). A và B đổi **cùng lúc** (T0 = 10:11:03 UTC) để có 2 alert cùng firing trong một ảnh.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (giá trị 21.6%) | pending sau 31 s → **firing sau 96 s** (`for: 1m`) | Error rate (đỏ), Requests/s theo status code (503 tăng), LLM calls theo kết quả |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` (p95 = 1.90 s) | **firing sau 150 s** (`for: 2m`) | Latency p95, đồ thị p50/p95/p99: p95/p99 vọt lên ~1.9 s, p50 gần như không đổi |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` (cost 1.30 → 16.76 USD/giờ, ×~13) | pending sau 31 s → **firing sau 151 s** (`for: 2m`) | Cost burn rate (USD/giờ), LLM latency p95 theo model (large ≈ 0.50 s) |
| D | `docker pause` 1 instance | `AgentInstanceDown` (`up = 0` cho 172.18.0.9) | pending sau 7 s → **firing sau 39 s** (`for: 30s`) | Instances up (3 → 2), Requests/s theo instance. **Error rate vẫn xanh (1.16%)** dù Nginx trả **24 lỗi 504** trong 60 s pause |
| E | `AGENT_VERSION=v1` | **Không có** | — | Retrieval hit ratio (1.0 → không còn dữ liệu), Tokens/s (1863.7 → 1032.3), Phiên bản đang chạy (`agent_info` = v1). Error rate 0.8%, p95 0.199 s: hạ tầng hoàn toàn "khoẻ" |

Hai quan sát thêm:
- **Cost alert nhạy với lưu lượng chứ không chỉ với model:** khi benchmark 100 user (~60 req/s) bằng model *small*, `LLMCostBurnRateHigh` **đã firing** (3.13 USD/giờ > ngưỡng 2). Ngưỡng tuyệt đối $/giờ nên đặt theo ngân sách thật và đi kèm alert "cost / request" để phân biệt "nhiều traffic" với "mỗi request đắt hơn".
- Sau thí nghiệm C, alert cost còn firing thêm ~5 phút (đến 10:22:08) vì biểu thức dùng `rate(...[5m])`, nên alert cần thời gian để "quên". Lúc đang chạy E, alert này còn firing là do C, không phải do v1.

**Vì sao 504 của D không hiện trên dashboard:** request tới instance bị pause chờ tới khi Nginx hết `proxy_read_timeout`/connect timeout rồi trả 504. Agent chưa bao giờ xử lý xong request đó, nên không có dòng nào trong `http_requests_total` của agent. Metric chỉ thấy những gì *chính service đó* nhìn thấy. Cần giám sát ở tầng load balancer (metric/log của Nginx) và synthetic check từ bên ngoài.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Đây là sự cố **chất lượng**, không phải sự cố hạ tầng: latency, error, saturation đều tốt, nhưng câu trả lời sai (eval: v1 chỉ 4.3% so với v2 91.3%). Cách phát hiện:
1. **Chặn trước khi deploy:** quality gate trong CI/CD (`run_eval.py --min-accuracy ... --baseline ...`, exit code 1 → không cho deploy). Chính gate này đã bắt v1 với 20 câu regression.
2. **Metric "đại diện" cho chất lượng có alert:** retrieval hit ratio giảm mạnh, tokens/request giảm đột ngột, `agent_info{version}` đổi ngoài lịch release.
3. **Eval liên tục trên production:** chạy golden dataset định kỳ (synthetic probe) vào endpoint thật; lấy mẫu một phần traffic thật để chấm bằng LLM-as-judge; ghi nhận phản hồi người dùng (thumbs up/down, tỉ lệ hỏi lại). Đặt SLO và alert cho các chỉ số chất lượng này giống SLO latency.

### 4.3 Debug bằng trace

Trace chậm nhất (Jaeger, `POST /ask`, sắp xếp Longest First, trong lần benchmark 100 user):

- `trace_id`: `19374723a63b0bf6551d02000351cb10`
- Tổng thời gian: **2083.1 ms**
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small` (1696.0 ms, 81.4%)**, tức là gọi LLM, không phải code của agent. Tokens: input 74, output 37.
- Các span khác: `agent.run` 1783.5 ms (bắt đầu ở +297.1 ms), `agent.retrieve_context` 87.2 ms. Khoảng ~297 ms trước `agent.run` và retrieval chậm hơn bình thường (~20 ms) cho thấy request còn phải xếp hàng trong event loop: lúc đó container (giới hạn 0.5 CPU) đang chịu tải ramp-up.
- Dòng log tương ứng (`docker compose -p k5-day14-observability logs agent | grep 19374723a63b0bf6551d02000351cb10`):

```json
{"ts": "2026-11-26T10:00:43.288043+00:00", "level": "INFO", "service": "observable-agent", "instance": "6d09c61fc332", "event": "llm_call", "trace_id": "19374723a63b0bf6551d02000351cb10", "span_id": "2f9547f8ab31f8f7", "request_id": "f782d0bdddc7", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 74, "output_tokens": 37, "cost_usd": 3.3e-05, "llm_ms": 1693.8}
{"ts": "2026-11-26T10:00:43.289841+00:00", "level": "INFO", "service": "observable-agent", "instance": "6d09c61fc332", "event": "http_request", "trace_id": "19374723a63b0bf6551d02000351cb10", "span_id": "2f9547f8ab31f8f7", "request_id": "f782d0bdddc7", "method": "POST", "route": "/ask", "status": 200, "ms": 2082.2}
```

Trace lỗi (`error=true`, ảnh `jaeger-error-trace.png`): `trace_id` `d3f8f8b4fa61817a6a4b6724864a5dbf`, 869 ms, `POST /ask` 503. Span `chat mock-llm-small` (843 ms) có `error.type = upstream_unavailable` và event `exception`: `exception.message = Upstream LLM error: 503 Service Unavailable (mock)`. Log tương ứng:

```json
{"ts": "2026-11-26T10:05:55.976113+00:00", "level": "INFO", "service": "observable-agent", "instance": "42a0f3267c68", "event": "llm_error", "trace_id": "d3f8f8b4fa61817a6a4b6724864a5dbf", "span_id": "99f0ed3e1d059d5c", "request_id": "3e2d8b7be609", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Nguồn: `benchmark-results/scale_1_stats.csv`, `scale_3_stats.csv`, dòng `POST /ask` (dòng Aggregated gồm cả `/health`: 65.56 và 66.06 req/s).

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 59.59 | 200 | 280 | 1700 | 67 / 7120 (0.94%) |
| 3 | 60.05 | 190 | 230 | 1700 | 74 / 7173 (1.03%) |

**Giải thích kết quả:** Scale lên 3 instance **gần như không đổi RPS** (+0.8%) và **p99 giữ nguyên 1700 ms**. p95 giảm nhẹ (280 → 230 ms) và p50 giảm 10 ms.
- **RPS bị giới hạn bởi bộ tạo tải, không phải bởi agent.** Locust là "closed model": 100 user, mỗi user chờ 0.5–2 s (trung bình 1.25 s) giữa các lần gọi. Throughput tối đa ≈ 100 / (1.25 s + ~0.24 s) ≈ 67 req/s, và cả 2 lần đều đạt ~66 req/s (Aggregated). Agent 1 instance chưa bão hoà (in-flight ~14, async nên phục vụ được nhiều request cùng lúc), nên thêm instance không làm tăng RPS.
- **p95 cải thiện nhẹ** vì mỗi container bị giới hạn 0.5 CPU. Chia tải ra 3 container thì mỗi event loop ít bận hơn, nên ít thời gian xếp hàng hơn (như ~297 ms chờ thấy trong trace chậm ở 4.3).
- **Nút thắt của tail latency là LLM upstream:** 3% request LLM chậm thêm ~1.5 s (`MOCK_SLOW_RATE=0.03`). Panel "LLM latency" và trace (span `chat` chiếm 81% thời gian) cho thấy thời gian nằm trong lời gọi LLM. Thêm instance agent không làm LLM nhanh hơn, nên p99 vẫn ~1.7 s. Muốn giảm tail phải xử lý ở tầng LLM: timeout + retry/hedging, cache câu trả lời, model nhanh hơn, streaming để giảm thời gian tới token đầu.

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Average nói dối, percentile thì không.** Chỉ 3% request chậm làm p99 tăng ×3.5 mà avg tăng chưa tới 8%. SLO và alert phải đặt theo p95/p99, và load test phải có đủ mẫu (eval 23 câu không đo được latency).
  2. **Khoẻ về hạ tầng ≠ trả lời đúng.** Đổi sang v1 làm accuracy rơi từ 91.3% xuống 4.3% mà không alert nào kêu. Chất lượng cần đo riêng bằng eval + quality gate trong CI, và giám sát các tín hiệu như retrieval hit ratio, tokens/request.
  3. **Mỗi lớp chỉ thấy những gì nó nhìn thấy, và benchmark phải kiểm chứng chính nó.** 504 ở Nginx không hiện trên metric agent; Prometheus `rate[5m]` làm alert còn firing sau khi đã hết sự cố; `docker compose run` âm thầm đổi số instance làm sai benchmark. Metrics → trace → log (nối bằng `trace_id`) giúp tìm ra đúng span gây chậm, nhưng luôn phải kiểm tra lại điều kiện thí nghiệm (`count(up)`, version, `.env`) trước khi tin vào con số.
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - **Alert theo error budget burn rate** (multi-window, vd 5m + 1h) thay cho ngưỡng cố định, để vừa bắt nhanh sự cố lớn vừa không báo động vì spike ngắn.
  - **Metric/alert ở tầng Nginx/LB và synthetic check từ bên ngoài** (tỉ lệ 5xx/504 của Nginx, blackbox probe `/health` và một `/ask` mẫu), để bắt các lỗi agent không tự thấy như thí nghiệm D.
  - **Saturation:** alert khi `agent_in_flight_requests` hoặc độ trễ hàng đợi cao kéo dài, kèm CPU/memory container, để biết khi nào cần scale.
  - **Chất lượng và chi phí:** retrieval hit ratio giảm, tokens/request và **cost/request** bất thường, cost theo ngày so với ngân sách; eval định kỳ trên production (golden set + LLM-as-judge trên mẫu traffic) có SLO accuracy riêng.
  - **LLM upstream:** latency p99 và error rate theo provider/model, timeout, số lần retry, để phân biệt lỗi của mình với lỗi của nhà cung cấp.
  - Gửi alert qua **Alertmanager** (Slack/PagerDuty) có phân mức `critical`/`warning` và runbook link trong annotation.

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

### 4.1 Screenshot

- [ ] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals)
- [ ] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian)
- [ ] Một trace lỗi trong Jaeger (có event `exception`)
- [ ] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing**

### 4.2 Thí nghiệm "phá hệ thống"

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | | | |
| B | `MOCK_SLOW_RATE=0.3` | | | |
| C | `LLM_MODEL=mock-llm-large` | | | |
| D | `docker pause` 1 instance | | | |
| E | `AGENT_VERSION=v1` | | | |

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

...

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được:

- `trace_id`: ...
- Tổng thời gian: ... ms
- Span chiếm nhiều thời gian nhất: ... (... ms, ...%)
- Dòng log tương ứng (grep theo `trace_id`):

```json

```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | | | | | |
| 3 | | | | | |

**Giải thích kết quả:** Scale có cải thiện p95 không? Nút thắt nằm ở đâu?

...

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. ...
  2. ...
  3. ...
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?

...

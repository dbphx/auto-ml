# Auto ML trainer

Service tự động train model từ dữ liệu và preprocessing trong chính repo này.

Với dataset WAF hiện tại (`label` là 0/1), pipeline chạy hai ứng viên:

- Random Forest classifier
- Logistic Regression classifier (mô hình tuyến tính; phù hợp hơn `LinearRegression` cho nhãn 0/1)

Nếu job khai báo `task=regression`, ứng viên tuyến tính sẽ là `LinearRegression` thật và RF sẽ chuyển sang `RandomForestRegressor`.

## Chạy

```bash
cd auto-ml
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m auto_ml.server
```

### Chạy server bằng Docker

```bash
docker compose up --build -d
```

Compose mở server tại `http://localhost:8080`, mount `./data` read-only vào `/app/data` và lưu kết quả trong volume `auto-ml-output`. Chế độ Codex nội bộ dùng phiên ChatGPT CLI đã đăng nhập trên host qua `~/.codex/auth.json`; Compose đồng bộ file auth vào volume riêng trong container. Copy `.env.example` thành `.env` để cấu hình LLM hoặc AWS. Image đã cài Codex CLI.

Gửi hook để khởi chạy một job (đường dẫn là đường dẫn **bên trong container**):

```bash
curl -X POST http://localhost:8080/hook \
  -H 'Content-Type: application/json' \
  -d '{"mode":"codex","input-path":{"normal":"/app/data/normal.txt","attack":"/app/data/attack.txt"},"output-path":"/app/output"}'
```

`mode` chọn `llm` (planner hiện tại) hoặc `codex` (Codex CLI tự tune). Trong mode `codex`, `llm` có thể chọn `external` hoặc `internal`; nếu bỏ qua thì mặc định là `internal`, dùng endpoint/auth mặc định của Codex CLI. Chế độ `external` dùng URL/key từ `VLLM_*` hoặc `LLM_*` trong env. Model luôn lấy từ `.env`: `VLLM_MODEL`/`LLM_MODEL` cho external, hoặc `CODEX_MODEL` cho internal (nếu bỏ trống thì Codex CLI dùng model mặc định). Hook mặc định chạy tối đa 10 trials cho từng model trong một round và chọn trial có validation accuracy cao nhất. Validation đạt 90% không làm dừng search; `target_met` cuối cùng được quyết định bởi category test với ngưỡng 90%. Có thể ghi đè `target_accuracy`, `max_trials`, `category_target_accuracy` trong JSON. `input-path` nhận đường dẫn tới thư mục/CSV, một mảng `[normal-path, attack-path]`, hoặc object `{ "normal": "...", "attack": "..." }`. Các trường `normal-path` và `attack-path` riêng lẻ vẫn được hỗ trợ. `output-path` là thư mục gốc artifacts; mỗi job tạo thư mục con theo `job_id`. URL và API key luôn lấy từ môi trường container. Hook trả HTTP `202` cùng `job_id`; dùng `GET /jobs/<job_id>` để xem trạng thái.

Quản lý các task bằng API:

```bash
# Liệt kê task, chỉ lọc task đang chạy
curl 'http://localhost:8080/tasks?status=running'

# Xem chi tiết task và report nếu đã xong
curl 'http://localhost:8080/tasks/<job_id>'

# Xem process hiện tại và lịch sử các bước
curl 'http://localhost:8080/tasks/<job_id>/process'
```

Trạng thái gồm `queued`, `running`, `retrying`, `completed` hoặc `failed`. Lịch sử process giữ tối đa 500 event gần nhất; `/jobs/<job_id>` vẫn được hỗ trợ.

Mặc định dataset là cặp `data/normal.txt` và `data/attack.txt`; output nằm ở `output/`.

Dataset có thể là S3 prefix chứa `normal.txt` và `attack.txt`. Nếu S3 không truy cập được, job tự fallback về local `data`.

## Trigger một job

```bash
curl -X POST http://127.0.0.1:8080/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "dataset_path": "data",
    "target_metrics": {"accuracy": 0.98, "f1": 0.97},
    "max_trials": 12,
    "models": ["random_forest", "linear"]
  }'
```

Theo dõi job:

```bash
curl http://127.0.0.1:8080/jobs/<job_id>
```

Hoặc chạy không cần server:

```bash
python -m auto_ml.cli --dataset data \
  --target-accuracy 0.98 --max-trials 12
```

Train từ S3 và upload artifact lên S3:

```bash
python -m auto_ml.cli \
  --dataset s3://my-bucket/datasets/waf \
  --fallback-dataset data \
  --output output \
  --output-s3 s3://my-bucket/models/waf \
  --target-accuracy 0.98 \
  --max-trials 12
```

S3 dùng credential chuẩn của boto3/AWS (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, profile hoặc IAM role). Nếu upload lỗi, toàn bộ model và report vẫn giữ ở `output/<job_id>/`.

CLI tự động làm đủ các bước:

1. Đọc `normal.txt` và `attack.txt`.
2. Nhờ LLM lập kế hoạch tuning nếu đã cấu hình `LLM_API_URL`/`LLM_API_KEY`; nếu không sẽ dùng search space deterministic.
3. Fine-tune Random Forest và Logistic Regression.
4. Refit model tốt nhất trên toàn bộ dữ liệu.
5. Chạy category regression RAW + ENCODED trên cả hai model với `attack_fields.txt` và `normal_fields.txt`.

### Dùng hai file normal và attack tùy chọn

Có thể chỉ định riêng từng file; mỗi đường dẫn là file local hoặc một S3 object (`s3://bucket/key`). File attack có thể đặt tên `malicious.txt`; nội dung cần theo định dạng category đánh số giống `data/attack.txt`.

```bash
python -m auto_ml.cli \
  --normal-path /path/to/normal.txt \
  --attack-path /path/to/malicious.txt
```

`--path1` và `--path2` là alias lần lượt cho `--normal-path` và `--attack-path`. Ví dụ với S3:

```bash
python -m auto_ml.cli \
  --path1 s3://my-bucket/datasets/normal.txt \
  --path2 s3://my-bucket/datasets/malicious.txt
```

API `POST /jobs` nhận cùng các trường `normal_path` và `attack_path` (hoặc alias `path1` và `path2`) trong JSON. Nếu chỉ truyền một trong hai đường dẫn, job báo lỗi; nếu truyền cả hai, cặp file này được dùng làm dữ liệu huấn luyện thay cho `--dataset`.

Trong lúc train, các model/trial được ghi vào thư mục tạm. Khi hoàn tất, thư mục tạm bị xóa và `artifacts/<job_id>/` chỉ chứa artifact của model thắng cuộc cùng `report.json`:

- `model.joblib`
- `vectorizer.joblib`
- `category_results.json` (khi có category test)
- `report.json`

Sau khi category test chạy xong, trainer chọn model có `category_tests.<model>.accuracy` cao nhất. `target_met` kiểm tra ngưỡng trên model thắng cuộc; `validation_target_met` vẫn báo riêng kết quả validation của model đó. Ngưỡng category đổi bằng `--category-target-accuracy`.

LLM là lớp lập kế hoạch tùy chọn. Có thể dùng `LLM_API_URL`, `LLM_API_KEY`, `LLM_MODEL` hoặc các biến VLLM tương ứng:

```bash
export VLLM_BASE_URL="https://your-vllm-host"
export VLLM_MODEL="your-model"
export VLLM_API_KEY="..."
```

Service sẽ gọi OpenAI-compatible endpoint `/v1/chat/completions`. Có thể dùng `--require-llm` để job dừng ngay nếu LLM không gọi được, thay vì fallback deterministic:

```bash
python -m auto_ml.cli --require-llm --max-trials 12
```

Mặc định mỗi request LLM chờ tối đa 90 giây, retry tối đa 2 lần khi timeout/lỗi tạm thời, và dành 2048 token cho phản hồi. Nếu output bị cắt do hết token, retry sẽ tăng giới hạn đến 8192. Có thể đổi bằng `--llm-timeout-seconds`, `--llm-retries`, `--llm-max-tokens` hoặc các biến môi trường tương ứng.

Nếu category test chưa đạt ngưỡng, CLI có thể lặp round khi đặt `--max-rounds` lớn hơn 1. Mặc định hiện tại là 1 round để không lặp training round.

LLM nhận kết quả train sau mỗi round và chọn lại hyperparameter trong bounded search space của project. Trainer tiếp tục kiểm tra đề xuất trước khi chạy; LLM không cập nhật weight trực tiếp, thực thi code hay sửa dataset. Để bắt buộc dùng LLM và dừng job nếu endpoint lỗi, bật `--require-llm`.

### Chọn agent cho quá trình training

Có hai mode:

- `llm` (mặc định): giữ planner hiện tại, gọi endpoint OpenAI-compatible để lập kế hoạch cho từng round; nếu category test chưa đạt, gửi feedback để lập kế hoạch round kế tiếp.
- `codex`: gọi Codex CLI sau mỗi trial để xem metrics và chọn hyperparameter cho trial kế tiếp. Codex chỉ trả lời trong search space cho phép; trainer xác thực lại từng giá trị. Codex chạy trong sandbox read-only và không sửa code, dataset hoặc model artifacts. Cần cài và xác thực Codex CLI trên máy/container chạy trainer.

Trong mode `codex`, mặc định dùng Codex provider/auth/model nội bộ. Chọn `"llm":"external"` để dùng URL/key/model từ `VLLM_*` (hoặc `LLM_*`); endpoint cần hỗ trợ `POST /v1/responses`. Endpoint chỉ hỗ trợ `/v1/chat/completions` dùng được với mode `llm`, nhưng không dùng trực tiếp được với Codex CLI. Codex CLI hiện yêu cầu custom provider dùng Responses API và `wire_api = "responses"` ([tài liệu cấu hình](https://developers.openai.com/codex/config-reference/)).

Chọn mode trong CLI:

```bash
python -m auto_ml.cli --agent-mode llm
python -m auto_ml.cli --agent-mode codex
```

Hoặc đặt `TRAINING_AGENT_MODE=codex`. Có thể cấu hình timeout mỗi quyết định bằng `CODEX_TIMEOUT_SECONDS` hoặc `--codex-timeout-seconds`. Khi chạy mode Codex, mỗi model có tối đa `max_trials - 1` lần gọi agent vì trial đầu dùng cấu hình khởi tạo; search chạy hết số trial đã đặt rồi mới đánh giá category target.

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

Compose mở server tại `http://localhost:18080`, mount `./data` read-only vào `/app/data` và lưu kết quả trong volume `auto-ml-output`. Codex nội bộ dùng phiên ChatGPT CLI đã đăng nhập trên host qua `~/.codex/auth.json`; Compose mount thư mục `.codex` (nếu file auth chưa có thì service vẫn khởi động, nhưng Codex cần được đăng nhập/cấu hình riêng). Copy `.env.example` thành `.env` để cấu hình Codex hoặc AWS. Image đã cài Codex CLI.

Mở `http://localhost:18080/` để vào trang management React: xem danh sách/trạng thái job, tiến trình và lịch sử cập nhật, hoặc tạo job training mới. React gọi trực tiếp API Flask; trang tự làm mới mỗi 3 giây. Job và lịch sử được lưu trong SQLite tại `/app/output/jobs.sqlite3` trên volume bền vững `auto-ml-output`, nên vẫn còn sau khi container khởi động lại. Job đang chạy dở lúc server dừng được đánh dấu lỗi với thông báo gián đoạn.

Tài liệu Swagger cho các endpoint có tại `http://localhost:18080/api`; OpenAPI 3.1.0 JSON để import vào công cụ khác tại `/openapi.json`.

Trong management, có thể xóa từng job hoặc xóa toàn bộ lịch sử đã kết thúc. Chỉ job `completed` hoặc `failed` được xóa; job đang chờ/chạy sẽ được giữ lại.

Server chạy tối đa 4 job training đồng thời theo mặc định; job vượt giới hạn ở trạng thái `queued` đến lượt. Có thể đổi giới hạn bằng `AUTO_ML_WORKERS` trong `.env`.

### Đăng nhập và token

Mọi REST API (trừ `/health`, `POST /auth/login`, trang web và Swagger) yêu cầu header `Authorization: Bearer <token>`; thiếu/sai/hết hạn token trả `401`. Tài khoản cấu hình bằng `AUTO_ML_USERNAME` (mặc định `admin`) và `AUTO_ML_PASSWORD`; nếu không đặt mật khẩu, server tự sinh mật khẩu ngẫu nhiên và in ra log khi khởi động. Token được ký bằng `AUTO_ML_SECRET_KEY` (không đặt thì sinh ngẫu nhiên, token mất hiệu lực sau mỗi lần restart) và hết hạn sau `AUTO_ML_TOKEN_TTL_SECONDS` giây (mặc định 12 giờ). Trang management hiện form đăng nhập; trên Swagger dùng nút **Authorize**.

```bash
TOKEN=$(curl -s -X POST http://localhost:18080/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"change-me"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
```

Gửi hook để khởi chạy một job (đường dẫn là đường dẫn **bên trong container**):

```bash
curl -X POST http://localhost:18080/hook \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"mode":"codex","input-path":{"normal":"/app/data/normal.txt","attack":"/app/data/attack.txt"},"output-path":"/app/output"}'
```

`mode` là `codex`; `/hook` từ chối mode khác. `codex_source` chọn `internal` hoặc `external` (mặc định `internal`). Internal dùng đăng nhập và model mặc định của Codex CLI. External dùng URL/key/model từ `VLLM_*` hoặc các alias `LLM_*` trong môi trường; endpoint phải hỗ trợ OpenAI Responses API (`/v1/responses`). Với internal, có thể đặt `CODEX_MODEL` hoặc để Codex CLI dùng model mặc định. Hook chạy tối đa 10 trials cho từng model trong một round và chọn trial có validation accuracy cao nhất. `category_target_accuracy` là ngưỡng đánh giá category test; có thể ghi đè cùng `max_trials` trong JSON. `input-path` nhận đường dẫn tới thư mục/CSV, một mảng `[normal-path, attack-path]`, hoặc object `{ "normal": "...", "attack": "..." }`. Các trường `normal-path` và `attack-path` riêng lẻ vẫn được hỗ trợ. `output-path` là thư mục gốc artifacts; mỗi job tạo thư mục con theo `job_id`. URL và API key luôn lấy từ môi trường container. Hook trả HTTP `202` cùng `job_id`; dùng `GET /jobs/<job_id>` để xem trạng thái.

Quản lý các task bằng API:

```bash
# Liệt kê task, chỉ lọc task đang chạy
curl -H "Authorization: Bearer $TOKEN" 'http://localhost:18080/tasks?status=running'

# Xem chi tiết task và report nếu đã xong
curl -H "Authorization: Bearer $TOKEN" 'http://localhost:18080/tasks/<job_id>'

# Xem process hiện tại và lịch sử các bước
curl -H "Authorization: Bearer $TOKEN" 'http://localhost:18080/tasks/<job_id>/process'
```

Trạng thái gồm `queued`, `running`, `retrying`, `completed` hoặc `failed`. Lịch sử process giữ tối đa 500 event gần nhất; `/jobs/<job_id>` vẫn được hỗ trợ.

Mặc định dataset là cặp `data/normal.txt` và `data/attack.txt`; output nằm ở `output/`.

Dataset có thể là S3 prefix chứa `normal.txt` và `attack.txt`. Nếu S3 không truy cập được, job tự fallback về local `data`.

Trong giao diện, mở **S3** để nhập và lưu access key, secret key, region và endpoint tùy chọn. Credential được lưu trong volume output tại `s3_settings.json` với quyền file `0600`; API chỉ trả trạng thái và access key đã che, không trả secret. Khi tạo job, chọn nguồn **S3**, chọn bucket rồi duyệt tới prefix có `normal.txt` và `attack.txt`, hoặc gán hai object bất kỳ lần lượt làm Normal và Attack. Nội dung hai file vẫn cần theo định dạng category text của trainer. Có thể chọn thêm prefix S3 để lưu artifacts; mỗi job tạo một thư mục con theo `job_id`. Job chọn S3 làm input sẽ báo lỗi nếu không truy cập được thay vì âm thầm dùng dataset local.

## Trigger một job

```bash
curl -X POST http://127.0.0.1:8080/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "dataset_path": "data",
    "category_target_accuracy": 0.9,
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
  --category-target-accuracy 0.98 --max-trials 12
```

Train từ S3 và upload artifact lên S3:

```bash
python -m auto_ml.cli \
  --dataset s3://my-bucket/datasets/waf \
  --fallback-dataset data \
  --output output \
  --output-s3 s3://my-bucket/models/waf \
  --category-target-accuracy 0.98 \
  --max-trials 12
```

S3 dùng credential chuẩn của boto3/AWS (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, profile hoặc IAM role). Nếu upload lỗi, toàn bộ model và report vẫn giữ ở `output/<job_id>/`.

CLI tự động làm đủ các bước:

1. Đọc `normal.txt` và `attack.txt`.
2. Dùng Codex CLI đề xuất hyperparameter trong search space giới hạn của project sau mỗi trial.
3. Fine-tune Random Forest và Logistic Regression.
4. Refit model tốt nhất trên toàn bộ dữ liệu.
5. Chạy category tests trên dữ liệu RAW và URL-encoded cho cả hai model với `attack_fields.txt` và `normal_fields.txt`.

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

Sau khi category test chạy xong, trainer chọn model có `category_tests.<model>.accuracy` cao nhất. `target_met` kiểm tra `category_target_accuracy` trên model thắng cuộc. Ngưỡng đổi bằng `--category-target-accuracy`.

Nếu category test chưa đạt ngưỡng, CLI có thể lặp round khi đặt `--max-rounds` lớn hơn 1. Mặc định hiện tại là 1 round để không lặp training round.

### Codex tuning

Codex CLI xem metrics sau mỗi trial và chọn hyperparameter tiếp theo trong search space giới hạn. Trainer xác thực lại các giá trị. Codex CLI chạy trong sandbox read-only; cần cài và xác thực Codex CLI trên máy/container chạy trainer. Internal dùng provider/auth mặc định. External dùng endpoint Responses API (`/v1/responses`) từ `VLLM_*` hoặc `LLM_*` ([tài liệu cấu hình Codex](https://developers.openai.com/codex/config-reference/)).

Chạy training qua CLI:

```bash
python -m auto_ml.cli --max-trials 12
```

Có thể cấu hình timeout mỗi quyết định bằng `CODEX_TIMEOUT_SECONDS` hoặc `--codex-timeout-seconds`. Mỗi model có tối đa `max_trials - 1` lần gọi Codex vì trial đầu dùng cấu hình khởi tạo; search chạy hết số trial đã đặt rồi mới đánh giá category target.

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

Kết quả nằm trong `artifacts/<job_id>/report.json`, cùng các file:

- `random_forest/model.joblib`, `random_forest/vectorizer.joblib`, `random_forest/category_results.json`
- `linear/model.joblib`, `linear/vectorizer.joblib`, `linear/category_results.json`

`validation_target_met` áp dụng cho validation search. Mỗi model được chấp nhận độc lập nếu `category_tests.<model>.accuracy >= 0.90`; ngưỡng này đổi bằng `--category-target-accuracy`.

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

Nếu category test chưa đạt ngưỡng, job tự chạy round tuning tiếp theo với seed khác, tối đa 10 round mặc định. Đổi giới hạn bằng `--max-rounds`.

LLM lập kế hoạch/chọn hyperparameter cho model ML; nó không cập nhật weight của Random Forest/Linear Regression. Mọi đề xuất đều được validate và LLM không được phép tự thực thi code hay sửa dataset.

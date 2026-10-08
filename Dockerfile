FROM node:22-slim AS codex-cli

RUN npm install --global @openai/codex@0.130.0

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OUTPUT_DIR=/app/output

WORKDIR /app

COPY requirements.txt .
COPY --from=codex-cli /usr/local/bin/node /usr/local/bin/node
COPY --from=codex-cli /usr/local/bin/codex /usr/local/bin/codex
COPY --from=codex-cli /usr/local/lib/node_modules/@openai /usr/local/lib/node_modules/@openai
RUN pip install --no-cache-dir -r requirements.txt
RUN ln -sf /usr/local/lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex

COPY auto_ml ./auto_ml
COPY src ./src
COPY data ./data
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/output \
    && chmod 755 /usr/local/bin/docker-entrypoint \
    && chown -R appuser:appuser /app

USER appuser
EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/docker-entrypoint"]
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--access-logfile", "-", "auto_ml.server:app"]

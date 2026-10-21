FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY stock_engine/ ./stock_engine/

ENV PORT=8080
EXPOSE 8080

# 4 workers handles concurrent Apps Script calls from multiple ERP users
CMD exec uvicorn stock_engine.main:app \
        --host 0.0.0.0 \
        --port ${PORT} \
        --workers 4 \
        --log-level info

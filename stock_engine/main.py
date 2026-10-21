"""
Evergreen Purchase ERP — Stock API entry point.

Deployment options
------------------
1. Cloud Run (recommended):
       uvicorn stock_engine.main:app --host 0.0.0.0 --port 8080 --workers 4

2. Cloud Functions HTTP trigger (set FUNCTION_TARGET=http_handler):
       export FUNCTION_TARGET=http_handler

3. Local dev:
       uvicorn stock_engine.main:app --reload

Environment variables
---------------------
BQ_PROJECT_ID     BigQuery project (default: productionreport-467412)
BQ_DATASET_ID     BigQuery dataset (default: PurchaseERP)
BQ_LOCATION       BigQuery region   (default: asia-south1)

Architecture
------------
- Apps Script (or any HTTP client) calls this API to write stock movements
- API streams rows directly into ERP_TXN_stock_ledger in BigQuery
- ERP_StockCalc materialized view auto-refreshes every 5 minutes
- GET /api/v1/stock always returns up-to-date stock; no trigger needed
"""
import logging
import os
import sys

from contextlib import asynccontextmanager

from google.cloud import bigquery

from . import config
from .api import app
from .setup_tables import setup_all


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)

log = logging.getLogger(__name__)


@app.on_event("startup")
def on_startup() -> None:
    client = bigquery.Client(
        project=config.PROJECT_ID,
        location=config.BQ_LOCATION,
    )
    setup_all(client)
    log.info("Stock API ready — BigQuery project: %s / dataset: %s",
             config.PROJECT_ID, config.DATASET_ID)


# ── Cloud Functions HTTP shim ─────────────────────────────────────────────

def http_handler(request):
    """
    Thin wrapper so this module can be deployed as a Cloud Function.
    Converts the functions-framework Request into an ASGI call.
    """
    import asyncio
    from starlette.testclient import TestClient  # only imported in CF context

    client = TestClient(app)
    method  = request.method
    path    = request.path
    headers = dict(request.headers)
    data    = request.get_data()

    resp = client.request(method, path, headers=headers, content=data)
    return (resp.content, resp.status_code, dict(resp.headers))


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("stock_engine.main:app", host="0.0.0.0", port=port, reload=False)

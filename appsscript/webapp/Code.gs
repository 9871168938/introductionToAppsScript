/**
 * Evergreen Purchase ERP — Stock Web App
 *
 * Serves the responsive HTML UI and proxies all requests to the
 * Cloud Run Stock API.  The Cloud Run URL stays server-side (never
 * exposed to the browser).
 *
 * Setup
 * -----
 * 1. Open Script Properties (Project Settings → Script Properties)
 * 2. Add property: STOCK_API_URL = https://<your-cloud-run-url>
 * 3. Deploy as Web App: Execute as = Me, Access = Anyone in org
 */

// ── Serve the web app ─────────────────────────────────────────────────────

function doGet() {
  return HtmlService.createHtmlOutputFromFile('Index')
    .setTitle('Evergreen ERP — Stock')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1, maximum-scale=1')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

// ── Internal helper ───────────────────────────────────────────────────────

function _apiUrl() {
  return PropertiesService.getScriptProperties()
    .getProperty('STOCK_API_URL') || '';
}

function _fetch(method, path, body) {
  const opts = {
    method: method.toLowerCase(),
    muteHttpExceptions: true,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body) opts.payload = JSON.stringify(body);
  try {
    const resp = UrlFetchApp.fetch(_apiUrl() + path, opts);
    const code = resp.getResponseCode();
    const text = resp.getContentText();
    if (code >= 400) return { error: true, status: code, message: text };
    return JSON.parse(text);
  } catch (e) {
    return { error: true, message: e.message };
  }
}

// ── API functions called from the browser via google.script.run ───────────

function getStock(branchId, itemCode) {
  const qs = [];
  if (branchId) qs.push('branch_id=' + encodeURIComponent(branchId));
  if (itemCode)  qs.push('item_code='  + encodeURIComponent(itemCode));
  return _fetch('GET', '/api/v1/stock' + (qs.length ? '?' + qs.join('&') : ''));
}

function postTransaction(payload) {
  payload.created_by = Session.getActiveUser().getEmail();
  return _fetch('POST', '/api/v1/transaction', payload);
}

function postTransactionBatch(payloads) {
  const email = Session.getActiveUser().getEmail();
  payloads.forEach(p => { p.created_by = email; });
  return _fetch('POST', '/api/v1/transactions/batch', payloads);
}

function getLedger(branchId, itemCode, limit) {
  const qs = ['limit=' + (limit || 300)];
  if (branchId) qs.push('branch_id=' + encodeURIComponent(branchId));
  if (itemCode)  qs.push('item_code='  + encodeURIComponent(itemCode));
  return _fetch('GET', '/api/v1/ledger?' + qs.join('&'));
}

function getExceptions() {
  return _fetch('GET', '/api/v1/exceptions');
}

function getCurrentUserEmail() {
  return Session.getActiveUser().getEmail();
}

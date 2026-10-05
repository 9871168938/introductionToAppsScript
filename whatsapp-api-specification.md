# WhatsApp Integration API Specification

**Version:** 1.0.0  
**Author:** dme@evergreen.net.in  
**Date:** 2026-10-05  
**Status:** Production-Ready Draft

---

## Reasoning

### 1. Core Requirements and Primary Flows

The primary goal is a RESTful API layer that sits between a backend application and the WhatsApp Business API (Cloud API or On-Premises API). There are two fundamental recipient types:

- **Individual contact:** Identified by an E.164-formatted phone number (e.g., `+14155552671`). Every message goes through WhatsApp's standard user-to-business channel.
- **Group chat:** Identified by a WhatsApp group JID (e.g., `120363012345678901@g.us`). The sending account must be a participant of the group; the API validates membership before dispatch.

Three message types must be supported: plain text, images (JPEG/PNG/WEBP/GIF), and PDF documents. The primary flows are:

1. **Send Text** → validate recipient → call WhatsApp messages API → return message ID.
2. **Send Image** → validate recipient → upload media to WhatsApp media endpoint → obtain media ID → send message referencing media ID → return message ID.
3. **Send PDF** → same upload-then-send pattern as images, using `document` type in the WhatsApp payload.

A secondary status-check flow is needed to surface asynchronous delivery status, because WhatsApp delivers status updates (sent, delivered, read, failed) via webhooks rather than synchronously.

### 2. Supported Attachment Types, Limits, and File Handling

WhatsApp Business API enforces hard limits per media type:

| Type     | Accepted MIME types                       | Max size |
|----------|-------------------------------------------|----------|
| Image    | image/jpeg, image/png, image/webp, image/gif (static) | 5 MB     |
| Document | application/pdf                           | 100 MB   |

File handling strategy:

- Clients **MUST** upload files as `multipart/form-data`. Base64 encoding is explicitly disallowed in the inbound API to prevent payload size inflation (~33 % overhead).
- Upon receipt, the API server uploads the file to WhatsApp's `/media` endpoint and stores the returned `media_id` transiently (TTL 30 days per WhatsApp policy).
- The raw file is **not** persisted on the API server after successful upload to WhatsApp; only the `media_id` and metadata are logged for audit.
- A `/media/upload` pre-upload endpoint is offered so large files can be uploaded once and reused across multiple sends (important for broadcast or bulk-send patterns).

### 3. Authentication: Approach and Justification

Two layers of authentication are required:

**Layer 1 — API Gateway Authentication (client → our API):**  
Bearer token authentication using JWT (JSON Web Tokens) signed with RS256. Tokens are issued by our Identity Provider (IdP), carry a `waba_id` claim (WhatsApp Business Account ID), and expire after 1 hour. Refresh tokens (7-day TTL) are provided for long-lived server-to-server integrations. This approach is chosen over simple API keys because it allows scoped, auditable, revocable access per sending account.

**Layer 2 — WhatsApp Business API Authentication (our API → WhatsApp):**  
System user access tokens from Meta's Graph API are stored server-side as encrypted secrets (AES-256-GCM, keys managed by a secrets manager such as AWS Secrets Manager or HashiCorp Vault). Clients **never** receive or transmit these tokens. This two-tier design ensures that even a compromised client JWT cannot directly access WhatsApp credentials.

All transport uses TLS 1.2 minimum (TLS 1.3 preferred). Mutual TLS (mTLS) is recommended for machine-to-machine callers.

### 4. Structure and Organization of Endpoints

Endpoints are versioned under `/v1/` and grouped by functional domain:

- `/v1/messages/*` — all outbound message operations (text, image, document)
- `/v1/media/*` — media pre-upload and metadata retrieval
- `/v1/messages/{message_id}/status` — delivery status polling
- `/v1/webhooks/status` — inbound webhook receiver for WhatsApp status callbacks

This flat, resource-oriented structure avoids deep nesting while keeping related operations co-located. Versioning in the path (not header) is chosen for simplicity and debuggability. Each endpoint accepts and returns `application/json` except multipart upload endpoints, which accept `multipart/form-data` and return `application/json`.

HTTP methods follow REST semantics strictly: `POST` for resource creation (message dispatch), `GET` for status retrieval, `DELETE` for media cleanup.

### 5. Security and Compliance Considerations

**Encryption:** All data in transit uses TLS 1.3. All media files in temporary storage use AES-256 at rest. WhatsApp end-to-end encryption applies automatically for messages once delivered to the platform.

**Data minimisation (GDPR Article 5(1)(c)):** Phone numbers and group IDs are treated as personal data. They are logged only in hashed form (SHA-256 with per-tenant salt) in access logs. Raw numbers appear only in the transient request processing pipeline, not in persistent logs.

**Right to erasure (GDPR Article 17):** The API exposes a `DELETE /v1/media/{media_id}` endpoint to remove uploaded media from WhatsApp's servers and our audit trail.

**API Key / Token hygiene:** JWT tokens are short-lived (1 hour). Token revocation is supported via a deny-list backed by Redis. Suspicious activity (> 5 failed auth attempts in 60 s) triggers an automatic 15-minute lockout and an alert.

**Input validation:** All parameters are validated against a strict JSON Schema before processing. Phone numbers are validated against E.164 format and checked against WhatsApp's opt-in registry. Group IDs are validated by format regex before dispatch.

**Rate limiting:** Per-token rate limits are enforced at the API gateway layer using a sliding window counter in Redis. WhatsApp's own tier limits (messaging limit tiers 1–4, template-based rate limits) are propagated as structured error responses rather than silently swallowed.

**Audit logging:** Every outbound message attempt is logged with: timestamp, hashed recipient, message type, success/failure, WhatsApp message ID (if successful), and the calling token's `sub` claim. Logs are append-only and retained for 90 days.

### 6. Response Structure and Error Reporting

All responses follow a consistent envelope:

- `status`: `"success"` or `"error"`
- `data`: present on success, contains resource-specific fields
- `error`: present on failure, contains `code` (machine-readable), `message` (human-readable), `details` (structured sub-errors or upstream error passthrough), and `request_id` for tracing

Error codes are namespaced: `WHATSAPP_*` for errors originating from Meta's API, `VALIDATION_*` for input errors, `AUTH_*` for authentication/authorisation failures, `RATE_LIMIT_*` for throttling, and `INTERNAL_*` for unexpected server errors.

WhatsApp API errors (from Meta's Graph API) are translated faithfully: the original `error.code`, `error.error_subcode`, and `error.error_user_msg` fields are forwarded in `error.details.upstream` to avoid information loss during debugging.

Async delivery failures (e.g., recipient phone unreachable) arrive via webhook and are NOT surfaced in the synchronous send response; the send response only confirms that WhatsApp accepted the message.

---

## API Specification

### Overview

**Base URL:** `https://api.yourdomain.com/v1`

**Protocol:** HTTPS only (TLS 1.2+)

**Content negotiation:**
- Request body: `application/json` (text/status endpoints) or `multipart/form-data` (media upload endpoint)
- Response body: always `application/json`

**Authentication:** Bearer token in `Authorization` header for all endpoints except `POST /v1/webhooks/status`.

**Idempotency:** All `POST` endpoints accept an optional `Idempotency-Key` header (UUID v4). Duplicate requests with the same key within a 24-hour window return the original response without re-sending.

---

### Authentication

All requests (except the webhook receiver) must include:

Authorization: Bearer \<jwt_token\>

**Token claims required:**
- `sub` — caller identifier
- `waba_id` — WhatsApp Business Account ID authorised for the caller
- `exp` — expiry (max 1 hour from issue)
- `scope` — must include `messages:write` for send endpoints, `media:write` for upload, `media:delete` for deletion

**Token issuance endpoint (out of scope for this spec):** `POST /auth/token`

---

### Rate Limits

| Limit type              | Value                              | Header returned on limit         |
|-------------------------|------------------------------------|----------------------------------|
| Per-token burst         | 100 requests / 10 seconds          | `X-RateLimit-Remaining`          |
| Per-token sustained     | 1,000 requests / minute            | `X-RateLimit-Reset` (Unix epoch) |
| Per-WABA (WhatsApp tier)| Varies by Meta tier (1k–10M/day)   | `Retry-After` (seconds)          |

When a rate limit is exceeded, the API returns HTTP `429` with a structured error body (see error examples below).

---

### Endpoints

---

#### POST /v1/messages/text

**Description:** Send a plain-text message to an individual WhatsApp contact or a group chat.

**Required scope:** `messages:write`

**Request headers:**

- `Authorization: Bearer <jwt_token>` (required)
- `Content-Type: application/json` (required)
- `Idempotency-Key: <uuid_v4>` (optional, recommended)

**Request body fields:**

| Field           | Type    | Required | Description                                                                                          |
|-----------------|---------|----------|------------------------------------------------------------------------------------------------------|
| recipient_type  | string  | Yes      | `"individual"` or `"group"`                                                                          |
| recipient_id    | string  | Yes      | E.164 phone number for individuals (e.g., `"+14155552671"`) or group JID for groups (e.g., `"120363012345678901@g.us"`) |
| message         | string  | Yes      | UTF-8 text content. Max 4096 characters.                                                             |
| preview_url     | boolean | No       | Whether to render link previews. Default: `false`.                                                   |

**Example Request:**

{
  "recipient_type": "individual",
  "recipient_id": "+14155552671",
  "message": "Hello! Your order #98234 has been shipped and will arrive by October 7th.",
  "preview_url": false
}

**Example Success Response (HTTP 202 Accepted):**

{
  "status": "success",
  "data": {
    "message_id": "wamid.HBgLMTQxNTU1NTI2NzEVAgARGBI4QjY3QzQ5QTJFQkJBRkY4RDMA",
    "recipient_type": "individual",
    "recipient_id": "+14155552671",
    "status": "accepted",
    "timestamp": "2026-10-05T10:23:45Z"
  },
  "request_id": "req_01JA4K8XZB3F9V7P2MNQW6DCGH"
}

**Example Error Response — Validation Failure (HTTP 422):**

{
  "status": "error",
  "error": {
    "code": "VALIDATION_INVALID_PHONE",
    "message": "The recipient_id must be a valid E.164 phone number when recipient_type is 'individual'.",
    "details": {
      "field": "recipient_id",
      "provided_value": "14155552671",
      "expected_format": "+[country_code][number], e.g. +14155552671"
    },
    "request_id": "req_01JA4K9ABD5V0P1XNMQ3W2ECFH"
  }
}

**Example Error Response — WhatsApp Upstream Failure (HTTP 502):**

{
  "status": "error",
  "error": {
    "code": "WHATSAPP_SEND_FAILED",
    "message": "WhatsApp API rejected the message. The recipient may not have a WhatsApp account.",
    "details": {
      "upstream": {
        "error_code": 131026,
        "error_subcode": null,
        "error_user_msg": "Message Undeliverable",
        "fbtrace_id": "A_xFvNqkMPy1a2B3c4D5E6F"
      }
    },
    "request_id": "req_01JA4KACDF6Q2R3YOMN5W4EDHJ"
  }
}

---

#### POST /v1/messages/image

**Description:** Send an image file to an individual contact or group chat. The image must be uploaded via `POST /v1/media/upload` first to obtain a `media_id`, OR the client may provide a publicly accessible HTTPS URL.

**Required scope:** `messages:write`

**Request headers:**

- `Authorization: Bearer <jwt_token>` (required)
- `Content-Type: application/json` (required)
- `Idempotency-Key: <uuid_v4>` (optional, recommended)

**Request body fields:**

| Field           | Type    | Required | Description                                                                                          |
|-----------------|---------|----------|------------------------------------------------------------------------------------------------------|
| recipient_type  | string  | Yes      | `"individual"` or `"group"`                                                                          |
| recipient_id    | string  | Yes      | E.164 phone number or group JID                                                                      |
| source_type     | string  | Yes      | `"media_id"` (pre-uploaded) or `"url"` (public HTTPS URL)                                           |
| media_id        | string  | Cond.    | Required when `source_type` is `"media_id"`. WhatsApp media ID from `/v1/media/upload`.             |
| url             | string  | Cond.    | Required when `source_type` is `"url"`. Must be HTTPS. Max file size 5 MB.                          |
| caption         | string  | No       | Optional image caption. Max 1024 characters.                                                         |
| filename        | string  | No       | Display filename. Defaults to the original uploaded filename or URL basename.                         |

**Accepted MIME types:** `image/jpeg`, `image/png`, `image/webp`  
**Max file size:** 5 MB

**Example Request (using pre-uploaded media_id):**

{
  "recipient_type": "group",
  "recipient_id": "120363012345678901@g.us",
  "source_type": "media_id",
  "media_id": "469478993642767",
  "caption": "Q3 2026 Sales Chart — please review before Friday's meeting.",
  "filename": "q3_sales_chart.png"
}

**Example Success Response (HTTP 202 Accepted):**

{
  "status": "success",
  "data": {
    "message_id": "wamid.HBgLMTIwMzYzMDEyMzQ1Njc4OTAxVAgARGBIABFCEFDA2ABF9012",
    "recipient_type": "group",
    "recipient_id": "120363012345678901@g.us",
    "media_id": "469478993642767",
    "status": "accepted",
    "timestamp": "2026-10-05T10:31:12Z"
  },
  "request_id": "req_01JA4KBEFG7R4S5ZPNQ6W7EHKL"
}

**Example Request (using public URL):**

{
  "recipient_type": "individual",
  "recipient_id": "+447911123456",
  "source_type": "url",
  "url": "https://cdn.yourdomain.com/assets/promo-banner-oct2026.jpg",
  "caption": "October promotion — 20% off all plans!"
}

**Example Error Response — File Too Large (HTTP 413):**

{
  "status": "error",
  "error": {
    "code": "VALIDATION_FILE_TOO_LARGE",
    "message": "Image files must not exceed 5 MB. The referenced resource is 7.2 MB.",
    "details": {
      "max_allowed_bytes": 5242880,
      "actual_bytes": 7549747,
      "source_type": "url",
      "url": "https://cdn.yourdomain.com/assets/large-banner.jpg"
    },
    "request_id": "req_01JA4KCFGH8S6T7AQOR7X8FILM"
  }
}

**Example Error Response — Unsupported MIME Type (HTTP 422):**

{
  "status": "error",
  "error": {
    "code": "VALIDATION_UNSUPPORTED_MIME",
    "message": "The provided file type is not supported for image messages.",
    "details": {
      "provided_mime_type": "image/bmp",
      "accepted_mime_types": ["image/jpeg", "image/png", "image/webp"]
    },
    "request_id": "req_01JA4KDFHI9T7U8BROP8Y9GJMN"
  }
}

---

#### POST /v1/messages/document

**Description:** Send a PDF document to an individual contact or group chat.

**Required scope:** `messages:write`

**Request headers:**

- `Authorization: Bearer <jwt_token>` (required)
- `Content-Type: application/json` (required)
- `Idempotency-Key: <uuid_v4>` (optional, recommended)

**Request body fields:**

| Field           | Type    | Required | Description                                                                                          |
|-----------------|---------|----------|------------------------------------------------------------------------------------------------------|
| recipient_type  | string  | Yes      | `"individual"` or `"group"`                                                                          |
| recipient_id    | string  | Yes      | E.164 phone number or group JID                                                                      |
| source_type     | string  | Yes      | `"media_id"` or `"url"`                                                                              |
| media_id        | string  | Cond.    | Required when `source_type` is `"media_id"`.                                                         |
| url             | string  | Cond.    | Required when `source_type` is `"url"`. Must be HTTPS.                                              |
| caption         | string  | No       | Optional description shown below the document thumbnail. Max 1024 characters.                        |
| filename        | string  | Yes      | Filename displayed in the WhatsApp chat (e.g., `"invoice_2026_10.pdf"`). Must end in `.pdf`.         |

**Accepted MIME types:** `application/pdf`  
**Max file size:** 100 MB

**Example Request:**

{
  "recipient_type": "individual",
  "recipient_id": "+919876543210",
  "source_type": "media_id",
  "media_id": "891234567890123",
  "filename": "invoice_INV-2026-1042.pdf",
  "caption": "Please find your invoice for October 2026 attached. Contact support@yourdomain.com with any questions."
}

**Example Success Response (HTTP 202 Accepted):**

{
  "status": "success",
  "data": {
    "message_id": "wamid.HBgLKzkxOTg3NjU0MzIxMFVAgARGBIA1234ABCDEF567890GH",
    "recipient_type": "individual",
    "recipient_id": "+919876543210",
    "media_id": "891234567890123",
    "filename": "invoice_INV-2026-1042.pdf",
    "status": "accepted",
    "timestamp": "2026-10-05T11:04:38Z"
  },
  "request_id": "req_01JA4KEHIJ0U8V9CSQT9Z0HKNO"
}

**Example Error Response — WhatsApp Media Expired (HTTP 400):**

{
  "status": "error",
  "error": {
    "code": "WHATSAPP_MEDIA_EXPIRED",
    "message": "The referenced media_id has expired. WhatsApp media IDs are valid for 30 days. Please re-upload the file.",
    "details": {
      "media_id": "891234567890123",
      "upstream": {
        "error_code": 131053,
        "error_subcode": null,
        "error_user_msg": "Media asset is no longer available",
        "fbtrace_id": "B_yGwOrNQz2b3C4d5E6f7G"
      }
    },
    "request_id": "req_01JA4KFIJK1V9W0DTRUA1ILOP"
  }
}

---

#### POST /v1/media/upload

**Description:** Upload a media file (image or PDF) to WhatsApp's media servers. Returns a `media_id` that can be reused in send calls within 30 days.

**Required scope:** `media:write`

**Request headers:**

- `Authorization: Bearer <jwt_token>` (required)
- `Content-Type: multipart/form-data` (required; set by HTTP client automatically)

**Form fields:**

| Field      | Type   | Required | Description                                               |
|------------|--------|----------|-----------------------------------------------------------|
| file       | binary | Yes      | The file binary. Accepted MIME types: `image/jpeg`, `image/png`, `image/webp`, `application/pdf`. |
| mime_type  | string | Yes      | Explicit MIME type (e.g., `"application/pdf"`).           |
| filename   | string | Yes      | Original filename with extension (e.g., `"report_q3.pdf"`). |

**Size limits:**
- Images: 5 MB
- PDFs: 100 MB

**Example Success Response (HTTP 201 Created):**

{
  "status": "success",
  "data": {
    "media_id": "469478993642767",
    "mime_type": "image/png",
    "filename": "q3_sales_chart.png",
    "file_size_bytes": 204800,
    "waba_id": "102290129340398",
    "expires_at": "2026-11-04T10:31:00Z",
    "uploaded_at": "2026-10-05T10:31:00Z"
  },
  "request_id": "req_01JA4KGJKL2W0X1EUSB2JMPQ"
}

**Example Error Response — Virus/Malware Detected (HTTP 422):**

{
  "status": "error",
  "error": {
    "code": "VALIDATION_MALWARE_DETECTED",
    "message": "The uploaded file failed security scanning and was rejected. The file has not been stored.",
    "details": {
      "scan_engine": "ClamAV 1.3.0",
      "threat_name": "Eicar-Test-Signature",
      "filename": "test.pdf"
    },
    "request_id": "req_01JA4KHJLM3X1Y2FVTC3KNQR"
  }
}

---

#### GET /v1/messages/{message_id}/status

**Description:** Poll the delivery status of a previously sent message. Note: production integrations should prefer the webhook-based status delivery (`POST /v1/webhooks/status`) over polling.

**Required scope:** `messages:write`

**Path parameters:**

| Parameter  | Type   | Required | Description                          |
|------------|--------|----------|--------------------------------------|
| message_id | string | Yes      | The WhatsApp message ID (`wamid.*`). |

**Query parameters:**

| Parameter | Type   | Required | Description                                         |
|-----------|--------|----------|-----------------------------------------------------|
| expand    | string | No       | Comma-separated fields to expand. Supported: `"recipient"`. |

**Example Request:**

GET /v1/messages/wamid.HBgLMTQxNTU1NTI2NzEVAgARGBI4QjY3QzQ5QTJFQkJBRkY4RDMA/status

**Example Success Response (HTTP 200 OK):**

{
  "status": "success",
  "data": {
    "message_id": "wamid.HBgLMTQxNTU1NTI2NzEVAgARGBI4QjY3QzQ5QTJFQkJBRkY4RDMA",
    "delivery_status": "read",
    "status_history": [
      {
        "status": "accepted",
        "timestamp": "2026-10-05T10:23:45Z"
      },
      {
        "status": "sent",
        "timestamp": "2026-10-05T10:23:46Z"
      },
      {
        "status": "delivered",
        "timestamp": "2026-10-05T10:24:01Z"
      },
      {
        "status": "read",
        "timestamp": "2026-10-05T10:27:33Z"
      }
    ],
    "recipient_type": "individual",
    "message_type": "text",
    "sent_at": "2026-10-05T10:23:45Z"
  },
  "request_id": "req_01JA4KIKMN4Y2Z3GWUD4LORSK"
}

**Example Error Response — Message Not Found (HTTP 404):**

{
  "status": "error",
  "error": {
    "code": "RESOURCE_NOT_FOUND",
    "message": "No message with the given ID was found for the authenticated account.",
    "details": {
      "message_id": "wamid.INVALID_OR_WRONG_ACCOUNT",
      "hint": "Verify the message_id was issued by this WABA. IDs are not portable across accounts."
    },
    "request_id": "req_01JA4KJLNO5Z3A4HVUE5MPSTL"
  }
}

---

#### DELETE /v1/media/{media_id}

**Description:** Delete a previously uploaded media asset from WhatsApp's servers. Use for GDPR erasure requests or cleanup of sensitive documents. The media asset is deleted from WhatsApp's CDN; this action is irreversible.

**Required scope:** `media:delete`

**Path parameters:**

| Parameter | Type   | Required | Description                    |
|-----------|--------|----------|--------------------------------|
| media_id  | string | Yes      | The media ID to delete.        |

**Example Success Response (HTTP 200 OK):**

{
  "status": "success",
  "data": {
    "media_id": "469478993642767",
    "deleted": true,
    "deleted_at": "2026-10-05T12:00:00Z"
  },
  "request_id": "req_01JA4KKLOP6A4B5IWVF6NQTUML"
}

**Example Error Response — Already Deleted or Not Found (HTTP 404):**

{
  "status": "error",
  "error": {
    "code": "RESOURCE_NOT_FOUND",
    "message": "The specified media_id does not exist or has already been deleted.",
    "details": {
      "media_id": "469478993642767"
    },
    "request_id": "req_01JA4KLMPQ7B5C6JXWG7ORUVNM"
  }
}

---

#### POST /v1/webhooks/status

**Description:** Inbound webhook endpoint. WhatsApp calls this endpoint to deliver asynchronous message status updates (sent, delivered, read, failed) and inbound message notifications. This endpoint is registered in the Meta App Dashboard as the Webhook Callback URL.

**Authentication:** Webhook signature verification via HMAC-SHA256. The `X-Hub-Signature-256` header value must equal `sha256=<HMAC-SHA256(app_secret, raw_request_body)>`. Requests failing signature verification are rejected with HTTP 401.

**Request headers:**

- `Content-Type: application/json`
- `X-Hub-Signature-256: sha256=<signature>`

**Request body (WhatsApp-delivered, verbatim):**

{
  "object": "whatsapp_business_account",
  "entry": [
    {
      "id": "102290129340398",
      "changes": [
        {
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {
              "display_phone_number": "15550001234",
              "phone_number_id": "107890987654321"
            },
            "statuses": [
              {
                "id": "wamid.HBgLMTQxNTU1NTI2NzEVAgARGBI4QjY3QzQ5QTJFQkJBRkY4RDMA",
                "status": "delivered",
                "timestamp": "1728125041",
                "recipient_id": "14155552671",
                "conversation": {
                  "id": "CONV_abc123",
                  "origin": {
                    "type": "utility"
                  }
                },
                "pricing": {
                  "billable": true,
                  "pricing_model": "CBP",
                  "category": "utility"
                }
              }
            ]
          },
          "field": "messages"
        }
      ]
    }
  ]
}

**Example Success Response (HTTP 200 OK):**

{
  "status": "success",
  "data": {
    "processed_events": 1,
    "event_ids": ["wamid.HBgLMTQxNTU1NTI2NzEVAgARGBI4QjY3QzQ5QTJFQkJBRkY4RDMA"]
  },
  "request_id": "req_01JA4KMNQR8C6D7KYXH8PSVWON"
}

**Example Error Response — Signature Mismatch (HTTP 401):**

{
  "status": "error",
  "error": {
    "code": "AUTH_INVALID_WEBHOOK_SIGNATURE",
    "message": "The X-Hub-Signature-256 header does not match the computed HMAC. The request was rejected.",
    "details": {
      "hint": "Ensure the webhook app secret matches the value configured in the Meta App Dashboard."
    },
    "request_id": "req_01JA4KNORS9D7E8LZYJ9QTWXPO"
  }
}

---

### Common Error Codes Reference

| HTTP Status | Error Code                        | Meaning                                                          |
|-------------|-----------------------------------|------------------------------------------------------------------|
| 400         | WHATSAPP_MEDIA_EXPIRED            | media_id is older than 30 days                                   |
| 400         | WHATSAPP_OPT_OUT                  | Recipient has opted out of messages from this WABA               |
| 401         | AUTH_TOKEN_EXPIRED                | JWT has expired; client must refresh                             |
| 401         | AUTH_TOKEN_INVALID                | Malformed or tampered JWT                                        |
| 401         | AUTH_INSUFFICIENT_SCOPE           | Token lacks required scope for this endpoint                     |
| 401         | AUTH_INVALID_WEBHOOK_SIGNATURE    | Webhook HMAC verification failed                                 |
| 403         | AUTH_ACCOUNT_SUSPENDED            | WABA suspended by Meta; contact support                          |
| 404         | RESOURCE_NOT_FOUND                | message_id or media_id does not exist for this account           |
| 409         | IDEMPOTENCY_CONFLICT              | Idempotency-Key reuse with different payload                      |
| 413         | VALIDATION_FILE_TOO_LARGE         | File exceeds per-type size limit                                 |
| 422         | VALIDATION_INVALID_PHONE          | Non-E.164 phone number                                           |
| 422         | VALIDATION_UNSUPPORTED_MIME       | MIME type not accepted by WhatsApp for this message type         |
| 422         | VALIDATION_MALWARE_DETECTED       | Uploaded file failed antivirus scan                              |
| 429         | RATE_LIMIT_TOKEN                  | Per-token rate limit exceeded (our API)                          |
| 429         | RATE_LIMIT_WHATSAPP               | WhatsApp Business tier rate limit exceeded; check Retry-After    |
| 502         | WHATSAPP_SEND_FAILED              | WhatsApp API returned a non-retryable error; see details.upstream|
| 503         | WHATSAPP_UNAVAILABLE              | WhatsApp API is temporarily unreachable; safe to retry           |
| 500         | INTERNAL_SERVER_ERROR             | Unexpected server-side failure; report with request_id           |

**Example Rate Limit Error Response (HTTP 429):**

{
  "status": "error",
  "error": {
    "code": "RATE_LIMIT_WHATSAPP",
    "message": "Your WhatsApp Business Account has reached its messaging tier limit. Upgrade your tier or wait for the limit to reset.",
    "details": {
      "current_tier": 1,
      "tier_daily_limit": 1000,
      "messages_sent_today": 1000,
      "reset_at": "2026-10-06T00:00:00Z",
      "upgrade_guide_url": "https://developers.facebook.com/docs/whatsapp/messaging-limits"
    },
    "request_id": "req_01JA4KPOSTAU8F9MZYK0RUXYPQ"
  }
}

---

### OpenAPI 3.1 Summary (Abbreviated)

openapi: "3.1.0"
info:
  title: "WhatsApp Integration API"
  version: "1.0.0"
  description: "Production API for sending WhatsApp messages and managing media."
servers:
  - url: "https://api.yourdomain.com/v1"
security:
  - bearerAuth: []
components:
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT
  schemas:
    SuccessEnvelope:
      type: object
      required: [status, data, request_id]
      properties:
        status:
          type: string
          enum: [success]
        data:
          type: object
        request_id:
          type: string
    ErrorEnvelope:
      type: object
      required: [status, error]
      properties:
        status:
          type: string
          enum: [error]
        error:
          type: object
          required: [code, message, request_id]
          properties:
            code:
              type: string
            message:
              type: string
            details:
              type: object
            request_id:
              type: string

---

### Changelog

| Version | Date       | Author                    | Notes                                   |
|---------|------------|---------------------------|------------------------------------------|
| 1.0.0   | 2026-10-05 | dme@evergreen.net.in      | Initial production-ready draft           |

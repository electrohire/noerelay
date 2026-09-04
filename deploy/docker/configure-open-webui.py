"""Idempotently apply the supported NoeRelay Open WebUI profile."""

import json
import os
import urllib.request


BASE_URL = os.environ["OPEN_WEBUI_URL"].rstrip("/")


def request(
    path: str,
    payload: dict | None = None,
    token: str | None = None,
    method: str | None = None,
) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=None if payload is None else json.dumps(payload).encode(),
        headers=headers,
        method=method or ("GET" if payload is None else "POST"),
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


auth = request(
    "/api/v1/auths/signin",
    {
        "email": os.environ["WEBUI_ADMIN_EMAIL"],
        "password": os.environ["WEBUI_ADMIN_PASSWORD"],
    },
)
token = auth["token"]

# Keep the governed assistant behind NoeRelay. The recovery assistant is
# intentionally configured below as a separate, direct Ollama connection so it
# can repair NoeRelay even when the control plane or an external provider fails.
request(
    "/openai/config/update",
    {
        "ENABLE_OPENAI_API": True,
        "OPENAI_API_BASE_URLS": ["http://noerelay:8080/v1"],
        "OPENAI_API_KEYS": [os.environ["NOERELAY_API_KEY"]],
        "OPENAI_API_CONFIGS": {
            "0": {
                "enable": True,
                "name": "NoeRelay",
                "prefix_id": "",
                "model_ids": ["axiovex-agni"],
            }
        },
    },
    token,
)
recovery_base_model = os.environ.get("NOERELAY_RECOVERY_LOCAL_MODEL", "qwen3.8:27b")
request(
    "/ollama/config/update",
    {
        "ENABLE_OLLAMA_API": True,
        "OLLAMA_BASE_URLS": ["http://ollama:11434"],
        "OLLAMA_API_CONFIGS": {
            "0": {
                "enable": True,
                "name": "Local Recovery",
                "prefix_id": "",
                "model_ids": [recovery_base_model],
            }
        },
    },
    token,
)

recovery_model = {
    "id": "axiovex-agni-recovery",
    "base_model_id": recovery_base_model,
    "name": "AXIOVEX Agni Recovery",
    "meta": {
        "description": (
            "Independent local host-GPU recovery agent for maintaining AXIOVEX "
            "components when NoeRelay or external providers are unavailable."
        ),
        "capabilities": {"vision": True, "usage": True},
    },
    "params": {
        "function_calling": "native",
        "num_ctx": 32768,
        "system": (
            "You are AXIOVEX Agni Recovery, an independent local maintenance agent. "
            "Your inference runs directly on the host-GPU Ollama service and bypasses "
            "NoeRelay and OpenRouter so you can diagnose, repair, test, and improve the "
            "AXIOVEX NoeRelay control plane and related AXIOVEX components without a "
            "bootstrapping dependency. Use the available Open Terminal tools to inspect "
            "the real workspace and execute bounded, verifiable work. For self-improvement "
            "requests, iterate only while tests or measured evidence improve and stop at "
            "diminishing returns. Never claim that this direct recovery inference path has "
            "NoeRelay governance receipts; report that boundary accurately."
        ),
    },
    "access_grants": [],
    "is_active": True,
}
# Keep the selector intentionally narrow: one governed model and one independent
# recovery model. Provider filtering above supplies the governed base model, so
# every persisted workspace model other than the recovery alias is stale here.
model_listing = request("/api/v1/models/list?page=1", token=token)
for item in model_listing.get("items", []):
    stale_id = item.get("id", "")
    if not stale_id or stale_id == recovery_model["id"]:
        continue
    try:
        request("/api/v1/models/model/delete", {"id": stale_id}, token)
    except Exception:
        pass
try:
    request("/api/v1/models/create", recovery_model, token)
except Exception:
    request("/api/v1/models/model/update", recovery_model, token)

request(
    "/api/v1/configs/models",
    {
        "DEFAULT_MODELS": "axiovex-agni",
        "DEFAULT_PINNED_MODELS": "axiovex-agni,axiovex-agni-recovery",
        "MODEL_ORDER_LIST": [
            "axiovex-agni",
            "axiovex-agni-recovery",
        ],
        "DEFAULT_MODEL_METADATA": {},
        "DEFAULT_MODEL_PARAMS": {"function_calling": "native"},
    },
    token,
)

visible_models = request("/api/models", token=token).get("data", [])
visible_ids = [model.get("id") for model in visible_models]
expected_ids = {"axiovex-agni", "axiovex-agni-recovery"}
if len(visible_ids) != 2 or set(visible_ids) != expected_ids:
    raise RuntimeError(f"unexpected AXIOVEX Sentinel model set: {visible_ids}")

# Keep execution local and sandboxed in the browser by default. Open Terminal,
# registered below, is the explicit path for agentic work against mounted files.
code_config = request("/api/v1/configs/code_execution", token=token)
code_config.update(
    {
        "ENABLE_CODE_EXECUTION": True,
        "CODE_EXECUTION_ENGINE": "pyodide",
        "ENABLE_CODE_INTERPRETER": True,
        "CODE_INTERPRETER_ENGINE": "pyodide",
    }
)
request("/api/v1/configs/code_execution", code_config, token)

# Local OCR/PDF ingestion plus keyless web search. Images are OCR inputs; video
# and YouTube attachments remain handled by Open WebUI's media/transcript path.
request(
    "/api/v1/retrieval/config/update",
    {
        "CONTENT_EXTRACTION_ENGINE": "docling",
        "CONTENT_EXTRACTION_SUPPORTED_MEDIA_MIME_TYPES": [
            "image/*",
            "audio/*",
            "video/*",
        ],
        "PDF_EXTRACT_IMAGES": True,
        "PDF_LOADER_MODE": "page",
        "DOCLING_SERVER_URL": "http://docling:5001",
        "DOCLING_PARAMS": {
            "do_ocr": True,
            "force_ocr": False,
            "ocr_engine": "tesseract",
            "ocr_lang": ["eng"],
            "pdf_backend": "dlparse_v4",
            "table_mode": "accurate",
            "pipeline": "standard",
        },
        "ENABLE_RAG_HYBRID_SEARCH": True,
        "ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER": True,
        "web": {
            "ENABLE_WEB_SEARCH": True,
            "ENABLE_WEB_SEARCH_CONFIRMATION": True,
            "WEB_SEARCH_ENGINE": "duckduckgo",
            "WEB_SEARCH_RESULT_COUNT": 5,
            "WEB_SEARCH_CONCURRENT_REQUESTS": 5,
            "WEB_LOADER_CONCURRENT_REQUESTS": 10,
            "WEB_LOADER_TIMEOUT": "15",
            "ENABLE_WEB_LOADER_SSL_VERIFICATION": True,
            "YOUTUBE_LOADER_LANGUAGE": ["en"],
        },
    },
    token,
)

# Ratings feed the built-in analytics. Arena models are intentionally disabled:
# exposing synthetic arena entries would violate the one-public-model contract.
request(
    "/api/v1/evaluations/config",
    {"ENABLE_EVALUATION_ARENA_MODELS": False, "EVALUATION_ARENA_MODELS": []},
    token,
)

admin_config = request("/api/v1/auths/admin/config", token=token)
admin_config.update(
    {
        "ENABLE_SIGNUP": False,
        "ENABLE_API_KEYS": False,
        "DEFAULT_USER_ROLE": "pending",
        "ENABLE_COMMUNITY_SHARING": False,
        "ENABLE_MESSAGE_RATING": True,
        "ENABLE_FOLDERS": True,
        "ENABLE_CHANNELS": False,
        "ENABLE_CALENDAR": False,
        "ENABLE_MEMORIES": True,
        "ENABLE_MEMORY_SYSTEM_CONTEXT": True,
        "ENABLE_NOTES": True,
        "ENABLE_USER_WEBHOOKS": False,
        "ENABLE_USER_STATUS": True,
        "DEFAULT_INTERFACE_SETTINGS": {"widescreenMode": True},
        "RESPONSE_WATERMARK": (
            "© 2026 AXIOVEX Systems Inc. · AXIOVEX Sentinel · "
            "NoeRelay Intelligent AI Control Plane"
        ),
    }
)
request("/api/v1/auths/admin/config", admin_config, token)
request(
    "/api/v1/configs/banners",
    {
        "banners": [
            {
                "id": "axiovex-sentinel-identity",
                "type": "info",
                "title": "AXIOVEX Sentinel",
                "content": (
                    '<img src="/static/axiovex/axiovex-logo-mark.svg" '
                    'alt="AXIOVEX" width="18" height="18"> '
                    "**AXIOVEX Sentinel** · NoeRelay — Intelligent AI Control Plane · "
                    "© 2026 AXIOVEX Systems Inc."
                ),
                "dismissible": False,
                "timestamp": 1788472800,
            }
        ]
    },
    token,
)

request(
    "/api/v1/configs/terminal_servers",
    {
        "TERMINAL_SERVER_CONNECTIONS": [
            {
                "id": "noerelay-workspace",
                "name": "NoeRelay Workspace",
                "enabled": True,
                "url": "http://open-terminal:8000",
                "path": "/openapi.json",
                "key": os.environ["OPEN_TERMINAL_API_KEY"],
                "auth_type": "bearer",
                "server_type": "terminal",
                "config": {"access_grants": []},
            }
        ]
    },
    token,
)
print("Open WebUI governed and independent local recovery models configured")

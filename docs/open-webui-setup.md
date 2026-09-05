# AXIOVEX Sentinel interface

**AXIOVEX Sentinel** is the enterprise-facing interface. **NoeRelay** is the
Intelligent AI Control Plane that selects and governs models underneath it.
Copyright © 2026 AXIOVEX Systems Inc. All rights reserved.

Open WebUI, Open Terminal, and the local Docling OCR/PDF service are part of the
primary GPU-enabled Compose stack.
Open WebUI sends ordinary work only to the Rust NoeRelay OpenAI-compatible
endpoint. Its explicitly named recovery model is the sole exception: it calls
the private local Ollama service directly so it remains available when NoeRelay
or an external provider is broken.

Start the stack on Linux:

```bash
bash scripts/linux-stack.sh init
bash scripts/linux-stack.sh up
```

Open `http://127.0.0.1:${NOERELAY_WEBUI_PORT}` (port `3000` by default).
This workstation uses `3001` because another local process owns port `3000`.
The initial email is `admin@noerelay.local`. Retrieve the generated password
from the ignored host-only file:

```bash
grep '^WEBUI_ADMIN_PASSWORD=' .env.docker
```

Public signup is disabled. The one-shot `open-webui-init` service idempotently
replaces persisted provider settings with exactly two public models:
`axiovex-agni` plus the independent local `axiovex-agni-recovery` route. It also
creates an administrator-only Open Terminal connection named
`NoeRelay Workspace`. Both APIs are private to the Compose network.
The recovery alias defaults to the local `qwen3.8:27b` model and can be changed
with `NOERELAY_RECOVERY_LOCAL_MODEL` without exposing its provider ID to clients.
This direct inference path does not claim NoeRelay governance receipts; the
separation is intentional for bootstrap-independent maintenance.

The same independent recovery route is available outside Sentinel at
`http://127.0.0.1:${NOERELAY_RECOVERY_PORT:-4002}/v1`. It accepts the model ID
`axiovex-agni-recovery` and the bearer token stored as `LITELLM_MASTER_KEY` in
the host-only `.env.docker` file. It remains usable if the NoeRelay gateway is
stopped.

Normal local inference prefers NVIDIA Personal AI Router (PAIR) as the outer
GPU-node router. Install PAIR on the Docker host, make
`NOERELAY_PAIR_MODEL` available in PAIR, and set its OpenAI-compatible URL in
`NOERELAY_PAIR_BASE_URL`. Docker reaches it through `host.docker.internal` on
Windows and Linux. If PAIR is unavailable, the model plane falls back to its
container-local model and then the configured external provider. Recovery
intentionally bypasses PAIR as well as NoeRelay, preserving an independent
repair path.

The managed profile enables browser-sandboxed code execution, Open Terminal,
DuckDuckGo web search with confirmation, hybrid document retrieval, Docling
OCR for images and scanned PDFs, page-aware PDF loading/render extraction,
audio/video media extraction and YouTube transcripts, message ratings, and administrator
analytics. Image generation/editing and evaluation-arena pseudo-models are
disabled because no generation backend is bundled. Evaluation-arena pseudo-models
remain disabled so the selector contains only the two intentional AXIOVEX entries.
Image understanding/OCR remains enabled.

NoeRelay normalizes provider-specific JSON tool envelopes into native OpenAI
tool calls for both buffered streaming and non-streaming responses. Self-description
and architecture questions are answered from NoeRelay's injected product manifest
without resource-discovery tools, while explicit requests such as listing knowledge
bases retain native tool execution. A gateway circuit breaker disables tools after
two identical calls (or eight calls in one user turn), and Open WebUI's independent
continuation ceiling defaults to 12 through
`CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS`. This prevents a weak provider from
repeating one tool until Open WebUI's upstream 256-iteration default is exhausted.

Set `NOERELAY_WORKSPACE_PATH` in `.env.docker` to an absolute host directory.
Linux paths and Docker Desktop paths such as `C:/Users/name/Development` are
supported. Open Terminal sees it at `/workspace` and edits are reflected on the
host. Additional explicit bind mounts can be added using
`docker-compose.workspaces.example.yml`.

Open Terminal intentionally runs Linux inside its container on every host. On
Windows it edits mounted Windows files, but it does not run arbitrary native
Windows processes. Deployment `.env` files are overlaid with redacted files in
the terminal container so agent loops cannot read control-plane secrets.

Open WebUI connection and integration keys are managed server-side in **Admin
Settings**. Non-secret agent settings can be managed under `/settings`. Core
container environment and signing/provider secrets remain in the host secret
file or an external secret manager and require a service recreation to apply.

Local password authentication is the safe development default. OAuth/OIDC and
LDAP configuration are present in Admin Settings but disabled until a real
identity provider is configured. Set `NOERELAY_WEBUI_ENABLE_OAUTH=true` or
`NOERELAY_WEBUI_ENABLE_LDAP=true` only with the corresponding provider details.
These controls support a CMMC-oriented deployment, but configuration alone is
not CMMC certification; production also needs TLS, centralized identity,
retention/backups, network policy, monitoring, and an assessed system boundary.

Approved AXIOVEX assets are served read-only from `deploy/docker/brand` and the
interface uses a subordinate identity banner plus a dark, wide workspace
default. Open WebUI attribution is retained. Deployments above Open WebUI's
small-scale exception need an appropriate Open WebUI license before deeper
white-label branding.

For architecture, remote GPU behavior, verification, and production controls,
see [Linux GPU deployment](LINUX_GPU_DEPLOYMENT.md).

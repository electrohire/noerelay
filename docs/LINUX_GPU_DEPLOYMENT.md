# NoeRelay Linux GPU deployment

This Compose deployment runs the complete system behind the Rust NoeRelay API:

```text
Open WebUI / OpenAI client -> Rust NoeRelay -> LiteLLM private model plane
                                             |-> Ollama -> all host NVIDIA GPUs
                                             |-> SSH tunnel -> remote GPU
                                             `-> OpenRouter (optional fallback)
                         `-> PostgreSQL audit, receipt, IAM, and cost records
Open WebUI -> Open Terminal -> /workspace host bind mount
           `-> Docling -> local OCR and page-aware PDF extraction
```

The model plane, database, and SSH tunnel have no published host ports. Only the
OpenAI-compatible Rust API (`8080`), Web UI (`3000`), and A2A adapter (`8090`)
are exposed.

## Prerequisites

- Linux with Docker Engine and Docker Compose v2
- NVIDIA driver and NVIDIA Container Toolkit configured for Docker
- Private-network connectivity to the remote GPU
- An SSH key authorized for the remote GPU account and a matching `known_hosts` entry
- An OpenAI-compatible inference server listening on remote port `4000`

## Start

```bash
bash scripts/linux-stack.sh init
$EDITOR .env.docker
bash scripts/linux-stack.sh up
bash scripts/linux-stack.sh verify
```

Set `REMOTE_GPU_MODEL` to the model name returned by the remote `/v1/models`
endpoint. Set `REMOTE_GPU_API_KEY` if that endpoint authenticates. Set
`OPENROUTER_API_KEY` only when cloud fallback is desired.

The first start downloads `qwen3:8b` into the Docker `ollama-models` volume.
Change `NOERELAY_LOCAL_MODEL` before first start or pull another model with
`./scripts/linux-stack.sh pull-model MODEL`.

Open `http://127.0.0.1:3000` for the UI. OpenAI SDKs should use
`http://127.0.0.1:8080/v1`, the `NOERELAY_API_KEY` value, and model
`axiovex-agni`, with the direct local `axiovex-agni-recovery` model reserved for maintenance.

`GET /v1/models` deliberately returns only the two AXIOVEX aliases. Internal Ollama,
Remote GPU, LiteLLM, and OpenRouter model identifiers never enter the public
catalog or successful response bodies; the Rust gateway chooses among them.

The initial account is `admin@noerelay.local`; its randomly generated password
is stored only in the ignored `.env.docker` file. Retrieve it locally with
`grep '^WEBUI_ADMIN_PASSWORD=' .env.docker`. Headless creation disables public
signup. Change the password in the UI after first login.

## Workspaces and Open Terminal

`NOERELAY_WORKSPACE_PATH` binds one host directory at `/workspace`, and
`NOERELAY_SETTINGS_PATH` binds non-secret agent-loop settings at `/settings`.
Use an absolute Linux path on Linux or a Docker Desktop path such as
`C:/Users/name/Development` on Windows. To mount more directories, copy
`docker-compose.workspaces.example.yml`, add explicit bind mounts, and include
it with `docker compose -f docker-compose.yml -f your-workspaces.yml ...`.

After logging in, add a system connection under **Admin Settings → Integrations
→ Open Terminal**. Use URL `http://open-terminal:8000` and the
`OPEN_TERMINAL_API_KEY` from `.env.docker`. The key stays in Open WebUI's
server-side settings. Do not mount `.env.docker` into Open Terminal or expose
the terminal port publicly.

When the mounted workspace is this repository, Compose overlays redacted files
at `/workspace/.env` and `/workspace/.env.docker`. This prevents an agentic loop
from reading control-plane credentials. Open WebUI can safely retain connection
keys in its server-side Admin settings; non-secret loop configuration belongs
under `/settings`. Changing core container environment still requires editing
the host-only `.env.docker` and recreating the affected service.

Open Terminal runs Linux commands even when Docker Desktop is hosted on
Windows; edits under `/workspace` apply directly to the Windows files. It does
not provide unrestricted Windows process execution. Run Open Terminal natively
on Windows only if that extra authority is intentionally required.

## Remote behavior

`remote-gpu-tunnel` uses `autossh`. If the remote GPU is offline, the local GPU
deployment continues serving requests and the tunnel reconnects automatically.
When the remote deployment is healthy, LiteLLM's `gpu-pool` distributes calls
between local Ollama and remote vLLM. Failed deployments are cooled down and
retried elsewhere. OpenRouter is the final fallback when its key is configured.

Inspect connectivity with:

```bash
./scripts/linux-stack.sh status
./scripts/linux-stack.sh logs remote-gpu-tunnel
./scripts/linux-stack.sh logs litellm
```

For production, set `NOERELAY_WEBUI_ENABLE_SIGNUP=false` after creating the
initial administrator, rotate keys on a schedule, back up the PostgreSQL and
Open WebUI volumes, and restrict ports 3000/8080/8090 at the host firewall.
Enable OIDC/LDAP only after supplying and testing real provider metadata. The
deployment supplies governance and audit controls but does not, by itself,
constitute CMMC certification or replace organizational policy and assessment.

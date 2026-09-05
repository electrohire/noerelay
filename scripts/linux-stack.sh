#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${repo_dir}/.env.docker"
compose=(docker compose --env-file "${env_file}" -f "${repo_dir}/docker-compose.yml")

random_hex() {
  openssl rand -hex "$1"
}

init_env() {
  command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 1; }
  if [[ -e "${env_file}" ]]; then
    ensure_env_additions
    echo "${env_file} already exists; added any newly required settings."
    return
  fi
  local ssh_dir="${HOME}/.ssh"
  umask 077
  {
    printf 'NOERELAY_API_KEY=nr_%s\n' "$(random_hex 24)"
    printf 'NOERELAY_A2A_BEARER_KEY=a2a_%s\n' "$(random_hex 24)"
    printf 'NOERELAY_POSTGRES_PASSWORD=%s\n' "$(random_hex 24)"
    printf 'LITELLM_MASTER_KEY=sk-%s\n' "$(random_hex 24)"
    printf 'WEBUI_SECRET_KEY=%s\n' "$(random_hex 32)"
    printf 'WEBUI_ADMIN_EMAIL=admin@noerelay.local\nWEBUI_ADMIN_NAME=NoeRelay-Admin\n'
    printf 'WEBUI_ADMIN_PASSWORD=Nr!%s\n' "$(random_hex 18)"
    printf 'OPEN_TERMINAL_API_KEY=ot_%s\n' "$(random_hex 32)"
    printf 'NOERELAY_RECEIPT_SIGNING_SEED_HEX=%s\n' "$(random_hex 32)"
    printf 'NOERELAY_LOCAL_MODEL=qwen3:8b\n'
    printf 'NOERELAY_RECOVERY_LOCAL_MODEL=qwen3.8:27b\n'
    printf 'REMOTE_GPU_SSH_HOST=remote-gpu.example.internal\n'
    printf 'REMOTE_GPU_SSH_PORT=22\nREMOTE_GPU_SSH_USER=actor\n'
    printf 'REMOTE_GPU_SSH_KEY_PATH=%s/id_remote_gpu\n' "${ssh_dir}"
    printf 'REMOTE_GPU_KNOWN_HOSTS_PATH=%s/known_hosts\n' "${ssh_dir}"
    printf 'REMOTE_GPU_VLLM_HOST=127.0.0.1\nREMOTE_GPU_VLLM_PORT=4000\n'
    printf 'REMOTE_GPU_MODEL=local-model\nREMOTE_GPU_API_KEY=no-key\n'
    printf 'OPENROUTER_API_KEY=\n'
    printf 'NOERELAY_PORT=8080\nNOERELAY_A2A_PORT=8090\nNOERELAY_WEBUI_PORT=3000\nNOERELAY_RECOVERY_PORT=4002\n'
    printf 'NOERELAY_WEBUI_ENABLE_SIGNUP=false\n'
    printf 'NOERELAY_WORKSPACE_PATH=%s\n' "${repo_dir}"
    printf 'NOERELAY_SETTINGS_PATH=%s/.noerelay-settings\n' "${repo_dir}"
  } >"${env_file}"
  mkdir -p "${repo_dir}/.noerelay-settings"
  chmod 600 "${env_file}"
  echo "Created ${env_file}. Set REMOTE_GPU_SSH_HOST, REMOTE_GPU_MODEL/API_KEY, and optional OPENROUTER_API_KEY before use."
}

ensure_env_additions() {
  command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 1; }
  grep -q '^WEBUI_ADMIN_EMAIL=' "${env_file}" || printf 'WEBUI_ADMIN_EMAIL=admin@noerelay.local\n' >>"${env_file}"
  grep -q '^WEBUI_ADMIN_NAME=' "${env_file}" || printf 'WEBUI_ADMIN_NAME=NoeRelay-Admin\n' >>"${env_file}"
  grep -q '^WEBUI_ADMIN_PASSWORD=' "${env_file}" || printf 'WEBUI_ADMIN_PASSWORD=Nr!%s\n' "$(random_hex 18)" >>"${env_file}"
  grep -q '^OPEN_TERMINAL_API_KEY=' "${env_file}" || printf 'OPEN_TERMINAL_API_KEY=ot_%s\n' "$(random_hex 32)" >>"${env_file}"
  grep -q '^NOERELAY_WORKSPACE_PATH=' "${env_file}" || printf 'NOERELAY_WORKSPACE_PATH=%s\n' "${repo_dir}" >>"${env_file}"
  grep -q '^NOERELAY_SETTINGS_PATH=' "${env_file}" || printf 'NOERELAY_SETTINGS_PATH=%s/.noerelay-settings\n' "${repo_dir}" >>"${env_file}"
  mkdir -p "${repo_dir}/.noerelay-settings"
  chmod 700 "${repo_dir}/.noerelay-settings" 2>/dev/null || true
}

require_env() {
  [[ -f "${env_file}" ]] || { echo "Run '$0 init' first." >&2; exit 1; }
  # Compose gives an already-exported host variable precedence over --env-file.
  # Export the deployment file explicitly so stale shell credentials cannot
  # silently replace the generated stack credentials.
  set -a
  # shellcheck disable=SC1090
  source "${env_file}"
  set +a
}

case "${1:-help}" in
  init)
    init_env
    ;;
  up)
    require_env
    docker info >/dev/null
    docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 \
      nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
    "${compose[@]}" up -d --build --remove-orphans
    "${compose[@]}" ps
    echo "Web UI: http://127.0.0.1:${NOERELAY_WEBUI_PORT:-3000}  API: http://127.0.0.1:${NOERELAY_PORT:-8080}/v1"
    ;;
  down)
    require_env
    "${compose[@]}" down
    ;;
  status)
    require_env
    "${compose[@]}" ps
    ;;
  logs)
    require_env
    "${compose[@]}" logs -f --tail=200 "${@:2}"
    ;;
  pull-model)
    require_env
    model="${2:?usage: $0 pull-model MODEL}"
    "${compose[@]}" exec ollama ollama pull "${model}"
    ;;
  verify)
    require_env
    "${compose[@]}" config --quiet
    "${compose[@]}" exec -T ollama nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
    curl --fail --silent "http://127.0.0.1:${NOERELAY_PORT:-8080}/health" >/dev/null
    curl --fail --silent "http://127.0.0.1:${NOERELAY_WEBUI_PORT:-3000}/health" >/dev/null
    "${compose[@]}" ps
    ;;
  *)
    echo "Usage: $0 {init|up|down|status|logs [SERVICE]|pull-model MODEL|verify}"
    ;;
esac

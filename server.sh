#!/usr/bin/env bash

# server.sh — process control for the MoonTVPlus FastAPI server.
#
#   ./server.sh start      # start in the background, log to .run/server.log
#   ./server.sh stop       # SIGTERM, then SIGKILL if it does not exit
#   ./server.sh restart
#   ./server.sh status     # pid, uptime, memory, last log lines
#   ./server.sh run        # foreground; Ctrl+C stops it. This is what a
#                          # container should run as PID 1 (uvicorn's signal
#                          # handling then reaches the app directly)
#   ./server.sh log        # tail -f the log
#   ./server.sh build      # rebuild frontend/dist, then start
#   ./server.sh cron       # old start.js's /api/cron loop, foreground
#   ./server.sh test       # pytest under backend/
#   ./server.sh help
#
# Options may appear before or after the command:
#   --env FILE    dotenv file to load       (default: ./.env, skipped if absent)
#   --host HOST   bind host                 (default: exported HOSTNAME, else 0.0.0.0)
#   --port PORT   bind port                 (default: PORT, else 3000)
#   --log FILE    log file                  (default: .run/server.log, background only)
#   --            everything after this is forwarded to uvicorn verbatim
#                 (put it after the command, or the command goes to uvicorn too)
#
# Environment. MoonTV's own variable names are kept identical, so existing
# `docker run -e ...` / docker-compose `environment:` blocks work unchanged:
#
#   HOSTNAME, PORT      the two the old MoonTV Docker image exported
#                       (`ENV HOSTNAME=0.0.0.0`, `ENV PORT=3000`). Only an
#                       *exported* HOSTNAME counts: bash always sets HOSTNAME
#                       to the machine name, which is not a bindable address.
#   PASSWORD, USERNAME, NEXT_PUBLIC_*, STORAGE_TYPE, REDIS_URL, ...
#                       read by the app itself (see backend/moontv/env.py).
#   MOONTV_DB_PATH      SQLite database file. Defaulted here to an absolute
#                       path so the database does not silently move with the
#                       caller's cwd (the app's own default is relative).
#
# Script-local knobs use the MOONTVPLUS_ prefix so they cannot collide with
# anything the app reads:
#
#   MOONTVPLUS_ENV_FILE, MOONTVPLUS_RUN_DIR, MOONTVPLUS_DATA_DIR,
#   MOONTVPLUS_LOG_FILE, MOONTVPLUS_LOG_LEVEL, MOONTVPLUS_STOP_TIMEOUT,
#   MOONTVPLUS_RELOAD (=1 adds --reload), MOONTVPLUS_CRON_INTERVAL (seconds).
#
# Precedence: --host/--port > env file > exported HOSTNAME / PORT > defaults.

set -euo pipefail

SCRIPT="${BASH_SOURCE[0]}"
SCRIPT_DIR="$(cd "$(dirname "${SCRIPT}")" && pwd)"
ROOT="$SCRIPT_DIR"
BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"
VENV_DIR="$BACKEND_DIR/.venv"
PY="$VENV_DIR/bin/python"

APP="moontv.main:app"

RUN_DIR="${MOONTVPLUS_RUN_DIR:-$ROOT/.run}"
DATA_DIR="${MOONTVPLUS_DATA_DIR:-$BACKEND_DIR}"
ENV_FILE="${MOONTVPLUS_ENV_FILE:-$ROOT/.env}"
LOG_FILE="${MOONTVPLUS_LOG_FILE:-$RUN_DIR/server.log}"
PID_FILE="$RUN_DIR/server.pid"
STOP_TIMEOUT="${MOONTVPLUS_STOP_TIMEOUT:-10}"
LOG_LEVEL="${MOONTVPLUS_LOG_LEVEL:-info}"
CRON_INTERVAL="${MOONTVPLUS_CRON_INTERVAL:-3600}"

EXTRA_ARGS=()

usage() {
  # The header comment block is the help text, so there is only one copy of it.
  awk 'NR == 1 && /^#!/ { next } /^#/ { sub(/^# ?/, ""); print; next } /^[[:space:]]*$/ { next } { exit }' "$SCRIPT"
}

# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------
parse_args() {
  # Options may appear before or after the command, so this is one pass that
  # takes the first non-option as the command. `--` must come after it, since
  # everything past `--` is forwarded to uvicorn verbatim.
  COMMAND=""
  while (($#)); do
    case "$1" in
      --env)
        ENV_FILE="${2:?--env needs a file}"
        shift 2
        ;;
      --host)
        CLI_HOST="${2:?--host needs a value}"
        shift 2
        ;;
      --port)
        CLI_PORT="${2:?--port needs a value}"
        shift 2
        ;;
      --log)
        LOG_FILE="${2:?--log needs a file}"
        shift 2
        ;;
      --)
        shift
        EXTRA_ARGS=("$@")
        break
        ;;
      -*)
        printf 'unknown option: %s (see help)\n' "$1"
        exit 2
        ;;
      *)
        if [[ -n "$COMMAND" ]]; then
          printf 'unexpected argument: %s (see help)\n' "$1"
          exit 2
        fi
        COMMAND="$1"
        shift
        ;;
    esac
  done
  COMMAND="${COMMAND:-help}"
}

# --------------------------------------------------------------------------
# Defaults. Resolved after the env file is loaded, so the file can set them;
# --host/--port still win over both.
# --------------------------------------------------------------------------
exported_host() {
  # Only an exported HOSTNAME (docker -e / Dockerfile ENV) is a bind address.
  local decl
  decl="$(declare -p HOSTNAME 2>/dev/null || true)"
  [[ "$decl" == declare\ -x\ HOSTNAME=* ]] && printf '%s' "$HOSTNAME"
}

resolve_config() {
  # precedence: --host/--port > env file > exported HOSTNAME / PORT > defaults
  HOST="${CLI_HOST:-${HOST:-}}"
  if [[ -z "${HOST:-}" ]]; then
    HOST="$(exported_host || true)"
  fi
  HOST="${HOST:-0.0.0.0}"
  PORT="${CLI_PORT:-${PORT:-3000}}"

  # Absolute, so `backend/moontv.db` is the database wherever the script is
  # invoked from. The app's own default is the relative `moontv.db`.
  MOONTV_DB_PATH="${MOONTV_DB_PATH:-$DATA_DIR/moontv.db}"
  export MOONTV_DB_PATH

  # The app resolves the frontend against its own file location, so this is
  # only needed when someone runs the app from a copied tree.
  MOONTV_FRONTEND_DIR="${MOONTV_FRONTEND_DIR:-$FRONTEND_DIR}"
  export MOONTV_FRONTEND_DIR

  # uvicorn's logging goes to a file; without this, `tail -f` lags behind.
  export PYTHONUNBUFFERED=1

  UVICORN_ARGS=(--app-dir "$BACKEND_DIR" --host "$HOST" --port "$PORT" --log-level "$LOG_LEVEL")
  if [[ "${MOONTVPLUS_RELOAD:-0}" == "1" ]]; then
    UVICORN_ARGS+=(--reload --reload-dir "$BACKEND_DIR")
  fi
  if ((${#EXTRA_ARGS[@]})); then
    UVICORN_ARGS+=("${EXTRA_ARGS[@]}")
  fi
}

load_env_file() {
  [[ -f "$ENV_FILE" ]] || return 0
  printf 'loading %s\n' "$ENV_FILE"
  # Parsed, not sourced: docker-compose's `environment:` block allows
  # `NEXT_PUBLIC_SITE_NAME=MoonTVPlus Demo`, which `source` would read as a
  # command. No shell expansion is offered, so an env file cannot run anything.
  local __env_line __env_key __env_value
  while IFS= read -r __env_line || [[ -n "$__env_line" ]]; do
    __env_line="${__env_line%$'\r'}"
    [[ "$__env_line" =~ ^[[:space:]]*(#|$) ]] && continue
    [[ "$__env_line" == export\ * ]] && __env_line="${__env_line#export }"
    [[ "$__env_line" == *=* ]] || continue
    __env_key="${__env_line%%=*}"
    __env_value="${__env_line#*=}"
    [[ "$__env_key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    if [[ "$__env_value" =~ ^\"(.*)\"$ ]] || [[ "$__env_value" =~ ^\'(.*)\'$ ]]; then
      __env_value="${BASH_REMATCH[1]}"
    fi
    printf -v "$__env_key" '%s' "$__env_value"
    export "$__env_key"
  done <"$ENV_FILE"
}

# --------------------------------------------------------------------------
# Bootstrap: venv, frontend build
# --------------------------------------------------------------------------
ensure_venv() {
  if [[ -x "$PY" ]]; then
    return 0
  fi
  printf 'backend/.venv is missing — creating it (needs network for pip)\n'
  python3 -m venv "$VENV_DIR"
  "$PY" -m pip install --upgrade pip >/dev/null
  # Keep in sync with backend/pyproject.toml dependencies.
  "$PY" -m pip install fastapi uvicorn httpx cryptography redis python-multipart pytest pytest-asyncio
}

ensure_frontend_dist() {
  if [[ -f "$FRONTEND_DIR/dist/index.html" ]]; then
    return 0
  fi
  printf 'frontend/dist is missing — building the bundle\n'
  build_frontend
}

build_frontend() {
  [[ -d "$FRONTEND_DIR" ]] || {
    printf 'no frontend/ directory\n'
    return 1
  }
  if ! command -v pnpm >/dev/null; then
    printf 'pnpm not found — cannot build the frontend\n'
    return 1
  fi
  (
    cd "$FRONTEND_DIR"
    pnpm install
    pnpm build
  )
}

# --------------------------------------------------------------------------
# Process bookkeeping. The pid file alone is not trusted: a recycled pid would
# make `stop` kill someone else's process, so the cmdline must still be ours.
# --------------------------------------------------------------------------
get_pid() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(<"$PID_FILE")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  printf '%s' "$pid"
}

running() {
  local pid
  pid="$(get_pid || true)"
  [[ -n "$pid" ]] || return 1
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  tr '\0' ' ' <"/proc/$pid/cmdline" | grep -q -- "$APP" || return 1
  return 0
}

# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
cmd_start() {
  if running; then
    printf 'already running (pid %s) — use restart\n' "$(get_pid)"
    return 0
  fi
  ensure_venv
  ensure_frontend_dist
  mkdir -p "$RUN_DIR"

  {
    printf '\n===== %s  host=%s port=%s db=%s =====\n' "$(date '+%F %T')" "$HOST" "$PORT" "$MOONTV_DB_PATH"
  } >>"$LOG_FILE"

  # Only the bytes this run appends count as evidence: an earlier start
  # already wrote the same "Uvicorn running on" line into the same file, and
  # matching that would turn a failed bind (port in use) into a fake success.
  local log_offset=0
  if [[ -f "$LOG_FILE" ]]; then
    log_offset="$(stat -c%s "$LOG_FILE")"
  fi

  nohup "$PY" -m uvicorn "$APP" "${UVICORN_ARGS[@]}" >>"$LOG_FILE" 2>&1 </dev/null &
  local pid=$!
  printf '%s' "$pid" >"$PID_FILE"

  local waited=0
  while ((waited < 10)); do
    if tail -c +"$((log_offset + 1))" "$LOG_FILE" 2>/dev/null | grep -q "Uvicorn running on"; then
      printf 'started (pid %s) — http://%s:%s/\nlog: %s\n' "$pid" "$HOST" "$PORT" "$LOG_FILE"
      if [[ -z "${PASSWORD:-}" ]]; then
        printf 'note: PASSWORD is unset, so the auth gate sends every page to /warning\n'
      fi
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$PID_FILE"
      printf 'failed to start:\n'
      tail -c +"$((log_offset + 1))" "$LOG_FILE" 2>/dev/null | tail -n 20
      return 1
    fi
    sleep 1
    waited=$((waited + 1))
  done
  printf 'started (pid %s) but no "Uvicorn running on" line yet — check the log\n' "$pid"
}

cmd_stop() {
  if ! running; then
    rm -f "$PID_FILE"
    printf 'not running\n'
    return 0
  fi
  local pid
  pid="$(get_pid)"
  printf 'stopping (pid %s)\n' "$pid"
  kill -TERM "$pid"

  local waited=0
  while ((waited < STOP_TIMEOUT)); do
    running || break
    sleep 1
    waited=$((waited + 1))
  done
  if running; then
    printf 'still alive after %ss — sending SIGKILL\n' "$STOP_TIMEOUT"
    kill -KILL "$pid" 2>/dev/null || true
    sleep 1
  fi
  rm -f "$PID_FILE"
  printf 'stopped\n'
}

cmd_status() {
  if running; then
    local pid
    pid="$(get_pid)"
    printf 'running (pid %s)\n' "$pid"
    ps -o pid=,etime=,rss=,args= -p "$pid"
    printf 'url:  http://%s:%s/\n' "$HOST" "$PORT"
    printf 'db:   %s\n' "$MOONTV_DB_PATH"
    printf 'log:  %s\n' "$LOG_FILE"
    if [[ -f "$LOG_FILE" ]]; then
      printf 'last log lines:\n'
      tail -n 5 "$LOG_FILE"
    fi
    return 0
  fi
  printf 'not running\n'
  return 3
}

cmd_run() {
  ensure_venv
  ensure_frontend_dist
  mkdir -p "$RUN_DIR"
  printf 'serving http://%s:%s/ (Ctrl+C to stop)\n' "$HOST" "$PORT"
  # exec so uvicorn becomes this process: SIGINT/SIGTERM from the shell or from
  # `docker stop` goes straight to uvicorn's graceful shutdown path.
  exec "$PY" -m uvicorn "$APP" "${UVICORN_ARGS[@]}"
}

cmd_log() {
  [[ -f "$LOG_FILE" ]] || {
    printf 'no log yet: %s\n' "$LOG_FILE"
    return 1
  }
  tail -n 50 -f "$LOG_FILE"
}

cmd_cron() {
  if ! command -v curl >/dev/null; then
    printf 'curl not found — cannot run the cron loop\n'
    return 1
  fi
  # `src/start.js` in the old image polled /login until the server answered
  # 2xx, ran /api/cron once, then reran it hourly. The route is ported
  # (`backend/moontv/routes/cron.py`, fire-and-forget like upstream).
  local probe_host="$HOST"
  [[ "$probe_host" == "0.0.0.0" || "$probe_host" == "[::]" ]] && probe_host="localhost"
  local base="http://$probe_host:$PORT"

  printf 'waiting for %s/login ...\n' "$base"
  while true; do
    if curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$base/login" | grep -q '^2'; then
      break
    fi
    sleep 1
  done
  while true; do
    printf 'GET %s/api/cron\n' "$base"
    curl -sS --max-time 30 "$base/api/cron" || printf 'cron job failed\n'
    printf 'sleeping %ss\n' "$CRON_INTERVAL"
    sleep "$CRON_INTERVAL"
  done
}

cmd_test() {
  ensure_venv
  (
    cd "$BACKEND_DIR"
    "$PY" -m pytest
  )
}

# --------------------------------------------------------------------------
# Entry
# --------------------------------------------------------------------------
main() {
  parse_args "$@"
  load_env_file
  resolve_config

  case "$COMMAND" in
    start) cmd_start ;;
    stop) cmd_stop ;;
    restart)
      cmd_stop
      cmd_start
      ;;
    status) cmd_status ;;
    run) cmd_run ;;
    log) cmd_log ;;
    build)
      build_frontend
      cmd_start
      ;;
    cron) cmd_cron ;;
    test) cmd_test ;;
    help | --help | -h) usage ;;
    "") usage ;;
    *)
      printf 'unknown command: %s (see help)\n' "$COMMAND"
      exit 2
      ;;
  esac
}

main "$@"

#!/usr/bin/env bash

# install.sh — bootstrap the MoonTVPlus toolchain from a bare tree.
#
#   ./install.sh                 # python venv + deps, then frontend pnpm install
#   ./install.sh --backend       # only the Python venv and its dependencies
#   ./install.sh --frontend      # only the frontend (pnpm install; add --build for dist)
#   ./install.sh --build         # also build frontend/dist, which the backend serves
#   ./install.sh --recreate      # throw away an existing venv before rebuilding it
#   ./install.sh --python BIN    # interpreter to build the venv from (default: python3)
#   ./install.sh --venv DIR      # where the venv goes (default: backend/.venv)
#   ./install.sh --pip-index URL # pip index mirror (default: PyPI)
#   ./install.sh --allow-builds  # pnpm 12 refuses dependency build scripts; set
#                                # dangerouslyAllowAllBuilds=true in the project's
#                                # .npmrc before installing the frontend
#   ./install.sh --dry-run       # print the commands instead of running them
#   ./install.sh help
#
# The dependency list is read from backend/pyproject.toml, so there is one
# source of truth for what the backend needs (version constraints are passed to
# pip as-is, so `fastapi>=0.115.0` installs whatever satisfies it).
#
# Nothing here is destructive by default: an existing venv is reused, and
# --recreate is the only path that removes one.

set -euo pipefail

SCRIPT="${BASH_SOURCE[0]}"
ROOT="$(cd "$(dirname "${SCRIPT}")" && pwd)"
BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"
PYPROJECT="$BACKEND_DIR/pyproject.toml"

VENV_DIR="${MOONTVPLUS_VENV_DIR:-$BACKEND_DIR/.venv}"
PY="$VENV_DIR/bin/python"
HOSTPY="${MOONTVPLUS_PY:-python3}"
MIN_PY="3.11"
PIP_INDEX="${MOONTVPLUS_PIP_INDEX:-}"

REQ_BACKEND=0
REQ_FRONTEND=0
DO_BUILD=0
RECREATE=0
ALLOW_BUILDS=0
DRY=0

usage() {
  awk 'NR == 1 && /^#!/ { next } /^#/ { sub(/^# ?/, ""); print; next } /^[[:space:]]*$/ { next } { exit }' "$SCRIPT"
}

run() {
  if [[ $DRY == 1 ]]; then
    printf '+ %q\n' "$@"
    return 0
  fi
  "$@"
}

# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------
parse_args() {
  # Options may appear on either side of the (single) command word.
  COMMAND=""
  while (($#)); do
    case "$1" in
      --backend) REQ_BACKEND=1; shift ;;
      --frontend) REQ_FRONTEND=1; shift ;;
      --build) DO_BUILD=1; REQ_FRONTEND=1; shift ;;
      --recreate) RECREATE=1; shift ;;
      --python)
        HOSTPY="${2:?--python needs a path}"
        shift 2
        ;;
      --venv)
        VENV_DIR="${2:?--venv needs a directory}"
        PY="$VENV_DIR/bin/python"
        shift 2
        ;;
      --pip-index)
        PIP_INDEX="${2:?--pip-index needs a url}"
        shift 2
        ;;
      --allow-builds) ALLOW_BUILDS=1; shift ;;
      --dry-run) DRY=1; shift ;;
      --)
        shift
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
  COMMAND="${COMMAND:-install}"

  if [[ $REQ_BACKEND == 0 && $REQ_FRONTEND == 0 ]]; then
    REQ_BACKEND=1
    REQ_FRONTEND=1
  fi
}

# --------------------------------------------------------------------------
# Python: interpreter, venv, dependencies
# --------------------------------------------------------------------------
check_python() {
  if ! command -v "$HOSTPY" >/dev/null; then
    printf 'no python interpreter found (%s). Point the script at one with --python.\n' "$HOSTPY"
    return 1
  fi
  local version
  version="$("$HOSTPY" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')"
  if ! "$HOSTPY" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"; then
    printf 'python %s is too old: backend/pyproject.toml requires >=%s (and tomllib, which is 3.11+).\n' "$version" "$MIN_PY"
    return 1
  fi
  printf 'host python: %s (%s)\n' "$HOSTPY" "$version"
}

create_venv() {
  if [[ -d "$VENV_DIR" && $RECREATE == 0 ]]; then
    printf 'reusing venv: %s\n' "$VENV_DIR"
    return 0
  fi
  if [[ -d "$VENV_DIR" ]]; then
    printf 'removing %s (--recreate)\n' "$VENV_DIR"
    run rm -rf "$VENV_DIR"
  fi
  run "$HOSTPY" -m venv "$VENV_DIR"
  printf 'created venv: %s\n' "$VENV_DIR"
}

read_deps() {
  # pyproject's dependency array, one requirement spec per line.
  DEPS=()
  local line
  while IFS= read -r line; do
    [[ -n "$line" ]] && DEPS+=("$line")
  done < <("$HOSTPY" - "$PYPROJECT" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as fh:
    data = tomllib.load(fh)
print("\n".join(data["project"]["dependencies"]))
PY
)
  if ((${#DEPS[@]} == 0)); then
    printf 'no dependencies declared in %s\n' "$PYPROJECT"
    return 1
  fi
  printf 'dependencies from %s: %s\n' "$PYPROJECT" "${DEPS[*]}"
}

install_deps() {
  local pip_args=()
  if [[ -n "$PIP_INDEX" ]]; then
    pip_args+=(--index-url "$PIP_INDEX")
  fi
  # Non-fatal: an unreachable index should not stop the dependency install
  # attempt below, which reports its own failure.
  run "$PY" -m pip install --upgrade pip || printf 'pip upgrade failed — continuing with the venv pip\n'
  if ! run "$PY" -m pip install "${pip_args[@]}" "${DEPS[@]}"; then
    printf 'pip failed — check network/index. You can retry with --pip-index <mirror>.\n'
    return 1
  fi
}

verify_backend() {
  "$PY" - <<'PY'
import importlib
mods = ["fastapi", "uvicorn", "httpx", "cryptography", "redis", "pytest", "pytest_asyncio", "python_multipart"]
missing = []
for name in mods:
    try:
        mod = importlib.import_module(name)
        print(f"  {name} {getattr(mod, '__version__', '?')}")
    except ImportError:
        missing.append(name)
if missing:
    print("missing:", " ".join(missing))
    raise SystemExit(1)
PY
  # Byte-compile the package so a syntax error is caught here, not at first
  # request (uvicorn imports moontv.main at startup).
  if "$PY" -m compileall -q "$BACKEND_DIR/moontv"; then
    printf 'backend/moontv compiles clean\n'
  else
    printf 'backend/moontv does not compile\n'
    return 1
  fi
}

# --------------------------------------------------------------------------
# Frontend: node/pnpm, install, optional build
# --------------------------------------------------------------------------
install_frontend() {
  if ! command -v node >/dev/null; then
    printf 'node not found — skipping the frontend. The backend still runs; it just has no UI to serve.\n'
    return 0
  fi
  if ! command -v pnpm >/dev/null; then
    printf 'pnpm not found — install it (corepack enable && corepack prepare pnpm@latest --activate) and rerun.\n'
    return 0
  fi
  [[ -f "$FRONTEND_DIR/package.json" ]] || {
    printf 'no frontend/package.json\n'
    return 1
  }
  printf 'node %s / pnpm %s\n' "$(node -v)" "$(pnpm -v)"

  if [[ $ALLOW_BUILDS == 1 ]]; then
    # pnpm 12 asks before running a dependency's build script; this is the
    # project-scoped opt-out, written to frontend/.npmrc.
    (
      cd "$FRONTEND_DIR"
      run pnpm config set dangerouslyAllowAllBuilds=true --location project
    )
    printf 'set dangerouslyAllowAllBuilds=true in %s\n' "$FRONTEND_DIR/.npmrc"
  fi

  (
    cd "$FRONTEND_DIR"
    run pnpm install
  ) || printf 'pnpm install failed — see its output above.\n'
}

build_frontend() {
  (
    cd "$FRONTEND_DIR"
    run pnpm build
  ) || printf 'pnpm build failed — the backend can still start, but there is no dist to serve.\n'
}

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------
summary() {
  local pyver="none"
  if [[ -x "$PY" ]]; then
    pyver="$("$PY" -V)"
  fi
  local dist="missing — the app has nothing to serve yet"
  if [[ -f "$FRONTEND_DIR/dist/index.html" ]]; then
    dist="present"
  fi
  printf '\nbackend venv : %s (%s)\n' "$VENV_DIR" "$pyver"
  printf 'frontend     : %s (dist/index.html %s)\n' "$FRONTEND_DIR" "$dist"
  printf 'next         : ./server.sh start   (PASSWORD and NEXT_PUBLIC_* come from the\n'
  printf '               environment; server.sh also loads ./.env when it exists)\n'
}

# --------------------------------------------------------------------------
# Entry
# --------------------------------------------------------------------------
main() {
  parse_args "$@"
  case "$COMMAND" in
    install) ;;
    help | --help | -h)
      usage
      exit 0
      ;;
    *)
      printf 'unknown command: %s (see help)\n' "$COMMAND"
      exit 2
      ;;
  esac

  if [[ $REQ_BACKEND == 1 ]]; then
    check_python
    create_venv
    read_deps
    install_deps
    if [[ $DRY == 0 ]]; then
      verify_backend
    fi
  fi
  if [[ $REQ_FRONTEND == 1 ]]; then
    install_frontend
    if [[ $DO_BUILD == 1 ]]; then
      build_frontend
    fi
  fi
  summary
}

main "$@"

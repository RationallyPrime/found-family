#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly STARTUP_TIMEOUT_SECONDS="${STARTUP_TIMEOUT_SECONDS:-90}"
readonly APP_PORT="${APP_PORT:-8000}"
readonly ENV_FILE="${MEMORY_PALACE_ENV_FILE:-$ROOT_DIR/.env}"

cd "$ROOT_DIR"
export MEMORY_PALACE_ENV_FILE="$ENV_FILE"
export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

app_pid=""
neo4j_started_here=false
compose=(docker compose --env-file "$ENV_FILE" -f "$ROOT_DIR/docker-compose.yml")

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

cleanup() {
    local status=$?
    trap - EXIT INT TERM

    if [[ -n "$app_pid" ]] && kill -0 "$app_pid" 2>/dev/null; then
        printf 'Stopping the API...\n'
        kill "$app_pid" 2>/dev/null || true
        wait "$app_pid" 2>/dev/null || true
    fi

    if [[ "$neo4j_started_here" == true ]]; then
        printf 'Stopping the Neo4j service started by this command...\n'
        "${compose[@]}" stop --timeout 30 neo4j >/dev/null || true
    fi

    exit "$status"
}

wait_for_api() {
    local attempt
    for ((attempt = 1; attempt <= STARTUP_TIMEOUT_SECONDS; attempt++)); do
        if [[ -n "$app_pid" ]] && ! kill -0 "$app_pid" 2>/dev/null; then
            wait "$app_pid" || true
            return 1
        fi

        if python3 -c \
            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${APP_PORT}/ready', timeout=2).close()" \
            >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

command -v docker >/dev/null 2>&1 || die "docker is required"
command -v uv >/dev/null 2>&1 || die "uv is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
[[ -f "$ENV_FILE" ]] || die "$ENV_FILE is missing; run ./setup.sh to generate it"
chmod 600 "$ENV_FILE"

"${compose[@]}" config --quiet

if "${compose[@]}" ps --status running --services | grep -Fxq neo4j; then
    printf 'Neo4j is already running; it will be left running on exit.\n'
else
    neo4j_started_here=true
    printf 'Starting Neo4j...\n'
fi

if ! "${compose[@]}" up -d --wait --wait-timeout "$STARTUP_TIMEOUT_SECONDS" neo4j; then
    "${compose[@]}" ps >&2 || true
    "${compose[@]}" logs --tail 100 neo4j >&2 || true
    die "Neo4j did not become healthy within ${STARTUP_TIMEOUT_SECONDS}s"
fi

printf 'Starting the API on http://127.0.0.1:%s...\n' "$APP_PORT"
uv run --frozen dotenv -f "$ENV_FILE" run --no-override -- \
    uvicorn memory_palace.main:app \
    --reload --host 127.0.0.1 --port "$APP_PORT" &
app_pid=$!

if ! wait_for_api; then
    die "the API did not become healthy within ${STARTUP_TIMEOUT_SECONDS}s"
fi

printf 'Memory Palace is ready. API docs: http://127.0.0.1:%s/docs\n' "$APP_PORT"
wait "$app_pid"

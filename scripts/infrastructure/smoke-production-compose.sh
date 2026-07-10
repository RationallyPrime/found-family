#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly PROJECT_NAME="memory-palace-smoke-${UID}-$$-${RANDOM}"
readonly STARTUP_TIMEOUT_SECONDS="${STARTUP_TIMEOUT_SECONDS:-180}"

command -v docker >/dev/null 2>&1 || {
    printf 'error: docker is required\n' >&2
    exit 1
}
command -v python3 >/dev/null 2>&1 || {
    printf 'error: python3 is required\n' >&2
    exit 1
}

env_file="$(mktemp /tmp/memory-palace-smoke.XXXXXX.env)"
chmod 600 "$env_file"
cat > "$env_file" <<'EOF'
VOYAGE_API_KEY=compose-smoke-placeholder
NEO4J_URI=bolt://neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=compose-smoke-database-password
JWT_SECRET_KEY=compose-smoke-signing-key-compose-smoke-signing-key
OAUTH_OWNER_USERNAME=owner
OAUTH_OWNER_PASSWORD=compose-smoke-owner-password
PUBLIC_BASE_URL=https://memory.example.com
CORS_ALLOWED_ORIGINS=["https://memory.example.com"]
OAUTH_ALLOWED_REDIRECT_URIS=["https://claude.ai/api/mcp/auth_callback"]
ENVIRONMENT=production
DEBUG=false
DISABLE_DREAM_JOBS=true
EOF

export APP_PORT=0
export MEMORY_PALACE_ENV_FILE="$env_file"
compose=(
    docker compose
    --project-name "$PROJECT_NAME"
    --env-file "$env_file"
    -f "$ROOT_DIR/docker-compose.prod.yml"
    -f "$ROOT_DIR/docker-compose.smoke.yml"
)

cleanup() {
    local status=$?
    trap - EXIT INT TERM
    "${compose[@]}" down --volumes --remove-orphans --timeout 30 >/dev/null 2>&1 || true
    rm -f -- "$env_file"
    exit "$status"
}
trap cleanup EXIT INT TERM

if ! "${compose[@]}" up -d --build --wait --wait-timeout "$STARTUP_TIMEOUT_SECONDS"; then
    "${compose[@]}" ps >&2 || true
    "${compose[@]}" logs --tail 100 >&2 || true
    exit 1
fi

app_id="$("${compose[@]}" ps -q memory-palace)"
neo4j_id="$("${compose[@]}" ps -q neo4j)"
[[ -n "$app_id" && -n "$neo4j_id" ]]

docker inspect "$app_id" "$neo4j_id" | python3 -c '
import json
import sys

app, neo4j = json.load(sys.stdin)

assert app["State"]["Health"]["Status"] == "healthy"
assert app["Config"]["User"] == "65532:65532"
assert app["HostConfig"]["ReadonlyRootfs"] is True
assert "ALL" in app["HostConfig"]["CapDrop"]
assert "no-new-privileges:true" in app["HostConfig"]["SecurityOpt"]
app_ports = app["NetworkSettings"]["Ports"]["8000/tcp"]
assert app_ports and all(binding["HostIp"] == "127.0.0.1" for binding in app_ports)

assert neo4j["State"]["Health"]["Status"] == "healthy"
assert neo4j["Config"]["User"] == "7474:7474"
assert neo4j["HostConfig"]["ReadonlyRootfs"] is True
assert "ALL" in neo4j["HostConfig"]["CapDrop"]
assert "no-new-privileges:true" in neo4j["HostConfig"]["SecurityOpt"]
assert not neo4j["HostConfig"]["PortBindings"]
data_mount = next(mount for mount in neo4j["Mounts"] if mount["Destination"] == "/data")
assert data_mount["Type"] == "volume"
assert data_mount["Name"].endswith("_neo4j_smoke_data")
'

printf 'Production Compose smoke test passed.\n'

#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly COMPOSE_FILE="$ROOT_DIR/docker-compose.prod.yml"
readonly STARTUP_TIMEOUT_SECONDS="${STARTUP_TIMEOUT_SECONDS:-180}"
readonly ENV_FILE="${MEMORY_PALACE_ENV_FILE:-$ROOT_DIR/.env}"
readonly TUNNEL_CONFIG="${CLOUDFLARED_CONFIG_FILE:-$HOME/.cloudflared/memory-palace.yml}"
readonly LEGACY_VOLUME_ACK_DIR="$ROOT_DIR/data/migration-state"

cd "$ROOT_DIR"
export MEMORY_PALACE_ENV_FILE="$ENV_FILE"
compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage:
  ./run-prod.sh
  ./run-prod.sh --acknowledge-legacy-volume FINGERPRINT

The acknowledgement command records a verified cold migration and exits. It
does not start production or delete the retained legacy volume.
EOF
}

compose_env_value() {
    local key=$1
    local line
    while IFS= read -r line; do
        if [[ "$line" == "$key="* ]]; then
            printf '%s' "${line#*=}"
            return 0
        fi
    done < <("${compose[@]}" config --environment)
    return 1
}

volume_identity_fingerprint() {
    local volume_name=$1
    local engine_id=$2

    docker volume inspect "$volume_name" | python3 -c '
import hashlib
import json
import sys

volumes = json.load(sys.stdin)
if len(volumes) != 1:
    raise SystemExit("expected exactly one Docker volume")

volume = volumes[0]
required = ("Name", "Driver", "CreatedAt", "Mountpoint")
if any(not isinstance(volume.get(field), str) or not volume[field] for field in required):
    raise SystemExit("Docker did not return stable identity metadata for the volume")

identity = {
    "created_at": volume["CreatedAt"],
    "docker_engine_id": sys.argv[1],
    "driver": volume["Driver"],
    "labels": volume.get("Labels") or {},
    "mountpoint": volume["Mountpoint"],
    "name": volume["Name"],
    "options": volume.get("Options") or {},
    "scope": volume.get("Scope") or "",
}
canonical = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
sys.stdout.write(hashlib.sha256(canonical).hexdigest())
' "$engine_id"
}

write_legacy_volume_ack() {
    local marker_path=$1
    local volume_name=$2
    local fingerprint=$3
    local bind_path=$4
    local engine_id=$5

    python3 -c '
import json
import os
from pathlib import Path
import tempfile
from datetime import UTC, datetime
import sys

target = Path(sys.argv[1])
target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
target.parent.chmod(0o700)
payload = {
    "acknowledged_at": datetime.now(UTC).isoformat(),
    "bind_path": sys.argv[4],
    "docker_engine_id": sys.argv[5],
    "legacy_volume_fingerprint": sys.argv[3],
    "legacy_volume_name": sys.argv[2],
    "schema_version": 1,
}

descriptor, temporary_name = tempfile.mkstemp(
    dir=target.parent,
    prefix=f".{target.name}.",
)
try:
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
        json.dump(payload, temporary, indent=2, sort_keys=True)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
    os.replace(temporary_name, target)
    directory_descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
finally:
    try:
        os.unlink(temporary_name)
    except FileNotFoundError:
        pass
' "$marker_path" "$volume_name" "$fingerprint" "$bind_path" "$engine_id"
}

legacy_volume_ack_matches() {
    local marker_path=$1
    local volume_name=$2
    local fingerprint=$3
    local bind_path=$4
    local engine_id=$5

    python3 -c '
import json
from pathlib import Path
import stat
import sys

path = Path(sys.argv[1])
try:
    metadata = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("acknowledgement is not a regular file")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError("acknowledgement is not owner-only")
    payload = json.loads(path.read_text(encoding="utf-8"))
except (OSError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)

expected = {
    "bind_path": sys.argv[4],
    "docker_engine_id": sys.argv[5],
    "legacy_volume_fingerprint": sys.argv[3],
    "legacy_volume_name": sys.argv[2],
    "schema_version": 1,
}
if any(payload.get(key) != value for key, value in expected.items()):
    raise SystemExit(1)
' "$marker_path" "$volume_name" "$fingerprint" "$bind_path" "$engine_id"
}

acknowledgement_fingerprint=""
case ${1:-} in
    "")
        [[ $# -eq 0 ]] || die "unexpected arguments; run ./run-prod.sh --help"
        ;;
    --acknowledge-legacy-volume)
        [[ $# -eq 2 ]] || die "--acknowledge-legacy-volume requires the fingerprint printed by the preflight gate"
        acknowledgement_fingerprint=$2
        ;;
    --help|-h)
        usage
        exit 0
        ;;
    *)
        die "unknown argument: $1; run ./run-prod.sh --help"
        ;;
esac

command -v docker >/dev/null 2>&1 || die "docker is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
[[ -f "$ENV_FILE" ]] || die "$ENV_FILE is missing; run ./setup.sh to generate it"
chmod 600 "$ENV_FILE"

"${compose[@]}" config --quiet

mapfile -t storage_config < <("${compose[@]}" config --format json | python3 -c '
import json
import sys

config = json.load(sys.stdin)
mount = next(
    item
    for item in config["services"]["neo4j"]["volumes"]
    if item["target"] == "/data"
)
print(config["name"])
print(mount["type"])
print(mount["source"])
')
[[ ${#storage_config[@]} -eq 3 ]] || die "could not resolve the Neo4j data mount"
project_name="${storage_config[0]}"
data_mount_type="${storage_config[1]}"
data_path="${storage_config[2]}"
[[ "$data_mount_type" == "bind" ]] || die "production Neo4j /data must resolve to a bind mount"

# Older releases used the project-scoped neo4j_data named volume. Docker keeps
# it after this topology change, but silently starting against an empty bind
# would look exactly like data loss. Refuse that ambiguous upgrade.
legacy_volume="${project_name}_neo4j_data"
if docker volume inspect "$legacy_volume" >/dev/null 2>&1; then
    docker_engine_id="$(docker info --format '{{.ID}}')" \
        || die "could not read the Docker engine identity"
    [[ -n "$docker_engine_id" ]] || die "Docker returned an empty engine identity"
    legacy_volume_fingerprint="$(
        volume_identity_fingerprint "$legacy_volume" "$docker_engine_id"
    )" || die "could not fingerprint legacy volume $legacy_volume"
    legacy_volume_ack_file="$LEGACY_VOLUME_ACK_DIR/${project_name}-neo4j-data.json"

    if [[ ! -d "$data_path/databases" ]]; then
        die "legacy volume $legacy_volume exists while $data_path has no database; migrate the stopped volume before production startup"
    fi

    if [[ -n "$acknowledgement_fingerprint" ]]; then
        [[ "$acknowledgement_fingerprint" == "$legacy_volume_fingerprint" ]] \
            || die "the supplied fingerprint does not identify the current legacy volume"
        running_legacy_containers="$(
            docker ps --quiet --filter "volume=$legacy_volume"
        )" || die "could not determine whether legacy volume $legacy_volume is in use"
        [[ -z "$running_legacy_containers" ]] \
            || die "legacy volume $legacy_volume is still mounted by a running container; stop it before acknowledging a cold migration"

        write_legacy_volume_ack \
            "$legacy_volume_ack_file" \
            "$legacy_volume" \
            "$legacy_volume_fingerprint" \
            "$data_path" \
            "$docker_engine_id" \
            || die "could not write $legacy_volume_ack_file"
        printf 'Recorded verified migration from %s to %s.\n' "$legacy_volume" "$data_path"
        printf 'Acknowledgement: %s\n' "$legacy_volume_ack_file"
        printf 'The legacy volume was retained. Run ./run-prod.sh normally to start production.\n'
        exit 0
    fi

    if ! legacy_volume_ack_matches \
        "$legacy_volume_ack_file" \
        "$legacy_volume" \
        "$legacy_volume_fingerprint" \
        "$data_path" \
        "$docker_engine_id"; then
        printf 'Legacy volume: %s\n' "$legacy_volume" >&2
        printf 'Legacy volume fingerprint: %s\n' "$legacy_volume_fingerprint" >&2
        printf 'Expected acknowledgement: %s\n' "$legacy_volume_ack_file" >&2
        printf 'After a stopped, cold migration and graph verification, record it with:\n' >&2
        printf '  ./run-prod.sh --acknowledge-legacy-volume %s\n' \
            "$legacy_volume_fingerprint" >&2
        die "production startup is blocked until the exact retained legacy volume is acknowledged"
    fi

    printf 'warning: retained legacy volume %s exists; verified bind data at %s will be used\n' \
        "$legacy_volume" "$data_path" >&2
elif [[ -n "$acknowledgement_fingerprint" ]]; then
    die "no legacy volume named $legacy_volume exists; no migration acknowledgement is needed"
fi
unset storage_config project_name data_mount_type data_path legacy_volume
unset docker_engine_id legacy_volume_fingerprint legacy_volume_ack_file
unset acknowledgement_fingerprint running_legacy_containers

neo4j_password="$(compose_env_value NEO4J_PASSWORD)" \
    || die "NEO4J_PASSWORD is not configured"
if [[ "$neo4j_password" == "password" || ${#neo4j_password} -lt 16 ]]; then
    die "production NEO4J_PASSWORD must be non-default and at least 16 characters"
fi
unset neo4j_password

jwt_secret="$(compose_env_value JWT_SECRET_KEY)" \
    || die "JWT_SECRET_KEY is not configured"
if [[ "$jwt_secret" == "generate_me" || ${#jwt_secret} -lt 32 ]]; then
    die "production JWT_SECRET_KEY must be persistent and at least 32 characters"
fi
unset jwt_secret

owner_username="$(compose_env_value OAUTH_OWNER_USERNAME)" \
    || die "OAUTH_OWNER_USERNAME is not configured"
[[ -n "$owner_username" ]] || die "OAUTH_OWNER_USERNAME must not be empty"
unset owner_username

owner_password="$(compose_env_value OAUTH_OWNER_PASSWORD)" \
    || die "OAUTH_OWNER_PASSWORD is not configured"
if [[ "$owner_password" == "password" || "$owner_password" == "generate_me" \
    || "$owner_password" == your_* || ${#owner_password} -lt 16 ]]; then
    die "production OAUTH_OWNER_PASSWORD must be non-default and at least 16 characters"
fi
unset owner_password

voyage_api_key="$(compose_env_value VOYAGE_API_KEY)" \
    || die "VOYAGE_API_KEY is not configured"
if [[ -z "$voyage_api_key" || "$voyage_api_key" == your_* ]]; then
    die "production VOYAGE_API_KEY must be configured"
fi
unset voyage_api_key

public_base_url="$(compose_env_value PUBLIC_BASE_URL)" \
    || die "PUBLIC_BASE_URL is not configured"
if ! python3 -c '
import sys
from urllib.parse import urlsplit

try:
    url = urlsplit(sys.argv[1])
    valid = (
        url.scheme == "https"
        and bool(url.hostname)
        and not url.username
        and not url.password
        and url.path in ("", "/")
        and not url.query
        and not url.fragment
    )
except ValueError:
    valid = False
raise SystemExit(0 if valid else 1)
' "$public_base_url"; then
    die "production PUBLIC_BASE_URL must be an https origin without credentials, path, query, or fragment"
fi
unset public_base_url

printf 'Building the production image...\n'
"${compose[@]}" build

printf 'Starting the production stack...\n'
if ! "${compose[@]}" up -d --wait --wait-timeout "$STARTUP_TIMEOUT_SECONDS"; then
    "${compose[@]}" ps >&2 || true
    "${compose[@]}" logs --tail 100 >&2 || true
    die "the production stack did not become healthy within ${STARTUP_TIMEOUT_SECONDS}s"
fi

"${compose[@]}" ps

if command -v systemctl >/dev/null 2>&1 \
    && command -v cloudflared >/dev/null 2>&1 \
    && [[ -f "$TUNNEL_CONFIG" ]] \
    && systemctl --user is-active --quiet cloudflared-memory-palace.service \
    && cloudflared tunnel --config "$TUNNEL_CONFIG" ready >/dev/null 2>&1; then
    printf 'Cloudflare Tunnel connector is ready.\n'
else
    printf 'Cloudflare Tunnel readiness was not confirmed; run scripts/infrastructure/setup-cloudflare-tunnel.sh.\n'
fi

printf 'Deployment complete. The API is bound to loopback only.\n'
printf 'Follow logs with: docker compose --env-file %q -f docker-compose.prod.yml logs -f\n' "$ENV_FILE"

#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
readonly TUNNEL_NAME="${TUNNEL_NAME:-memory-palace}"
readonly CONFIG_DIR="$HOME/.cloudflared"
readonly CONFIG_FILE="$CONFIG_DIR/memory-palace.yml"
readonly CONFIG_TEMPLATE="$SCRIPT_DIR/cloudflare-tunnel-config.yml"
readonly USER_UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
readonly USER_UNIT_FILE="$USER_UNIT_DIR/cloudflared-memory-palace.service"
readonly ENV_FILE="$PROJECT_ROOT/.env"

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "$1 is required"
}

usage() {
    cat <<'EOF'
Usage: setup-cloudflare-tunnel.sh [options] [hostname]

Options:
  --origin-port PORT       Override APP_PORT for the loopback origin.
  --replace-dns            Explicitly replace an existing DNS record.
  --replace-local-config   Replace a differing memory-palace.yml after review.
  --reuse-existing-tunnel Certify that an existing named tunnel is dedicated
                            to Memory Palace and may use this one-host ingress.
  --update-public-url      Explicitly replace a non-local PUBLIC_BASE_URL.
  -h, --help               Show this help.

Environment:
  TUNNEL_NAME              Cloudflare tunnel name (default: memory-palace).
EOF
}

trim() {
    local value=$1
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    printf '%s' "$value"
}

dotenv_value() {
    local key=$1
    local line value
    [[ -f "$ENV_FILE" ]] || return 1
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        if [[ "$line" =~ ^[[:space:]]*${key}[[:space:]]*=(.*)$ ]]; then
            value="$(trim "${BASH_REMATCH[1]}")"
            if [[ "$value" == \"*\" && "$value" == *\" ]]; then
                value="${value:1:${#value}-2}"
            elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
                value="${value:1:${#value}-2}"
            fi
            printf '%s' "$value"
            return 0
        fi
    done < "$ENV_FILE"
    return 1
}

set_dotenv_value() {
    local key=$1
    local value=$2
    local tmp_file
    tmp_file="$(mktemp "$PROJECT_ROOT/.env.tmp.XXXXXX")"
    awk -v key="$key" -v value="$value" '
        BEGIN { replaced = 0 }
        $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
            if (!replaced) {
                print key "=" value
                replaced = 1
            }
            next
        }
        { print }
        END {
            if (!replaced) {
                print key "=" value
            }
        }
    ' "$ENV_FILE" > "$tmp_file"
    chmod 600 "$tmp_file"
    mv -f -- "$tmp_file" "$ENV_FILE"
}

find_tunnel_id() {
    cloudflared tunnel list --name "$TUNNEL_NAME" --output json \
        | python3 -c '
import json
import sys

name = sys.argv[1]
matches = [item for item in json.load(sys.stdin) if item.get("name") == name and not item.get("deletedAt")]
if len(matches) > 1:
    raise SystemExit(f"multiple active tunnels named {name!r}")
if matches:
    print(matches[0]["id"])
' "$TUNNEL_NAME"
}

require_command cloudflared
require_command cmp
require_command id
require_command python3
require_command systemctl

replace_dns=false
replace_local_config=false
reuse_existing_tunnel=false
update_public_url=false
origin_port=""
domain=""
while (($# > 0)); do
    case "$1" in
        --origin-port)
            [[ $# -ge 2 ]] || die "--origin-port requires a value"
            origin_port=$2
            shift 2
            ;;
        --replace-dns)
            replace_dns=true
            shift
            ;;
        --replace-local-config)
            replace_local_config=true
            shift
            ;;
        --reuse-existing-tunnel)
            reuse_existing_tunnel=true
            shift
            ;;
        --update-public-url)
            update_public_url=true
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        --*)
            die "unknown option: $1"
            ;;
        *)
            [[ -z "$domain" ]] || die "only one hostname may be supplied"
            domain=$1
            shift
            ;;
    esac
done

[[ -f "$ENV_FILE" ]] || die "$ENV_FILE is missing; run setup.sh first"
chmod 600 "$ENV_FILE"

install -d -m 0700 "$CONFIG_DIR"

if [[ ! -s "$CONFIG_DIR/cert.pem" ]]; then
    printf 'Cloudflare authentication is required. Running cloudflared tunnel login...\n'
    cloudflared tunnel login
fi
[[ -s "$CONFIG_DIR/cert.pem" ]] || die "Cloudflare origin certificate was not created"

if [[ -z "$domain" ]]; then
    read -r -p "Public hostname (for example, memory.example.com): " domain
fi
domain="${domain,,}"
if [[ ${#domain} -gt 253 \
    || ! "$domain" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$ ]]; then
    die "invalid DNS hostname: $domain"
fi

if [[ -z "$origin_port" ]]; then
    origin_port="${APP_PORT:-$(dotenv_value APP_PORT || true)}"
fi
origin_port="${origin_port:-8000}"
[[ "$origin_port" =~ ^[0-9]+$ ]] || die "origin port must be an integer"
origin_port=$((10#$origin_port))
((origin_port >= 1 && origin_port <= 65535)) || die "origin port must be between 1 and 65535"

desired_public_url="https://$domain"
current_public_url="$(dotenv_value PUBLIC_BASE_URL || true)"
public_url_update_required=false
case "$current_public_url" in
    "$desired_public_url" | "$desired_public_url/")
        ;;
    "" | http://localhost | http://localhost:* | http://127.0.0.1 | http://127.0.0.1:*)
        public_url_update_required=true
        ;;
    *)
        if [[ "$update_public_url" != true ]]; then
            die "PUBLIC_BASE_URL is $current_public_url; rerun with --update-public-url to change the OAuth issuer"
        fi
        public_url_update_required=true
        ;;
esac

tunnel_id="$(find_tunnel_id)"
if [[ -n "$tunnel_id" ]]; then
    if [[ "$reuse_existing_tunnel" != true ]]; then
        die "tunnel '$TUNNEL_NAME' already exists; choose a unique TUNNEL_NAME or pass --reuse-existing-tunnel only after confirming it is dedicated to Memory Palace"
    fi
    printf '%s\n' \
        "WARNING: reusing tunnel '$TUNNEL_NAME' certifies it is dedicated to Memory Palace." \
        "Starting connectors for one tunnel with different ingress files can route requests" \
        "to this one-host configuration and cause intermittent HTTP 404 responses." >&2
else
    if [[ -e "$CONFIG_FILE" && "$replace_local_config" != true ]]; then
        die "$CONFIG_FILE already exists but tunnel '$TUNNEL_NAME' does not; review it and pass --replace-local-config before creating a replacement tunnel"
    fi
    printf 'Creating Cloudflare tunnel %s...\n' "$TUNNEL_NAME"
    cloudflared tunnel create "$TUNNEL_NAME"
    tunnel_id="$(find_tunnel_id)"
fi
[[ "$tunnel_id" =~ ^[0-9a-fA-F-]{36}$ ]] || die "could not resolve a valid tunnel UUID"

credentials_file="$CONFIG_DIR/${tunnel_id}.json"
[[ -s "$credentials_file" ]] || die "tunnel credentials not found at $credentials_file"
chmod 600 "$credentials_file" "$CONFIG_DIR/cert.pem"

rendered="$(<"$CONFIG_TEMPLATE")"
rendered="${rendered//__TUNNEL_ID__/$tunnel_id}"
rendered="${rendered//__CREDENTIALS_FILE__/$credentials_file}"
rendered="${rendered//__HOSTNAME__/$domain}"
rendered="${rendered//__ORIGIN_PORT__/$origin_port}"
config_tmp="$(mktemp "$CONFIG_DIR/memory-palace.yml.tmp.XXXXXX")"
cleanup() {
    if [[ -n "${config_tmp:-}" && -e "$config_tmp" ]]; then
        rm -f -- "$config_tmp"
    fi
}
trap cleanup EXIT
printf '%s\n' "$rendered" > "$config_tmp"
chmod 600 "$config_tmp"
cloudflared tunnel --config "$config_tmp" ingress validate

config_changed=true
if [[ -e "$CONFIG_FILE" ]]; then
    [[ -f "$CONFIG_FILE" && ! -L "$CONFIG_FILE" ]] \
        || die "$CONFIG_FILE must be a regular, non-symlink file"
    if cmp -s -- "$CONFIG_FILE" "$config_tmp"; then
        config_changed=false
    elif [[ "$replace_local_config" != true ]]; then
        die "$CONFIG_FILE differs from the requested one-host ingress; review it and pass --replace-local-config to replace it (shared ingress is never merged automatically)"
    else
        printf 'WARNING: replacing the reviewed, differing local config at %s.\n' "$CONFIG_FILE" >&2
    fi
fi

if [[ "$public_url_update_required" == true ]]; then
    backup="$ENV_FILE.backup-$(date -u +%Y%m%dT%H%M%SZ)"
    cp -p -- "$ENV_FILE" "$backup"
    chmod 600 "$backup"
    set_dotenv_value PUBLIC_BASE_URL "$desired_public_url"
    printf 'Updated PUBLIC_BASE_URL to %s (backup: %s).\n' "$desired_public_url" "$backup"
fi

if [[ "$config_changed" == true ]]; then
    mv -f -- "$config_tmp" "$CONFIG_FILE"
else
    rm -f -- "$config_tmp"
fi
config_tmp=""

if [[ "$replace_dns" == true ]]; then
    cloudflared tunnel route dns --overwrite-dns "$TUNNEL_NAME" "$domain"
elif ! cloudflared tunnel route dns "$TUNNEL_NAME" "$domain"; then
    die "DNS routing failed; use --replace-dns only if this hostname's existing record may be replaced"
fi

install -d -m 0700 "$USER_UNIT_DIR"
install -m 0600 "$SCRIPT_DIR/cloudflared-memory-palace.service" "$USER_UNIT_FILE"
systemctl --user daemon-reload
systemctl --user enable cloudflared-memory-palace.service
if systemctl --user is-active --quiet cloudflared-memory-palace.service; then
    systemctl --user restart cloudflared-memory-palace.service
else
    systemctl --user start cloudflared-memory-palace.service
fi

tunnel_ready=false
for ((attempt = 1; attempt <= 30; attempt++)); do
    if systemctl --user is-active --quiet cloudflared-memory-palace.service \
        && cloudflared tunnel --config "$CONFIG_FILE" ready >/dev/null 2>&1; then
        tunnel_ready=true
        break
    fi
    sleep 1
done
if [[ "$tunnel_ready" != true ]]; then
    if command -v journalctl >/dev/null 2>&1; then
        journalctl --user -u cloudflared-memory-palace.service --no-pager -n 50 >&2 || true
    fi
    die "the Cloudflare tunnel did not establish a connector within 30 seconds"
fi

printf 'Tunnel ready at https://%s/mcp\n' "$domain"
printf 'Origin: http://127.0.0.1:%s\n' "$origin_port"
printf 'For service startup without a login session, enable user lingering once:\n'
printf '  sudo loginctl enable-linger %s\n' "$(id -un)"

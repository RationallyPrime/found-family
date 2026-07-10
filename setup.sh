#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly COPIER_VERSION="9.7.1"
readonly PREK_VERSION="0.3.8"
readonly STARTUP_TIMEOUT_SECONDS="${STARTUP_TIMEOUT_SECONDS:-120}"
readonly GENERATED_ENV_FILE="$ROOT_DIR/memory-palace.env"

cd "$ROOT_DIR"
export MEMORY_PALACE_ENV_FILE="$ROOT_DIR/.env"

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "$1 is required"
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
    done < .env
    return 1
}

set_dotenv_value() {
    local key=$1
    local value=$2
    local tmp_file
    tmp_file="$(mktemp "$ROOT_DIR/.env.tmp.XXXXXX")"

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
    ' .env > "$tmp_file"

    chmod 600 "$tmp_file"
    mv -f -- "$tmp_file" .env
}

configure_environment() {
    local backup=""
    local previous_db_password=""
    local previous_jwt_secret=""
    local previous_owner_password=""
    local previous_owner_username=""
    local reply=""

    if [[ -f .env ]]; then
        read -r -p ".env already exists. Reconfigure it? [y/N] " reply
        if [[ ! "$reply" =~ ^[Yy]$ ]]; then
            printf 'Keeping the existing .env.\n'
            return 0
        fi

        previous_db_password="$(dotenv_value NEO4J_PASSWORD || true)"
        previous_jwt_secret="$(dotenv_value JWT_SECRET_KEY || true)"
        previous_owner_password="$(dotenv_value OAUTH_OWNER_PASSWORD || true)"
        previous_owner_username="$(dotenv_value OAUTH_OWNER_USERNAME || true)"
        backup=".env.backup-$(date -u +%Y%m%dT%H%M%SZ)"
        mv -- .env "$backup"
        chmod 600 "$backup"
        printf 'Existing configuration backed up to %s.\n' "$backup"
    fi

    rm -f -- "$GENERATED_ENV_FILE"
    if ! uvx --from "copier==${COPIER_VERSION}" copier copy \
        . . --answers-file .copier-answers.yml; then
        rm -f -- "$GENERATED_ENV_FILE" .env
        if [[ -n "$backup" ]]; then
            mv -- "$backup" .env
        fi
        die "configuration generation failed"
    fi

    if [[ ! -f "$GENERATED_ENV_FILE" ]]; then
        if [[ -n "$backup" ]]; then
            mv -- "$backup" .env
        fi
        die "Copier did not generate memory-palace.env"
    fi

    mv -- "$GENERATED_ENV_FILE" .env
    chmod 600 .env

    # These secrets identify durable state. Reconfiguration may change profile
    # data and provider keys, but it must not silently orphan a Neo4j volume or
    # invalidate every issued OAuth token.
    if [[ -n "$previous_db_password" ]]; then
        set_dotenv_value NEO4J_PASSWORD "$previous_db_password"
    fi
    if [[ -n "$previous_jwt_secret" ]]; then
        set_dotenv_value JWT_SECRET_KEY "$previous_jwt_secret"
    fi
    if [[ -n "$previous_owner_password" ]]; then
        set_dotenv_value OAUTH_OWNER_PASSWORD "$previous_owner_password"
    fi
    if [[ -n "$previous_owner_username" ]]; then
        set_dotenv_value OAUTH_OWNER_USERNAME "$previous_owner_username"
    fi
    unset previous_db_password previous_jwt_secret previous_owner_password previous_owner_username
}

ensure_jwt_secret() {
    local jwt_secret=""
    jwt_secret="$(dotenv_value JWT_SECRET_KEY || true)"
    if [[ ${#jwt_secret} -ge 32 && "$jwt_secret" != "generate_me" ]]; then
        return 0
    fi

    jwt_secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
    set_dotenv_value JWT_SECRET_KEY "$jwt_secret"
    unset jwt_secret
    printf 'Generated a persistent JWT signing key.\n'
}

ensure_database_password() {
    local password=""
    password="$(dotenv_value NEO4J_PASSWORD || true)"
    if [[ -z "$password" ]]; then
        password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
        set_dotenv_value NEO4J_PASSWORD "$password"
        printf 'Generated a strong Neo4j password.\n'
    elif [[ "$password" == "password" || "$password" == your_* || ${#password} -lt 16 ]]; then
        die "choose a non-default NEO4J_PASSWORD of at least 16 characters, then rerun setup"
    fi
    unset password
}

ensure_oauth_owner_credentials() {
    local password=""
    local username=""

    username="$(dotenv_value OAUTH_OWNER_USERNAME || true)"
    if [[ -z "$username" ]]; then
        set_dotenv_value OAUTH_OWNER_USERNAME owner
    fi

    password="$(dotenv_value OAUTH_OWNER_PASSWORD || true)"
    if [[ -z "$password" ]]; then
        password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
        set_dotenv_value OAUTH_OWNER_PASSWORD "$password"
        printf 'Generated a strong OAuth owner password.\n'
    elif [[ "$password" == "password" || "$password" == "generate_me" \
        || "$password" == your_* || ${#password} -lt 16 ]]; then
        die "choose an OAUTH_OWNER_PASSWORD of at least 16 characters, then rerun setup"
    fi
    unset password username
}

require_command docker
require_command uv
require_command python3
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"

configure_environment
[[ -f .env ]] || die "configuration did not create .env"
chmod 600 .env
ensure_jwt_secret
ensure_database_password
ensure_oauth_owner_credentials

printf 'Installing the locked development environment...\n'
uv sync --frozen

prek_bin="$(command -v prek || true)"
prek_version_output=""
if [[ -n "$prek_bin" ]]; then
    prek_version_output="$("$prek_bin" --version || true)"
fi
if [[ "$prek_version_output" != "prek $PREK_VERSION" ]]; then
    printf 'Installing prek %s...\n' "$PREK_VERSION"
    uv tool install --force "prek==$PREK_VERSION"
    prek_bin="$(uv tool dir --bin)/prek"
fi
[[ -x "$prek_bin" ]] || die "prek installation did not create an executable"
"$prek_bin" validate-config .pre-commit-config.yaml
"$prek_bin" install --hook-type pre-commit --hook-type pre-push
unset prek_bin prek_version_output

printf 'Validating Compose configuration...\n'
docker compose config --quiet

printf 'Starting Neo4j...\n'
docker compose up -d --wait --wait-timeout "$STARTUP_TIMEOUT_SECONDS" neo4j

voyage_key="$(dotenv_value VOYAGE_API_KEY || true)"
if [[ -z "$voyage_key" || "$voyage_key" == your_* ]]; then
    printf 'warning: VOYAGE_API_KEY still needs to be configured in .env\n' >&2
fi
unset voyage_key

printf 'Setup complete. Start development with ./run.sh.\n'

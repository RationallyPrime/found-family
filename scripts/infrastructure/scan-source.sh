#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly TRIVY_IMAGE="${1:?pass the pinned Trivy image reference}"

command -v docker >/dev/null 2>&1 || {
    printf 'error: docker is required\n' >&2
    exit 1
}
command -v git >/dev/null 2>&1 || {
    printf 'error: git is required\n' >&2
    exit 1
}

scan_root="$(mktemp -d /tmp/memory-palace-source-scan.XXXXXX)"
cleanup() {
    local status=$?
    trap - EXIT INT TERM
    rm -rf -- "$scan_root"
    exit "$status"
}
trap cleanup EXIT INT TERM

cd "$ROOT_DIR"
while IFS= read -r -d '' path; do
    [[ -f "$path" || -L "$path" ]] || continue
    install -d -m 0700 "$scan_root/$(dirname -- "$path")"
    cp -a -- "$path" "$scan_root/$path"
done < <(git ls-files -z --cached --others --exclude-standard)

docker run --rm \
    --cpus "${TRIVY_CPUS:-1.0}" \
    --memory "${TRIVY_MEMORY:-2g}" \
    --pids-limit 256 \
    --volume "$scan_root:/workspace:ro" \
    --volume memory-palace-trivy-cache:/root/.cache/trivy \
    --workdir /workspace \
    "$TRIVY_IMAGE" fs \
    --scanners vuln,secret,misconfig \
    --severity HIGH,CRITICAL \
    --exit-code 1 \
    .

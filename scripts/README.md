# Scripts Directory

This directory contains utility scripts for managing, importing, and maintaining the Memory Palace system.

## Data Import Scripts

### `import_curated_memories.py`
Import carefully selected conversation memories into the palace. This script processes curated JSON files containing high-value conversations.

**Usage:**
```bash
uv run python scripts/import_curated_memories.py
```

**Input Format:** Expects JSON files with conversation structure
**Purpose:** Bulk import of pre-selected important memories

### `import_friendship_memories.py`
Import friendship-related memories and relationship context. Specifically designed to process memories from friendship conversations.

**Usage:**
```bash
uv run python scripts/import_friendship_memories.py
```

**Purpose:** Build relationship context and emotional connections

### `import_tiered_memories.py` 
Import memories with different importance tiers (core, important, contextual, reference).

**Usage:**
```bash
uv run python scripts/import_tiered_memories.py
```

**Tiers:**
- **Core**: Essential memories that define the relationship
- **Important**: Significant but not foundational memories
- **Contextual**: Supporting context and background
- **Reference**: Information for future lookup

## Data Management Scripts

### `backup_graph.py`
Dump the entire graph (nodes + relationships, embeddings included) to a
timestamped JSON file under `data/backups/`. Run before any graph surgery.
Backups are read from one Neo4j snapshot, atomically published, exclude
ephemeral OAuth codes, and are owner-readable only.

**Usage:**
```bash
uv run python scripts/backup_graph.py
```

### `migrate_legacy_graph.py`
The Aug-2025 → 2026 schema migration (rescued founding Message nodes,
unified edges, backfilled lifecycle fields). Idempotent; already run.

**Usage:**
```bash
uv run python scripts/migrate_legacy_graph.py          # read-only plan
uv run python scripts/migrate_legacy_graph.py --apply  # explicit mutation
```

### `adopt_embedding_provenance.py`

Stamp a legacy corpus with its proven embedding model and dimensions without
regenerating vectors. This is intentionally not automatic: take a backup and
confirm deployment history before adoption. The command rejects mixed vector
dimensions, conflicting model metadata, and conflicting schema descriptors.

```bash
uv run python scripts/adopt_embedding_provenance.py \
  --model voyage-4-large --dimensions 1024          # read-only proof
uv run python scripts/adopt_embedding_provenance.py \
  --model voyage-4-large --dimensions 1024 --apply  # metadata-only migration
```

### `smoke_mcp.py` and `smoke_oauth.py`

`smoke_mcp.py` is read-only: it checks health, discovery, MCP initialization,
and the complete tool list. `smoke_oauth.py` additionally exercises a
Codex-style native client with a loopback callback: DCR, owner approval, S256
PKCE, bearer-authenticated MCP, refresh rotation, and replay rejection. The
OAuth smoke password is accepted only through
`MEMORY_PALACE_SMOKE_OWNER_PASSWORD`; tokens are never printed.

```bash
uv run python scripts/smoke_mcp.py --target http://127.0.0.1:8000

MEMORY_PALACE_SMOKE_OWNER_PASSWORD='owner-password' \
  uv run python scripts/smoke_oauth.py \
    --target http://127.0.0.1:8000 \
    --redirect-uri http://127.0.0.1:43119/callback/codex-smoke \
    --owner-username owner \
    --cleanup-local-state
```

Use the owner username and password configured in `.env`. This is a protocol
smoke test, not proof that the Claude.ai UI completed its interactive connector
flow; verify that separately by adding the public `/mcp` URL in Claude.ai and
completing browser authorization.

### `review_memories.py`
Interactive tool to review, audit, and potentially clean up stored memories.

**Usage:**
```bash
uv run python scripts/review_memories.py
```

**Features:**
- Browse stored memories
- Review memory quality
- Remove duplicate or low-quality memories
- Export memory subsets

## Infrastructure Scripts (`infrastructure/`)

### Cloudflare Tunnel Setup

#### `setup-cloudflare-tunnel.sh`
Fail-closed setup script for a dedicated Cloudflare tunnel that exposes Memory
Palace publicly.

**Usage:**
```bash
TUNNEL_NAME=memory-palace-personal \
  ./scripts/infrastructure/setup-cloudflare-tunnel.sh memory.example.com
```

**What it does:**
1. Authenticates `cloudflared` when needed
2. Updates `.env` `PUBLIC_BASE_URL` for the requested hostname
3. Creates a tunnel and a dedicated `~/.cloudflared/memory-palace.yml`
4. Validates ingress and configures DNS
5. Installs and starts the user-level systemd service

An existing tunnel is rejected unless `--reuse-existing-tunnel` explicitly
certifies that it is dedicated to Memory Palace. Reusing a shared tunnel with
different one-host ingress files can produce intermittent 404 responses across
connectors. A differing local config is independently rejected unless
`--replace-local-config` is passed after review. The installer never modifies
`~/.cloudflared/config.yml` or merges arbitrary shared ingress.

**Prerequisites:**
- Cloudflare account
- Domain configured in Cloudflare
- cloudflared CLI installed

#### `cloudflare-tunnel-config.yml`
Template configuration file for Cloudflare tunnel. Defines:
- Ingress rules for routing
- Service mappings
- Security settings
- TLS configuration

#### `cloudflared-memory-palace.service`
User-level systemd service installed by `setup-cloudflare-tunnel.sh`.

**Management:**
```bash
# Check status
systemctl --user status cloudflared-memory-palace

# View logs
journalctl --user -u cloudflared-memory-palace -f

# Restart tunnel
systemctl --user restart cloudflared-memory-palace
```

## Environment Requirements

All scripts expect:
- `.env` file with required API keys (VOYAGE_API_KEY, NEO4J_*, etc.)
- Neo4j database running (via docker-compose)
- Python dependencies installed (`uv sync`)
- FastAPI application accessible at http://localhost:8000

## Common Operations

### Full Memory Import Workflow
```bash
# 1. Ensure services are running
./run.sh

# 2. Import core memories first
uv run python scripts/import_tiered_memories.py

# 3. Import friendship context
uv run python scripts/import_friendship_memories.py

# 4. Import curated conversations
uv run python scripts/import_curated_memories.py

# 5. Review imported memories
uv run python scripts/review_memories.py
```

## Troubleshooting

### Import Scripts Failing
- Check Neo4j is running: `docker compose ps`
- Verify readiness: `curl --fail http://localhost:8000/ready`
- Ensure VOYAGE_API_KEY is set in `.env`
- Check input file formats match expected structure

### Cloudflare Tunnel Issues
- Verify cloudflared is installed: `cloudflared version`
- Check tunnel status: `cloudflared tunnel list`
- Review logs: `journalctl --user -u cloudflared-memory-palace.service -n 50`
- Ensure domain is configured in Cloudflare dashboard

## Development Notes

When adding new scripts:
1. Follow naming convention: `{action}_{target}.py`
2. Include proper docstrings and type hints
3. Use the shared MemoryService for database operations
4. Add error handling and logging
5. Update this README with usage instructions

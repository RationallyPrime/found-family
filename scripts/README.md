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

### `migrate_memory_types.py`
Migrate memory data between different formats and update schema versions.

**Usage:**
```bash
uv run python scripts/migrate_memory_types.py
```

**Purpose:** Database schema evolution and data format updates

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
Interactive setup script for configuring Cloudflare tunnels to expose Memory Palace publicly.

**Usage:**
```bash
./scripts/infrastructure/setup-cloudflare-tunnel.sh
```

**What it does:**
1. Checks for cloudflared installation
2. Creates tunnel configuration
3. Sets up systemd service
4. Configures ingress rules
5. Provides public URL for remote access

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
Systemd service file for running the Cloudflare tunnel as a daemon.

**Installation:**
```bash
sudo cp scripts/infrastructure/cloudflared-memory-palace.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cloudflared-memory-palace
sudo systemctl start cloudflared-memory-palace
```

**Management:**
```bash
# Check status
sudo systemctl status cloudflared-memory-palace

# View logs
sudo journalctl -u cloudflared-memory-palace -f

# Restart tunnel
sudo systemctl restart cloudflared-memory-palace
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

### Setting Up Remote Access
```bash
# 1. Configure Cloudflare tunnel
./scripts/infrastructure/setup-cloudflare-tunnel.sh

# 2. Install and start service
sudo cp scripts/infrastructure/cloudflared-memory-palace.service /etc/systemd/system/
sudo systemctl enable --now cloudflared-memory-palace

# 3. Verify tunnel is running
sudo systemctl status cloudflared-memory-palace
```

## Troubleshooting

### Import Scripts Failing
- Check Neo4j is running: `docker compose ps`
- Verify API is accessible: `curl http://localhost:8000/api/v1/memory/health`
- Ensure VOYAGE_API_KEY is set in `.env`
- Check input file formats match expected structure

### Cloudflare Tunnel Issues
- Verify cloudflared is installed: `cloudflared version`
- Check tunnel status: `cloudflared tunnel list`
- Review logs: `sudo journalctl -u cloudflared-memory-palace -n 50`
- Ensure domain is configured in Cloudflare dashboard

## Development Notes

When adding new scripts:
1. Follow naming convention: `{action}_{target}.py`
2. Include proper docstrings and type hints
3. Use the shared MemoryService for database operations
4. Add error handling and logging
5. Update this README with usage instructions
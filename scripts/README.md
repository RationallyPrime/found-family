# Scripts Directory

This directory contains utility scripts for managing Memory Palace.

## Data Management Scripts

- `import_curated_memories.py` - Import curated conversation memories into the palace
- `import_friendship_memories.py` - Import friendship-related memories 
- `import_tiered_memories.py` - Import memories with different importance tiers
- `migrate_memory_types.py` - Migrate memory data between different formats
- `review_memories.py` - Review and audit stored memories

## Infrastructure Scripts (`infrastructure/`)

- `setup-cloudflare-tunnel.sh` - Interactive setup for Cloudflare tunnels (replaces Tailscale)
- `cloudflare-tunnel-config.yml` - Cloudflare tunnel configuration template
- `cloudflared-memory-palace.service` - Systemd service file for tunnel daemon

## Usage

Run infrastructure setup:
```bash
./scripts/infrastructure/setup-cloudflare-tunnel.sh
```

Run data management scripts:
```bash
uv run python scripts/import_curated_memories.py
```
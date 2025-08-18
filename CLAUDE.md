# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Memory Palace is a Neo4j-backed persistence system for AI continuity of experience. It provides semantic memory storage and retrieval using graph relationships and vector embeddings.

## Development Commands

### Starting the Application

**Development mode (with hot reload):**
```bash
./run.sh  # Starts Neo4j + FastAPI with auto-reload
```

**Production mode (containerized):**
```bash
./run-prod.sh  # Runs all services in Docker containers
```

**Manual start:**
```bash
docker compose up -d neo4j  # Start Neo4j
uv run uvicorn memory_palace.main:app --reload --host 0.0.0.0 --port 8000
```

### Code Quality
```bash
# Format code
uv run ruff format src/

# Lint code
uv run ruff check src/ --fix

# Type checking (both work)
uv run pyright  # Full Pyright
uv run ty check  # Alias for convenience
```

### Testing
```bash
# Run MCP integration tests
python tests/test_mcp.py

# Run unit tests (when added)
uv run pytest

# Test API endpoints
curl -X POST http://localhost:8000/api/v1/memory/remember \
  -H "Content-Type: application/json" \
  -d '{"user_content": "test", "assistant_content": "response"}'

curl -X POST http://localhost:8000/api/v1/memory/recall \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "k": 5}'
```

## Architecture

### Core Components

1. **FastAPI Backend** (`src/memory_palace/main.py`)
   - REST API at `/api/v1`
   - MCP discovery at `/.well-known/mcp`
   - OAuth endpoints at `/oauth/*`
   - Streamable HTTP transport at `/mcp/stream`
   - CORS enabled for localhost:3000

2. **Neo4j Graph Database**
   - Stores memories as graph nodes
   - Relationships: Conversation → Turn → Message
   - Manual cosine similarity for vector search (Community Edition)

3. **Domain Models** (`src/memory_palace/domain/models/`)
   - `MemoryChunk`: Base memory unit with embedding and metadata
   - `Message`: Individual user/assistant message
   - `ConversationTurn`: Paired user-assistant exchange

4. **Services** (`src/memory_palace/services/`)
   - `MemoryService`: Core business logic for storing/retrieving memories
   - Handles embedding generation via Voyage AI
   - Manages Neo4j persistence

5. **Infrastructure** (`src/memory_palace/infrastructure/`)
   - `VoyageEmbeddingService`: Generates semantic embeddings
   - `Neo4jDriver`: Database connection management
   - Query builder pattern for complex graph queries

### API Endpoints

**Memory Operations:**
- `POST /api/v1/memory/remember` - Store a conversation turn
- `POST /api/v1/memory/recall` - Search memories semantically
- `POST /api/v1/unified_query` - Advanced query interface
- `GET /api/v1/memory/health` - Service health check

**OAuth/MCP Integration:**
- `GET /.well-known/mcp` - MCP discovery endpoint
- `GET /.well-known/oauth-authorization-server` - OAuth metadata
- `GET /oauth/authorize` - OAuth authorization
- `POST /oauth/token` - Token exchange
- `POST /oauth/register` - Dynamic client registration

**Admin Operations:**
- `POST /api/v1/admin/jobs/trigger/{job_id}` - Trigger background jobs
- `GET /api/v1/admin/cache/stats` - Cache statistics

### Environment Variables

Required in `.env`:
- `VOYAGE_API_KEY` - For embedding generation
- `NEO4J_URI` - Database connection (default: bolt://localhost:7687)
- `NEO4J_USER` / `NEO4J_PASSWORD` - Database auth
- `CLAUDE_API_KEY` - OAuth client secret for Claude.ai

Optional:
- `OPENAI_API_KEY` - OpenAI integration
- `ANTHROPIC_API_KEY` - Anthropic API access
- `GEMINI_API_KEY` - Google Gemini
- `LOGFIRE_TOKEN` - Structured logging
- `JWT_SECRET_KEY` - OAuth token signing

## Key Implementation Details

### Vector Search
Since Neo4j Community Edition lacks GDS plugin, cosine similarity is calculated manually in Cypher:
```cypher
reduce(dot = 0.0, i IN range(0, size($query_embedding)-1) |
   dot + m.embedding[i] * $query_embedding[i]) AS dotProduct
```

### Graph Structure
```
Conversation
  └─[HAS_TURN]→ Turn
      ├─[USER_MESSAGE]→ Message(role=user)
      └─[ASSISTANT_MESSAGE]→ Message(role=assistant)
```

### Embedding Storage
- 1536-dimensional vectors from Voyage AI
- Stored directly on Message nodes
- Query vs Document embedding types supported

## Development Workflow

1. Ensure `.env` file exists with API keys
2. Start services with `./run.sh`
3. Access Neo4j browser at http://localhost:7474 (neo4j/password)
4. API docs at http://localhost:8000/docs
5. Format and lint before committing: `uv run ruff format src/ && uv run ruff check src/`

## Project Organization

### Scripts Directory

```
scripts/
├── README.md                    # Script documentation
├── infrastructure/              # Infrastructure setup
│   ├── cloudflare-tunnel-config.yml
│   ├── cloudflared-memory-palace.service
│   └── setup-cloudflare-tunnel.sh
├── import_curated_memories.py   # Import curated memories
├── import_friendship_memories.py # Import friendship data
├── import_tiered_memories.py    # Import tiered memories
├── migrate_memory_types.py      # Database migrations
└── review_memories.py           # Memory review tool
```

### Production Deployment

1. **Set up Cloudflare Tunnel:**
```bash
./scripts/infrastructure/setup-cloudflare-tunnel.sh
```

2. **Install systemd service:**
```bash
sudo cp scripts/infrastructure/cloudflared-memory-palace.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cloudflared-memory-palace
sudo systemctl start cloudflared-memory-palace
```

3. **Run production stack:**
```bash
./run-prod.sh
```

## Future Features (In Progress)

- Topic clustering for automatic organization
- Salience scoring for memory importance  
- Ontology learning and evolution
- BERTopic integration for topic modeling
- HDBSCAN clustering for semantic groups

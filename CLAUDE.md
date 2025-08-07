# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Memory Palace is a Neo4j-backed persistence system for AI continuity of experience. It provides semantic memory storage and retrieval using graph relationships and vector embeddings.

## Development Commands

### Starting the Application
```bash
# Start all services (Neo4j + FastAPI)
./run.sh

# Or manually:
docker compose up -d neo4j  # Start Neo4j
uv run uvicorn memory_palace.main:app --reload --host 0.0.0.0 --port 8000
```

### Code Quality
```bash
# Format code
uv run ruff format src/

# Lint code
uv run ruff check src/ --fix

# Type checking
uv run ty check
```

### Testing
```bash
# Run tests (when added)
uv run pytest

# Test API endpoints
curl -X POST http://localhost:8000/api/v1/memory/remember \
  -H "Content-Type: application/json" \
  -d @test_memory.json

curl -X POST http://localhost:8000/api/v1/memory/recall \
  -H "Content-Type: application/json" \
  -d @test_recall.json
```

## Architecture

### Core Components

1. **FastAPI Backend** (`src/memory_palace/main.py`)
   - REST API at `/api/v1`
   - MCP integration at `/mcp`
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

- `POST /api/v1/memory/remember` - Store a conversation turn
- `POST /api/v1/memory/recall` - Search memories semantically
- `GET /api/v1/memory/health` - Service health check

### Environment Variables

Required in `.env`:
- `VOYAGE_API_KEY` - For embedding generation
- `NEO4J_URI` - Database connection (default: bolt://localhost:7687)
- `NEO4J_USER` / `NEO4J_PASSWORD` - Auth credentials

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

## Future Features (In Progress)

- Topic clustering for automatic organization
- Salience scoring for memory importance
- Ontology learning and evolution
- BERTopic integration for topic modeling
- HDBSCAN clustering for semantic groups

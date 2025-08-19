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

## Query Architecture Rules - CRITICAL

**NEVER WRITE RAW CYPHER IN SERVICE LAYER!** All database queries must follow these strict rules:

### Layer Responsibilities

1. **Service Layer** (`services/*.py`):
   - Business logic ONLY
   - NEVER contains raw Cypher queries
   - Calls repository methods or uses centralized queries
   - Uses `run_query()` helper with queries from infrastructure layer

2. **Repository Layer** (`infrastructure/repositories/*.py`):
   - Orchestrates database access
   - Uses centralized queries from `queries.py`
   - Uses query builder for dynamic queries
   - NEVER writes inline Cypher strings

3. **Infrastructure Layer** (`infrastructure/neo4j/*.py`):
   - `queries.py`: SINGLE SOURCE OF TRUTH for all Cypher queries
   - `query_builder.py`: Dynamic query construction
   - `filter_compiler.py`: Filter to Cypher compilation
   - ALL Cypher lives here, nowhere else

### Query Patterns

**CORRECT - Using centralized queries:**
```python
# In service layer
from memory_palace.infrastructure.neo4j.queries import MemoryQueries

query, _ = MemoryQueries.get_relationship_edges()
result = await self.run_query(query, memory_id=str(memory_id))
```

**WRONG - Raw Cypher in service:**
```python
# NEVER do this in service layer!
query = """
    MATCH (m:Memory {id: $memory_id})
    RETURN m
"""
result = await self.run_query(query, memory_id=str(memory_id))
```

### Adding New Queries

1. Add query method to appropriate class in `queries.py`:
   - `MemoryQueries`: Memory operations
   - `DreamJobQueries`: Background maintenance
   - `VectorIndexQueries`: Index management

2. Use `CypherQueryBuilder` for dynamic queries
3. Return `tuple[LiteralString, dict[str, Any]]`
4. Cast complex queries with `cast(LiteralString, query)`

## Error Handling Architecture - CRITICAL

**DO NOT USE TRY-EXCEPT BLOCKS!** The codebase has a sophisticated error handling system that must be used instead.

### Core Error System (`src/memory_palace/core/`)

The application uses a structured error handling system with:

1. **Custom Error Classes** (`core/errors.py`):
   - `ApplicationError`: Base class with error code, level, and structured details
   - `ServiceError`, `AuthenticationError`, `ProcessingError`, `RateLimitError`, `TimeoutError`
   - All errors include structured `ErrorDetails` models (Pydantic) for rich logging

2. **Error Codes** (`core/base.py`):
   - Categorized error codes (1xxx: General, 2xxx: API, 3xxx: Database, 4xxx: AI/ML, 5xxx: Infrastructure)
   - Each error has a specific code for tracking and debugging

3. **Error Details Models** (`core/base.py`):
   - `ServiceErrorDetails`: Service name, endpoint, status code, latency
   - `ValidationErrorDetails`: Field, actual value, expected type, constraint
   - `ResourceErrorDetails`: Resource ID, type, action
   - `DatabaseErrorDetails`: Query type, table, transaction ID
   - `AIServiceErrorDetails`: Model name, tokens, temperature

### How to Handle Errors

**NEVER write raw try-except blocks!** Instead:

1. **Use the decorators** (`core/decorators.py`):
```python
from memory_palace.core.decorators import with_error_handling
from memory_palace.core.errors import ServiceError
from memory_palace.core.base import ServiceErrorDetails

@with_error_handling(reraise=True)
async def call_external_service():
    # If this fails, raise a proper ApplicationError
    if response.status_code != 200:
        raise ServiceError(
            message="External service failed",
            details=ServiceErrorDetails(
                source="my_module",
                operation="api_call",
                service_name="voyage",
                endpoint="/embeddings",
                status_code=response.status_code
            )
        )
```

2. **Raise ApplicationError subclasses** with structured details:
```python
from memory_palace.core.errors import ProcessingError

# Instead of generic exceptions:
# raise Exception("Processing failed")  # WRONG!

# Use structured errors:
raise ProcessingError(
    message="Failed to process memory",
    details={
        "source": "memory_service",
        "operation": "store_memory",
        "memory_id": str(memory_id),
        "reason": "Embedding generation failed"
    }
)
```

3. **The system automatically**:
   - Captures full error context with trace IDs
   - Logs with structured data to Logfire
   - Preserves error details through the stack
   - Formats error responses for API endpoints

### Logging System (`core/logging/`)

Uses Logfire + structlog for structured logging:

1. **Get a logger**:
```python
from memory_palace.core.logging import get_logger
logger = get_logger(__name__)
```

2. **Log with structure** (automatic with decorators):
```python
logger.info("Operation completed", user_id=user_id, duration_ms=100)
```

3. **Context management**:
```python
from memory_palace.core.logging import update_log_context
update_log_context("request_id", request_id)
# All subsequent logs in this context include request_id
```

### Key Principles

1. **No naked try-except**: Always use `@with_error_handling` decorator
2. **Structured errors**: Always raise `ApplicationError` subclasses with details
3. **Rich context**: Include all relevant data in error details
4. **Let it propagate**: The system handles logging and response formatting
5. **One source of truth**: Error handling logic is centralized in `core/`

### Example: Correct Error Handling

```python
from memory_palace.core.decorators import with_error_handling
from memory_palace.core.errors import ServiceError
from memory_palace.core.base import ServiceErrorDetails
from memory_palace.core.logging import get_logger

logger = get_logger(__name__)

class VoyageEmbeddingService:
    @with_error_handling(reraise=True)  # Decorator handles all error logging
    async def generate_embedding(self, text: str) -> list[float]:
        logger.info("Generating embedding", text_length=len(text))
        
        response = await self.client.embeddings.create(...)
        
        if not response.data:
            # Raise structured error, no try-except needed!
            raise ServiceError(
                message="Voyage API returned empty response",
                details=ServiceErrorDetails(
                    source="voyage_embedding",
                    operation="generate_embedding",
                    service_name="voyage",
                    endpoint="/embeddings",
                    status_code=200,
                    latency_ms=response.headers.get("x-response-time")
                )
            )
        
        return response.data[0].embedding
```

## Future Features (In Progress)

- Topic clustering for automatic organization
- Salience scoring for memory importance  
- Ontology learning and evolution
- BERTopic integration for topic modeling
- HDBSCAN clustering for semantic groups

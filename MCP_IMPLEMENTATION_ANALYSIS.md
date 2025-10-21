# Memory Palace MCP Implementation Analysis

## Executive Summary

The Memory Palace system uses **FastApiMCP** to automatically convert FastAPI endpoints into MCP (Model Context Protocol) tools that are exposed via a streamable HTTP transport at `/mcp`. The system exposes 9 main tools for memory operations, queries, and admin functions. A critical issue exists with the **salience parameter** which has inconsistent defaults, silent auto-modification, and automatic memory eviction without user awareness.

---

## 1. MCP Server Setup and Architecture

### Core Implementation
- **File**: `/home/user/found-family/src/memory_palace/main.py` (lines 182-183)
- **Library**: `fastapi-mcp>=0.4.0` automatically discovers FastAPI endpoints and exposes them as MCP tools
- **Protocol**: MCP 2024-11-05, using streamable HTTP transport (not SSE)

```python
from fastapi_mcp import FastApiMCP

mcp = FastApiMCP(app)
mcp.mount_http()  # Creates MCP server at /mcp with HTTPS support
```

### Key Architectural Features
- **Auto-tool Generation**: Every FastAPI endpoint with proper documentation becomes an MCP tool
- **OAuth 2.0 Integration**: Full RFC 8414/RFC 7591 compliance with dynamic client registration
- **Lifespan Management**: Coordinates Neo4j driver, embedding service, clustering service, and dream jobs
- **Protocol Version**: 2024-11-05 with support for streamable HTTP transport

---

## 2. MCP Discovery and OAuth Endpoints

All discovery and OAuth endpoints are in `/home/user/found-family/src/memory_palace/api/endpoints/oauth.py`:

### Discovery Endpoints
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/.well-known/mcp` | GET/HEAD | MCP discovery - returns protocol version, endpoint URL, and transport type |
| `/.well-known/oauth-authorization-server` | GET/HEAD | OAuth 2.0 metadata and capabilities |
| `/.well-known/oauth-protected-resource` | GET/HEAD | Indicates MCP resource requires OAuth bearer token |

### OAuth Endpoints  
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/oauth/register` | POST | Dynamic Client Registration (RFC 7591) |
| `/oauth/authorize` | GET | Authorization request with PKCE support |
| `/oauth/token` | POST | Token exchange (authorization_code and refresh_token grants) |
| `/oauth/introspect` | POST | Token introspection (RFC 7662) |
| `/oauth/revoke` | POST | Token revocation (RFC 7009) |
| `/oauth/userinfo` | GET | OpenID Connect userinfo endpoint |
| `/.well-known/jwks.json` | GET | JSON Web Key Set (for verification) |

---

## 3. Exposed MCP Tools

FastApiMCP automatically generates tools from these FastAPI endpoints:

### Memory Operations (File: `/home/user/found-family/src/memory_palace/api/endpoints/memory.py`)

#### Tool 1: `remember` (POST `/api/v1/memory/remember`)
**Parameters**:
- `content` (string, required): The memory content
- `role` (string, required): "user" or "assistant"
- `conversation_id` (UUID, optional): Groups related memories
- `metadata` (object, optional): Additional structured data
- `ontology_path` (array of strings, optional): Hierarchical categorization
- `salience` (number, optional, 0.0-1.0): Importance rating

**Salience Scale Guidance**:
```
0.0-0.2: Background context, ambient information
0.3-0.4: Regular conversation, standard Q&A (DEFAULT: 0.3)
0.5-0.6: Interesting or useful information
0.7-0.8: Important preferences, decisions, learning moments
0.9-1.0: Critical memories - core beliefs, breakthroughs, defining moments
```

#### Tool 2: `remember_batch` (POST `/api/v1/memory/remember/batch`)
**Parameters**:
- `memories` (array of memory objects, required): Up to N memories to store
- `create_temporal_links` (boolean, optional): Whether to create PRECEDES relationships between consecutive memories

#### Tool 3: `recall` (POST `/api/v1/memory/recall`)
**Parameters**:
- `query` (string, required): Text to search for semantically
- `k` (integer, default: 10): Number of results to return
- `threshold` (number, default: 0.7): Similarity threshold (0.0-1.0)
- `min_salience` (number, optional): Minimum importance level for results
- `topic_ids` (array, optional): Filter by specific topic clusters
- `ontology_path` (array, optional): Filter by categorization path

### Query Operations (File: `/home/user/found-family/src/memory_palace/api/endpoints/unified_query.py`)

#### Tool 4: `query` (POST `/api/v1/unified/query`)
Advanced declarative query interface with specification-based filtering.

**Main Parameters**:
- `query.node_label` (string): Neo4j label to query (default: "Memory")
- `query.filters` (MemorySpecification): Specification-based filtering
- `query.similarity` (object): Semantic similarity search configuration
- `query.expand_relationships` (boolean): Whether to traverse graph relationships
- `query.relationship_depth` (integer, 1-3): How many hops to traverse
- `query.return_fields` (array): Which fields to include in results
- `query.order_by` (string): ORDER BY clause (e.g., "timestamp DESC")
- `query.limit` (integer, 1-100): Max results
- `query.skip` (integer): Pagination offset
- `query.timeout_ms` (integer, 1000-60000): Query timeout

### Admin Operations (File: `/home/user/found-family/src/memory_palace/api/endpoints/admin.py`)

#### Tool 5: `job_status` (GET `/admin/jobs/status`)
Returns the status of background dream job orchestrator.

#### Tool 6: `trigger` (POST `/admin/jobs/trigger/{job_id}`)
Manually trigger background jobs:
- `salience_refresh`: Apply salience decay and eviction
- `cluster_recent`: Assign topics to recent unassigned memories
- `nightly_recluster`: Full optimization recluster

#### Tool 7: `cache_stats` (GET `/admin/cache/stats`)
Get embedding cache statistics.

### Core Endpoints (File: `/home/user/found-family/src/memory_palace/api/endpoints/core.py`)

#### Tool 8: `health` (GET `/health`)
Health check endpoint.

### OAuth/MCP Discovery (File: `/home/user/found-family/src/memory_palace/api/endpoints/oauth.py`)

#### Tool 9: `mcp_discovery` (GET `/.well-known/mcp`)
Returns MCP discovery metadata for Claude.ai integration.

---

## 4. Salience Parameter: Usage and Problems

### Where Salience is Used

**1. Model Definition** (`/home/user/found-family/src/memory_palace/domain/models/memories.py`)
```python
class FriendUtterance(GraphModel):
    salience: float = 0.5  # DEFAULT HERE

class ClaudeUtterance(GraphModel):
    salience: float = 0.5  # DEFAULT HERE
```
**Problem**: Default is 0.5, inconsistent with endpoint default of 0.3

**2. API Validation** (`/home/user/found-family/src/memory_palace/api/endpoints/memory.py`, lines 44-69)
- Special validator that converts string numbers to floats
- Designed to handle MCP tool type coercion
- Could accept wrong types and fail validation silently

**3. Service Layer** (`/home/user/found-family/src/memory_palace/services/memory_service.py`)
- `remember_message()` (lines 73-142): Accepts salience, defaults to SALIENCE_DEFAULT if None
- `remember_turn()` (lines 163-219): Passes salience to BOTH user and assistant messages
- `_update_salience_from_relationships()` (lines 287-300): **AUTO-MODIFIES** salience by +0.1 per relationship!

**4. Search Filtering** (`/home/user/found-family/src/memory_palace/api/endpoints/memory.py`, lines 103, 193)
- `min_salience` parameter in recall
- Could filter out low-importance memories unintentionally

**5. Unified Query DSL** (`/home/user/found-family/src/memory_palace/api/endpoints/unified_query.py`)
- Salience available as return field and in specifications
- Complex query system increases chance of misuse

**6. Database Queries** (`/home/user/found-family/src/memory_palace/infrastructure/neo4j/queries.py`)
- Lines 95, 119, 131: Stored in all memory nodes
- Lines 276-318: Refresh and eviction queries manipulate salience

**7. Background Dream Jobs** (`/home/user/found-family/src/memory_palace/services/dream_jobs.py`)
- `refresh_salience()` job (lines 54-57, 88-106):
  - Runs **every 5 minutes** automatically
  - Applies exponential decay: `m.salience * $decay_factor`
  - **Evicts memories below 0.05 WITHOUT WARNING**

**8. Constants** (`/home/user/found-family/src/memory_palace/core/constants.py`)
```python
SALIENCE_DEFAULT = 0.3              # Default for new memories
SALIENCE_EVICTION_THRESHOLD = 0.05  # Auto-delete below this
SALIENCE_DECAY_FACTOR_DEFAULT = 0.0154  # 45-day half-life
SALIENCE_REFRESH_INTERVAL_MINUTES = 5  # How often decay runs
```

### Identified Critical Problems

#### Problem 1: Default Value Inconsistency
- **Models default**: 0.5
- **API endpoint default**: 0.3
- **Stored constants**: 0.3
- **Impact**: Confusion about what "normal" importance is, unexpected behavior

#### Problem 2: Silent Automatic Salience Boost
- `_update_salience_from_relationships()` increases salience by 0.1 for each relationship
- No user control or notification
- Could make memories artificially important
- **Impact**: User-provided salience values silently overridden

#### Problem 3: Automatic Memory Eviction
- Memories below 0.05 salience automatically deleted every 5 minutes
- No warning, no notification, no recovery
- 45-day half-life means old memories decay even if important
- **Impact**: Permanent data loss without user awareness or control

#### Problem 4: Type Coercion Issues
- Validator accepts strings and converts them to floats
- MCP tools might pass unexpected types
- Could cause subtle bugs in type handling
- **Impact**: Unexpected behavior from MCP tool calls

#### Problem 5: Unclear Semantics
- Scale description is complex and unintuitive
- Default of 0.3 for "regular conversation" is counterintuitive
- Relationship-based boost system is undocumented
- **Impact**: Users set incorrect salience values

#### Problem 6: No Configuration Control
- Decay factor hard-coded (0.0154 for 45-day half-life)
- Eviction threshold hard-coded (0.05)
- Refresh interval hard-coded (5 minutes)
- **Impact**: One-size-fits-all approach, no customization for different use cases

#### Problem 7: Missing Safeguards
- No way to lock important memories from decay
- No audit trail for evicted memories
- No preview before eviction
- **Impact**: Permanent data loss without recovery options

---

## 5. MCP Protocol Flow

```
Claude.ai Client
    ↓
GET /.well-known/mcp
    ↓ (Claude discovers MCP endpoint and protocol details)
    ↓
GET /.well-known/oauth-authorization-server
    ↓ (Claude learns about OAuth endpoints)
    ↓
POST /oauth/register
    ↓ (Claude registers as OAuth client)
    ↓
GET /oauth/authorize + POST /oauth/token
    ↓ (Claude gets OAuth bearer token)
    ↓
POST /mcp/stream (streamable HTTP with bearer token)
    ↓ (JSONL protocol messages)
    ↓
FastApiMCP receives MCP requests
    ↓
Tools discovered from FastAPI endpoints:
  - /api/v1/memory/remember
  - /api/v1/memory/recall
  - /api/v1/memory/remember/batch
  - /api/v1/unified/query
  - /admin/jobs/status
  - /admin/jobs/trigger/{job_id}
  - /admin/cache/stats
  - /health
    ↓
Services process tool inputs with salience parameters
    ↓
Neo4j stores/updates memories with salience
    ↓
Background jobs (every 5 min) apply decay and eviction
```

---

## 6. Tool Summary Table

| Tool | Endpoint | Method | Purpose | Salience Impact |
|------|----------|--------|---------|-----------------|
| `remember` | /api/v1/memory/remember | POST | Store single memory | Input param, gets auto-boosted if relationships exist |
| `remember_batch` | /api/v1/memory/remember/batch | POST | Store multiple memories | Input param, same auto-boost issues |
| `recall` | /api/v1/memory/recall | POST | Search memories | Filter by min_salience (could exclude results) |
| `query` | /api/v1/unified/query | POST | Advanced queries | Specification-based filtering on salience |
| `job_status` | /admin/jobs/status | GET | Status of background jobs | Shows if salience_refresh is running |
| `trigger` | /admin/jobs/trigger/{job_id} | POST | Manually trigger jobs | Can trigger salience decay/eviction |
| `cache_stats` | /admin/cache/stats | GET | Cache statistics | None |
| `health` | /health | GET | Health check | None |
| `mcp_discovery` | /.well-known/mcp | GET | MCP metadata | None |

---

## 7. File Location Reference

### Core MCP Files
- **Main Setup**: `/home/user/found-family/src/memory_palace/main.py` (lines 17, 182-183)
- **OAuth/Discovery**: `/home/user/found-family/src/memory_palace/api/endpoints/oauth.py`
- **Test Diagnostic**: `/home/user/found-family/tests/test_mcp.py`

### Tool Definition Files
- **Memory Tools**: `/home/user/found-family/src/memory_palace/api/endpoints/memory.py`
- **Query Tool**: `/home/user/found-family/src/memory_palace/api/endpoints/unified_query.py`
- **Admin Tools**: `/home/user/found-family/src/memory_palace/api/endpoints/admin.py`

### Service Implementation
- **Memory Service**: `/home/user/found-family/src/memory_palace/services/memory_service.py`
- **Dream Jobs**: `/home/user/found-family/src/memory_palace/services/dream_jobs.py`
- **Database Queries**: `/home/user/found-family/src/memory_palace/infrastructure/neo4j/queries.py`

### Configuration
- **Models**: `/home/user/found-family/src/memory_palace/domain/models/memories.py`
- **Constants**: `/home/user/found-family/src/memory_palace/core/constants.py`

---

## 8. Recommendations for Fixing Salience Issues

### High Priority
1. **Unify Default Values**: Change model default from 0.5 to 0.3 everywhere
2. **Document Salience Behavior**: Add clear comments about auto-modification and decay
3. **Add Salience Preservation**: Allow users to mark memories as "preserve" (no decay)
4. **Configuration Support**: Make decay factor, eviction threshold, and refresh interval configurable via environment variables

### Medium Priority
5. **Audit Trail**: Log all salience changes and evictions
6. **Deprecation Warning**: Consider removing auto-boost via relationships
7. **Better Type Safety**: Strengthen salience parameter validation
8. **Safety Thresholds**: Add configurable minimum salience for important memories

### Low Priority
9. **Testing**: Add comprehensive unit tests for salience behavior
10. **Documentation**: Update tool documentation with salience semantics
11. **Preview System**: Add API to preview what would be evicted
12. **Recovery Option**: Add ability to un-evict recent memories

---

## 9. Conclusion

The MCP implementation is well-structured and properly integrated using FastApiMCP. However, the salience parameter has significant issues around:

1. **Inconsistent defaults** across model, endpoint, and constants
2. **Silent auto-modification** of user-provided values
3. **Automatic memory eviction** without user awareness
4. **No configurability** for different use cases

These issues could lead to unexpected data loss and confusion about memory importance semantics. A coordinated fix addressing the salience system is recommended before further MCP feature development.

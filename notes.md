
## 🚨 DUPLICATION & SCATTER MAP



### 3. **Clustering Service Lifecycle** (4 different patterns)

#### Initialization scattered:
1. `main.py` lines 93-96: Creates and loads model
2. `memory_service.py` line 73: Creates new instance (!)
3. `dependencies.py` line 42: Injects global instance
4. `dream_jobs.py` line 19: Expects it passed in

### 4. **Vector Index Dimension Management** (3 locations)

#### Dimension logic duplicated:
1. `src/memory_palace/infrastructure/embeddings/voyage.py` lines 258-270: `get_model_dimensions()`
2. `src/memory_palace/infrastructure/neo4j/driver.py` lines 109-153: `ensure_vector_index()`
3. `src/memory_palace/main.py` lines 88-91: Dimension checking

### 5. **Memory Type Conversions** (5 patterns)

#### Neo4j ↔ Python conversions scattered:
1. `src/memory_palace/domain/models/base.py` lines 46-93: `to_neo4j_properties()` and `from_neo4j_record()`
2. `src/memory_palace/infrastructure/repositories/memory.py` lines 285-293: `_record_to_memory()`
3. `src/memory_palace/services/memory_service.py` lines 553-570: Manual record → Memory
4. `src/memory_palace/api/endpoints/memory.py` lines 114-129: Manual Memory → dict
5. `src/memory_palace/services/memory_service.py` lines 892-899: Another manual conversion

### 6. **Similarity Search Logic** (4 implementations)

#### Same vector search, different code:
1. `memory_service.py` line 346: Manual Cypher for similarity
2. `memory_service.py` line 553: Different manual Cypher
3. `memory.py` lines 62-74: Yet another similarity query
4. `query_builder/helpers.py` lines 66-81: `with_similarity()` helper (unused!)


### 8. **Relationship Creation** (3 different methods)

1. `memory_service.py` line 209-214: Direct repository call
2. `memory_service.py` line 576-585: Different pattern
3. `memory.py` lines 169-188: Generic `connect()` method

## 📋 CONSOLIDATION PLAN (Priority Order)

### **TASK 1: Centralize Query Building** ⭐⭐⭐⭐⭐
**Files to modify:**
1. Create `src/memory_palace/infrastructure/neo4j/queries.py`
2. Move ALL queries here using QueryBuilder
3. Delete raw queries from: `memory_service.py`, `dream_jobs.py`, `memory.py`

### **TASK 2: Fix Service Initialization** ⭐⭐⭐⭐
**Files to modify:**
1. Create `src/memory_palace/core/services.py` for singleton management
2. Update `main.py` to use it
3. Update `dependencies.py` to use it
4. Remove duplicate initialization from `memory_service.py`

### **TASK 3: Unify Vector Operations** ⭐⭐⭐⭐
**Files to modify:**
1. Create `src/memory_palace/infrastructure/neo4j/vector_ops.py`
2. Consolidate all similarity search
3. Move dimension management here
4. Update all callers

### **TASK 4: Standardize Type Conversions** ⭐⭐⭐
**Files to modify:**
1. Create `src/memory_palace/domain/converters.py`
2. Move all Neo4j ↔ Python conversions
3. Delete scattered conversion code

### **TASK 5: Pick ONE Relationship Pattern** ⭐⭐⭐
**Decision needed:**
- Keep the generic `connect()` in repository?
- Or domain-specific methods in service?

### **TASK 6: Consistent Error Handling** ⭐⭐
**Files to modify:**
1. Pick decorator vs manual pattern
2. Apply consistently everywhere

## 🎯 START HERE (Bite-sized first task)

**Create `src/memory_palace/infrastructure/neo4j/queries.py`:**
```python
"""Centralized query definitions using QueryBuilder.
This is the SINGLE SOURCE OF TRUTH for all Cypher queries.
"""

from memory_palace.infrastructure.neo4j.query_builder import CypherQueryBuilder

class MemoryQueries:
    """All memory-related queries in one place."""

    @staticmethod
    def similarity_search(limit: int = 10) -> tuple[str, dict]:
        """Standard similarity search query."""
        return (CypherQueryBuilder()
            .call_vector_index('memory_embeddings', limit)
            .where_param("score > {}", "threshold")
            .return_clause("node as m", "score")
            .order_by("score DESC")
            .build())

    # Add more queries here...
```

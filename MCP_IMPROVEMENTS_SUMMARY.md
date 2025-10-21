# MCP Improvements Summary

## Overview

This document summarizes the comprehensive improvements made to the Memory Palace MCP (Model Context Protocol) implementation to address critical issues with the salience parameter and make MCP tools more intuitive.

## Problems Addressed

### 1. Inconsistent Salience Defaults
**Before:**
- Model default: 0.5
- API endpoint default: 0.3
- Constants: 0.3

**After:**
- Unified default: 0.3 across all layers
- Clear documentation of what "normal" importance means

**Files Changed:**
- `src/memory_palace/domain/models/memories.py`: Lines 16, 31

### 2. Silent Automatic Salience Boost
**Before:**
- `_update_salience_from_relationships()` silently increased salience by 0.1 per relationship
- No user notification or control
- User-provided values were overridden

**After:**
- Removed automatic salience boost entirely
- Salience is now explicitly controlled by the user
- Relationships no longer modify importance ratings

**Files Changed:**
- `src/memory_palace/services/memory_service.py`: Lines 210-217, removed method

### 3. Automatic Memory Eviction Without Warning
**Before:**
- Memories below 0.05 salience automatically deleted every 5 minutes
- No warning, notification, or recovery option
- 45-day half-life meant even important old memories decayed

**After:**
- Added `preserve` flag to prevent decay/eviction
- Audit trail logs evicted memories with sample data
- Configurable decay factor, eviction threshold, and refresh interval
- Option to disable decay entirely via `SALIENCE_DECAY_ENABLED=false`

**Files Changed:**
- `src/memory_palace/domain/models/memories.py`: Lines 17, 32
- `src/memory_palace/api/endpoints/memory.py`: Lines 37-41
- `src/memory_palace/infrastructure/neo4j/queries.py`: Lines 283-312
- `src/memory_palace/services/dream_jobs.py`: Lines 93-126

### 4. Lack of Configuration Control
**Before:**
- Decay factor hard-coded (0.0154 for 45-day half-life)
- Eviction threshold hard-coded (0.05)
- Refresh interval hard-coded (5 minutes)

**After:**
- All settings configurable via environment variables:
  - `SALIENCE_DECAY_ENABLED`: Enable/disable decay (default: true)
  - `SALIENCE_DECAY_FACTOR`: Decay rate (default: 0.0154)
  - `SALIENCE_EVICTION_THRESHOLD`: Deletion threshold (default: 0.05)
  - `SALIENCE_REFRESH_INTERVAL_MINUTES`: How often to run (default: 5)

**Files Changed:**
- `src/memory_palace/core/config.py`: Lines 42-55
- `src/memory_palace/services/dream_jobs.py`: Lines 29-65

### 5. Unclear and Confusing MCP Tool Semantics
**Before:**
- Complex salience scale description (9 lines of comments)
- Unclear what happens to memories over time
- No mention of preservation options

**After:**
- Simplified, intuitive descriptions
- Clear guidance: "Leave unset for default (0.3)"
- Prominent preserve flag with clear benefits
- Improved endpoint docstrings explaining behavior

**Files Changed:**
- `src/memory_palace/api/endpoints/memory.py`: Lines 30-41, 126-133, 163-167, 204-208

## New Features

### Preserve Flag
```python
# Example usage
await memory_service.remember_message(
    content="Critical information",
    role="user",
    salience=1.0,
    preserve=True  # This memory will NEVER decay or be deleted
)
```

### Configurable Decay Settings
```bash
# .env file
SALIENCE_DECAY_ENABLED=true
SALIENCE_DECAY_FACTOR=0.0154  # 45-day half-life
SALIENCE_EVICTION_THRESHOLD=0.05
SALIENCE_REFRESH_INTERVAL_MINUTES=5

# To disable automatic decay entirely:
SALIENCE_DECAY_ENABLED=false
```

### Audit Trail for Evictions
All evicted memories are now logged with:
- Count of evicted memories
- Eviction threshold used
- Sample of evicted memories (first 5) with:
  - Memory ID
  - Content snippet (first 100 chars)
  - Final salience value

## Technical Changes

### Query Updates
Both `refresh_salience()` and `evict_low_salience()` now:
1. Check preserve flag: `WHERE (m.preserve = false OR m.preserve IS NULL)`
2. Accept parameterized threshold: `$eviction_threshold`
3. Return audit trail data for logging

### API Changes
All MCP tools now expose:
- `preserve` parameter (boolean, default: false)
- Improved descriptions
- Clearer salience guidance

### Service Layer Changes
- Removed automatic salience boost logic
- Pass preserve flag through to models
- Use configurable settings instead of constants

## Migration Guide

### For Existing Memories
Existing memories without the `preserve` field will be treated as `preserve=NULL`, which is equivalent to `preserve=false`. They will continue to decay as before.

### For Users
1. **To keep critical memories forever:** Set `preserve=true` when storing
2. **To adjust decay rate:** Set `SALIENCE_DECAY_FACTOR` in .env
3. **To disable decay entirely:** Set `SALIENCE_DECAY_ENABLED=false` in .env
4. **To review evictions:** Check logs at WARNING level for eviction audit trail

## Testing Recommendations

1. **Test preserve flag:**
   ```python
   # Store a preserved memory
   POST /api/v1/memory/remember
   {
     "content": "Never forget this",
     "role": "user",
     "preserve": true
   }

   # Verify it doesn't decay after refresh_salience runs
   ```

2. **Test configurable settings:**
   ```bash
   # Set custom settings
   export SALIENCE_EVICTION_THRESHOLD=0.1
   export SALIENCE_DECAY_ENABLED=false

   # Verify decay job respects settings
   POST /api/v1/admin/jobs/trigger/salience_refresh
   ```

3. **Test audit trail:**
   ```python
   # Create low-salience memory
   POST /api/v1/memory/remember
   {
     "content": "Test memory",
     "role": "user",
     "salience": 0.04
   }

   # Trigger eviction
   POST /api/v1/admin/jobs/trigger/salience_refresh

   # Check logs for eviction audit trail
   ```

## Files Modified

1. `src/memory_palace/domain/models/memories.py`: Unified defaults, added preserve field
2. `src/memory_palace/api/endpoints/memory.py`: Added preserve param, improved docs
3. `src/memory_palace/services/memory_service.py`: Removed auto-boost, added preserve support
4. `src/memory_palace/infrastructure/neo4j/queries.py`: Updated queries for preserve flag
5. `src/memory_palace/services/dream_jobs.py`: Configurable settings, audit trail
6. `src/memory_palace/core/config.py`: Added decay/eviction configuration

## Benefits

1. **Predictable Behavior:** No more silent modifications to user-provided values
2. **Data Safety:** Preserve flag prevents accidental deletion of critical memories
3. **Configurability:** Adapt decay/eviction behavior to different use cases
4. **Transparency:** Audit trail for all evictions
5. **Intuitive Tools:** Simplified MCP tool descriptions reduce confusion
6. **Backwards Compatible:** Existing memories continue to work as before

## Future Enhancements

Potential improvements for future iterations:
1. **Preview API:** Endpoint to preview what would be evicted
2. **Recovery System:** Ability to un-evict recently deleted memories
3. **Batch Preservation:** Endpoint to mark multiple memories as preserved
4. **Salience Boost API:** Optional explicit API to boost salience based on usage
5. **Decay Visualization:** Dashboard showing salience decay over time

---

**Generated:** 2025-10-21
**Author:** Claude Code MCP Enhancement Session

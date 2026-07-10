"""Public response contracts remain concrete in generated OpenAPI schemas."""

from memory_palace.api.endpoints.admin import CacheStatsResponse, JobStatusResponse
from memory_palace.api.endpoints.memory import AwakenResponse, SearchResponse


def test_memory_responses_have_typed_items_and_stats() -> None:
    search_schema = SearchResponse.model_json_schema()
    awaken_schema = AwakenResponse.model_json_schema()

    assert "$ref" in search_schema["properties"]["messages"]["items"]
    assert "$ref" in awaken_schema["properties"]["identity"]["items"]
    assert "$ref" in awaken_schema["properties"]["stats"]


def test_admin_responses_do_not_emit_unbounded_objects() -> None:
    job_schema = JobStatusResponse.model_json_schema()
    cache_schema = CacheStatsResponse.model_json_schema()

    assert "$ref" in job_schema["properties"]["jobs"]["items"]
    assert set(cache_schema["required"]) == {"size", "total_hits"}

"""Tests for the clustering service's in-process state boundary."""

from memory_palace.services.clustering.dbscan_service import DBSCANClusteringService


def test_clustering_service_has_no_executable_state_persistence_surface() -> None:
    assert not hasattr(DBSCANClusteringService, "save_model")
    assert not hasattr(DBSCANClusteringService, "load_model")


async def test_fitted_state_supports_prediction_in_process() -> None:
    service = DBSCANClusteringService(eps=0.2, min_samples=2)
    await service.fit([[1.0, 0.0], [0.99, 0.01]])

    assert await service.predict([[1.0, 0.0]]) == [0]
    assert await service.predict([[100.0, 0.0]]) == [0]
    assert await service.predict([[-1.0, 0.0]]) == [-1]


async def test_unfitted_service_does_not_train_on_a_single_request() -> None:
    service = DBSCANClusteringService(eps=0.2, min_samples=3)

    assert await service.predict([[1.0, 0.0]]) == [-1]
    assert service.fitted_embeddings is None


async def test_zero_vectors_fail_before_sklearn_cosine_math() -> None:
    service = DBSCANClusteringService()

    try:
        await service.predict([[0.0, 0.0]])
    except ValueError as exc:
        assert "zero vectors" in str(exc)
    else:
        raise AssertionError("zero-vector input should fail")


async def test_too_small_refit_discards_stale_snapshot() -> None:
    service = DBSCANClusteringService(eps=0.2, min_samples=2)
    await service.fit([[1.0, 0.0], [0.99, 0.01]])

    await service.fit([[1.0, 0.0]])

    assert service.fitted_embeddings is None
    assert await service.predict([[1.0, 0.0]]) == [-1]

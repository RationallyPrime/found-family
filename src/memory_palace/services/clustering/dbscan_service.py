from __future__ import annotations

import asyncio

import numpy as np
from numpy.typing import NDArray
from sklearn.cluster import DBSCAN


class DBSCANClusteringService:
    def __init__(self, eps: float = 0.3, min_samples: int = 3) -> None:
        if not 0.0 < eps <= 2.0:
            raise ValueError("eps must be in the cosine-distance interval (0, 2]")
        if min_samples < 1:
            raise ValueError("min_samples must be at least one")
        self.eps = eps
        self.min_samples = min_samples
        self.clusterer: DBSCAN | None = None
        self.fitted_embeddings: NDArray[np.float64] | None = None
        self._fitted_labels: NDArray[np.int64] | None = None
        self._snapshot_lock = asyncio.Lock()

    async def fit(self, embeddings: list[list[float]]) -> None:
        """Fit the clustering model on all embeddings."""
        matrix = self._validated_matrix(embeddings)
        if len(matrix) < self.min_samples:
            await self.reset()
            return

        # sklearn is synchronous. Keeping this call in-process avoids unsafe
        # persisted executables; callers schedule full fits as bounded jobs.
        clusterer, core_embeddings, core_labels = self._fit_snapshot(matrix)
        async with self._snapshot_lock:
            self.clusterer = clusterer
            self.fitted_embeddings = core_embeddings
            self._fitted_labels = core_labels

    async def reset(self) -> None:
        """Discard a stale clustering snapshot explicitly."""
        async with self._snapshot_lock:
            self.clusterer = None
            self.fitted_embeddings = None
            self._fitted_labels = None

    async def predict(self, embeddings: list[list[float]]) -> list[int]:
        """Predict cluster labels for new embeddings."""
        matrix = self._validated_matrix(embeddings)
        async with self._snapshot_lock:
            fitted_embeddings = self.fitted_embeddings
            fitted_labels = self._fitted_labels

        if fitted_embeddings is None or fitted_labels is None:
            return [-1] * len(matrix)
        if matrix.shape[1] != fitted_embeddings.shape[1]:
            raise ValueError("Embedding dimensions do not match the fitted clustering snapshot")
        return self._predict_snapshot(matrix, fitted_embeddings, fitted_labels)

    def _fit_snapshot(self, matrix: NDArray[np.float64]) -> tuple[DBSCAN, NDArray[np.float64], NDArray[np.int64]]:
        clusterer = DBSCAN(eps=self.eps, min_samples=self.min_samples, metric="cosine")
        clusterer.fit(matrix)
        labels = np.asarray(clusterer.labels_, dtype=np.int64)
        core_indices = np.asarray(clusterer.core_sample_indices_, dtype=np.int64)
        return clusterer, matrix[core_indices], labels[core_indices]

    def _predict_snapshot(
        self,
        matrix: NDArray[np.float64],
        fitted_embeddings: NDArray[np.float64],
        fitted_labels: NDArray[np.int64],
    ) -> list[int]:
        if len(fitted_embeddings) == 0:
            return [-1] * len(matrix)

        normalized = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)
        normalized_fitted = fitted_embeddings / np.linalg.norm(fitted_embeddings, axis=1, keepdims=True)
        cosine_distances = 1.0 - np.clip(normalized @ normalized_fitted.T, -1.0, 1.0)
        nearest_indices = np.argmin(cosine_distances, axis=1)
        nearest_distances = cosine_distances[np.arange(len(matrix)), nearest_indices]
        return [
            int(fitted_labels[index]) if distance <= self.eps else -1
            for index, distance in zip(nearest_indices, nearest_distances, strict=True)
        ]

    @staticmethod
    def _validated_matrix(embeddings: list[list[float]]) -> NDArray[np.float64]:
        matrix = np.asarray(embeddings, dtype=np.float64)
        if matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[1] == 0:
            raise ValueError("Embeddings must be a non-empty rectangular matrix")
        if not np.isfinite(matrix).all():
            raise ValueError("Embeddings must contain only finite values")
        if np.any(np.linalg.norm(matrix, axis=1) == 0.0):
            raise ValueError("Embeddings must not contain zero vectors")
        return matrix

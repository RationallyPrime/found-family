import base64
import pickle

import numpy as np
from sklearn.cluster import DBSCAN


class DBSCANClusteringService:
    def __init__(self, eps: float = 0.3, min_samples: int = 3):
        self.eps = eps
        self.min_samples = min_samples
        self.clusterer: DBSCAN | None = None
        self.fitted_embeddings: np.ndarray | None = None

    async def fit(self, embeddings: list[list[float]]) -> None:
        """Fit the clustering model on all embeddings."""
        X = np.array(embeddings)
        self.fitted_embeddings = X

        self.clusterer = DBSCAN(
            eps=self.eps,
            min_samples=self.min_samples,
            metric="cosine",
        )
        self.clusterer.fit(X)

    async def predict(self, embeddings: list[list[float]]) -> list[int]:
        """Predict cluster labels for new embeddings."""
        if self.clusterer is None or self.fitted_embeddings is None:
            # If not fitted, fit on these embeddings
            await self.fit(embeddings)
            return self.clusterer.labels_.tolist()

        # For new points, find nearest cluster
        X = np.array(embeddings)
        labels: list[int] = []

        for embedding in X:
            # Find nearest neighbor in fitted data
            distances = np.dot(self.fitted_embeddings, embedding)
            nearest_idx = np.argmax(distances)
            nearest_label = self.clusterer.labels_[nearest_idx]
            labels.append(int(nearest_label))

        return labels

    async def save_model(self, session) -> None:
        """Persist model to Neo4j."""
        if self.clusterer:
            model_bytes = pickle.dumps(self.clusterer)
            model_b64 = base64.b64encode(model_bytes).decode("utf-8")
            await session.run(
                """
                MERGE (m:ClusterModel {name: 'default'})
                SET m.data = $model,
                    m.updated = datetime()
                """,
                model=model_b64,
            )

    async def load_model(self, session) -> bool:
        """Load model from Neo4j."""
        result = await session.run(
            """
            MATCH (m:ClusterModel {name: 'default'})
            RETURN m.data as model
            """
        )
        record = await result.single()
        if record:
            model_b64 = record["model"]
            model_bytes = base64.b64decode(model_b64)
            self.clusterer = pickle.loads(model_bytes)
            return True
        return False

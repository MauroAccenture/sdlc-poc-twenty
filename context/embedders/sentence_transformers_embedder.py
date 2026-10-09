"""
Sentence Transformers embedder — free, runs locally, no API cost.

Uses the `sentence-transformers` library with a pre-trained model.
Model is downloaded once and cached locally (~90MB for MiniLM).

Default model: all-MiniLM-L6-v2
  - 384 dimensions
  - Fast inference (~14k sentences/sec on CPU)
  - Strong performance on code and prose similarity
  - Fully open source (Apache 2.0)

Alternative models (set in AGENTS.md):
  - all-mpnet-base-v2     : 768 dims, higher quality, slower
  - all-MiniLM-L12-v2    : 384 dims, slightly better than L6
  - microsoft/codebert-base: optimised for code (768 dims)

Configuration (AGENTS.md)
─────────────────────────
context:
  embedder:
    provider: sentence_transformers
    model: all-MiniLM-L6-v2     # default
    batch_size: 64               # default
    cache_dir: .cache/embeddings # default
"""

import os

from context.embedders.base import Embedder

DEFAULT_MODEL = "all-MiniLM-L6-v2"
DEFAULT_BATCH = 64


class SentenceTransformersEmbedder(Embedder):
    """
    Local embedder using sentence-transformers.
    No API key required. No data leaves your machine.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        batch_size: int = DEFAULT_BATCH,
        cache_dir: str = ".cache/embeddings",
    ) -> None:
        self.model_name = model
        self.batch_size = batch_size
        self.cache_dir  = cache_dir
        self._model = None   # lazy load

    def _get_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise ImportError(
                    "sentence-transformers is not installed. "
                    "Run: pip install sentence-transformers"
                )
            os.makedirs(self.cache_dir, exist_ok=True)
            print(f"  Loading embedding model '{self.model_name}'...")
            self._model = SentenceTransformer(
                self.model_name,
                cache_folder=self.cache_dir,
            )
        return self._model

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        model = self._get_model()
        embeddings = model.encode(
            texts,
            batch_size=self.batch_size,
            show_progress_bar=len(texts) > 100,
            convert_to_numpy=True,
        )
        return embeddings.tolist()

    def get_dimension(self) -> int:
        return self._get_model().get_sentence_embedding_dimension()

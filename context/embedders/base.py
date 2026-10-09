"""
Embedder — abstract base class for all embedding providers.

An embedder converts text into a dense vector representation used for
semantic similarity search. The choice of embedder is independent of the
choice of vector store — any embedder works with any store.

Implementing a new embedder
────────────────────────────
1. Subclass Embedder.
2. Implement embed_batch() and get_dimension().
3. Register in context/embedders/__init__.py.
4. Set embedder.provider in AGENTS.md.

Important: the dimension returned by get_dimension() must match the
dimension of the vectors stored in the vector store. If you switch
embedders on an existing index, you must rebuild the index from scratch.
"""

from abc import ABC, abstractmethod


class Embedder(ABC):

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a batch of texts into dense vectors.

        Parameters
        ----------
        texts : list of raw text strings to embed

        Returns
        -------
        List of embedding vectors, one per input text.
        Each vector has length == get_dimension().

        Implementations should handle batching internally for efficiency.
        """

    @abstractmethod
    def get_dimension(self) -> int:
        """
        Return the dimensionality of the embedding vectors.

        Must be consistent across calls. Used by vector stores to
        initialise their collections with the correct vector size.
        """

    def embed_one(self, text: str) -> list[float]:
        """Embed a single text. Convenience wrapper around embed_batch."""
        return self.embed_batch([text])[0]

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(dim={self.get_dimension()})"

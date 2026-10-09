from context.stores.base import VectorStore
from context.stores.chroma_store import ChromaStore
from context.stores.azure_search_store import AzureSearchStore
from context.stores.qdrant_store import QdrantStore

STORE_REGISTRY: dict[str, type[VectorStore]] = {
    "chroma":       ChromaStore,
    "azure_search": AzureSearchStore,
    "qdrant":       QdrantStore,
}


def store_from_config(cfg: dict) -> VectorStore:
    provider = cfg.get("provider", "chroma")
    cls = STORE_REGISTRY.get(provider)
    if cls is None:
        known = ", ".join(STORE_REGISTRY.keys())
        raise ValueError(f"Unknown store provider '{provider}'. Known: {known}")
    cfg_without_provider = {k: v for k, v in cfg.items() if k != "provider"}
    return cls(**cfg_without_provider)


__all__ = ["VectorStore", "ChromaStore", "AzureSearchStore", "QdrantStore", "store_from_config"]

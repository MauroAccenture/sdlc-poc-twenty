from context.embedders.base import Embedder
from context.embedders.sentence_transformers_embedder import SentenceTransformersEmbedder
from context.embedders.azure_openai_embedder import AzureOpenAIEmbedder

EMBEDDER_REGISTRY: dict[str, type[Embedder]] = {
    "sentence_transformers": SentenceTransformersEmbedder,
    "azure_openai":          AzureOpenAIEmbedder,
}


def embedder_from_config(cfg: dict) -> Embedder:
    provider = cfg.get("provider", "sentence_transformers")
    cls = EMBEDDER_REGISTRY.get(provider)
    if cls is None:
        known = ", ".join(EMBEDDER_REGISTRY.keys())
        raise ValueError(f"Unknown embedder provider '{provider}'. Known: {known}")
    cfg_without_provider = {k: v for k, v in cfg.items() if k != "provider"}
    return cls(**cfg_without_provider)


__all__ = ["Embedder", "SentenceTransformersEmbedder", "AzureOpenAIEmbedder", "embedder_from_config"]

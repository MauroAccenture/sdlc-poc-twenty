"""
Azure OpenAI embedder — STUB.

Uses the Azure OpenAI Service to generate embeddings.
Requires an Azure OpenAI deployment of text-embedding-ada-002 or
text-embedding-3-small/large.

Configuration (claude.md)
─────────────────────────
context:
  embedder:
    provider: azure_openai
    deployment: text-embedding-ada-002
    api_version: "2024-02-01"

Required secrets
────────────────
AZURE_OPENAI_ENDPOINT  — e.g. https://myorg.openai.azure.com
AZURE_OPENAI_API_KEY   — API key from Azure portal

Implementation guide
────────────────────
pip install openai>=1.0.0

from openai import AzureOpenAI

client = AzureOpenAI(
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
    api_version=self.api_version,
)

response = client.embeddings.create(
    input=texts,
    model=self.deployment,   # deployment name, not model name
)
return [item.embedding for item in response.data]

Dimension mapping:
  text-embedding-ada-002  → 1536 dims
  text-embedding-3-small  → 1536 dims (default), configurable
  text-embedding-3-large  → 3072 dims (default), configurable

Note: switching from sentence-transformers (384 dims) to Azure OpenAI
(1536 dims) requires a full index rebuild. Update the dimension in
your Chroma/Azure Search collection configuration first.
"""

import os
from context.embedders.base import Embedder


class AzureOpenAIEmbedder(Embedder):
    """Azure OpenAI embedding service. NOT YET IMPLEMENTED."""

    def __init__(
        self,
        deployment: str = "text-embedding-ada-002",
        api_version: str = "2024-02-01",
        endpoint: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.deployment  = deployment
        self.api_version = api_version
        self.endpoint    = endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        self.api_key     = api_key  or os.environ.get("AZURE_OPENAI_API_KEY", "")

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError(
            "AzureOpenAIEmbedder is not yet implemented. "
            "See module docstring for implementation guide."
        )

    def get_dimension(self) -> int:
        return 1536   # ada-002 and 3-small default

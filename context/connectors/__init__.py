from context.connectors.base import SourceConnector
from context.connectors.local_connector import LocalConnector
from context.connectors.github_connector import GitHubConnector
from context.connectors.sharepoint_connector import SharePointConnector

CONNECTOR_REGISTRY: dict[str, type[SourceConnector]] = {
    "local":      LocalConnector,
    "github":     GitHubConnector,
    "sharepoint": SharePointConnector,
}


def connector_from_config(cfg: dict) -> SourceConnector:
    conn_type = cfg.get("type")
    cls = CONNECTOR_REGISTRY.get(conn_type)
    if cls is None:
        known = ", ".join(CONNECTOR_REGISTRY.keys())
        raise ValueError(f"Unknown connector type '{conn_type}'. Known: {known}")
    cfg_without_type = {k: v for k, v in cfg.items() if k != "type"}
    return cls(**cfg_without_type)


__all__ = [
    "SourceConnector", "LocalConnector", "GitHubConnector", "SharePointConnector",
    "connector_from_config",
]

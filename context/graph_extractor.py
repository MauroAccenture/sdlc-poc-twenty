"""
Graph extractor — lightweight entity and relationship extraction.

Extracts entities (classes, functions, modules) and relationships
(imports, calls, inherits) from source files and stores them as
structured metadata on Documents. This enables graph-aware retrieval:
"find all files that import auth_middleware" rather than just
"find files similar to auth_middleware".

This is a lightweight custom implementation — not Microsoft GraphRAG.
It uses regex-based extraction which works well enough for a PoC and
avoids the heavy dependencies of the full GraphRAG library.

The extracted graph is stored in two places:
1. As metadata on each Document in the vector store (for filtered search)
2. As memory/patterns/*.md files (human-readable, Obsidian-compatible)
"""

import re
from dataclasses import dataclass, field


@dataclass
class Entity:
    """A named code entity: class, function, module."""
    name:      str
    kind:      str          # "class", "function", "module", "constant"
    file_path: str
    line:      int = 0


@dataclass
class Relationship:
    """A directed relationship between two entities."""
    source:      str        # entity name or file path
    target:      str        # entity name or file path
    kind:        str        # "imports", "calls", "inherits", "defines"
    source_file: str = ""


@dataclass
class FileGraph:
    """Entity and relationship graph for a single file."""
    path:          str
    entities:      list[Entity]      = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    imports:       list[str]          = field(default_factory=list)  # module paths imported


def extract_graph(path: str, content: str, language: str) -> FileGraph:
    """
    Extract entities and relationships from a source file.
    Returns a FileGraph with all discovered entities and relationships.
    """
    graph = FileGraph(path=path)

    if language == "python":
        _extract_python(content, path, graph)
    elif language in {"javascript", "typescript"}:
        _extract_js(content, path, graph)
    else:
        _extract_generic(content, path, graph)

    return graph


def graph_to_metadata(graph: FileGraph) -> dict:
    """
    Flatten a FileGraph into Document metadata for storage in the vector store.
    Values are stored as comma-separated strings (vector stores need primitives).
    """
    entity_names  = [e.name for e in graph.entities]
    class_names   = [e.name for e in graph.entities if e.kind == "class"]
    func_names    = [e.name for e in graph.entities if e.kind == "function"]
    inherited     = [r.target for r in graph.relationships if r.kind == "inherits"]
    called_by     = [r.target for r in graph.relationships if r.kind == "calls"]

    return {
        "entities":   ",".join(entity_names[:20]),    # cap to avoid huge metadata
        "classes":    ",".join(class_names[:10]),
        "functions":  ",".join(func_names[:20]),
        "imports":    ",".join(graph.imports[:20]),
        "inherits":   ",".join(inherited[:10]),
        "calls":      ",".join(called_by[:20]),
    }


def graph_to_markdown(graph: FileGraph) -> str:
    """
    Render a FileGraph as a markdown note for the Obsidian memory vault.
    Uses [[wikilinks]] so Obsidian shows relationships in its graph view.
    """
    lines = [
        f"# {graph.path}",
        "",
        "## Entities",
    ]

    for entity in graph.entities:
        lines.append(f"- `{entity.kind}` **{entity.name}** (line {entity.line})")

    if graph.imports:
        lines += ["", "## Imports"]
        for imp in graph.imports:
            # Create wikilinks for local imports so Obsidian connects them
            if not imp.startswith(("os", "sys", "re", "json", "typing", "abc",
                                   "datetime", "pathlib", "subprocess", "collections")):
                lines.append(f"- [[{imp}]]")
            else:
                lines.append(f"- `{imp}` (stdlib)")

    inheritance = [r for r in graph.relationships if r.kind == "inherits"]
    if inheritance:
        lines += ["", "## Inherits from"]
        for rel in inheritance:
            lines.append(f"- [[{rel.target}]]")

    return "\n".join(lines)


# ── Language-specific extractors ─────────────────────────────────────────────

def _extract_python(content: str, path: str, graph: FileGraph) -> None:
    lines = content.splitlines()

    for i, line in enumerate(lines, 1):
        # Imports
        m = re.match(r"^(?:from\s+([\w.]+)\s+import|import\s+([\w.,\s]+))", line)
        if m:
            module = m.group(1) or m.group(2).split(",")[0].strip()
            graph.imports.append(module)
            graph.relationships.append(Relationship(
                source=path, target=module, kind="imports", source_file=path
            ))

        # Class definitions
        m = re.match(r"^class\s+(\w+)(?:\(([^)]*)\))?", line)
        if m:
            name    = m.group(1)
            parents = m.group(2) or ""
            graph.entities.append(Entity(name=name, kind="class", file_path=path, line=i))
            for parent in [p.strip() for p in parents.split(",") if p.strip()]:
                if parent not in {"object", "ABC", "Enum"}:
                    graph.relationships.append(Relationship(
                        source=name, target=parent, kind="inherits", source_file=path
                    ))

        # Function/method definitions
        m = re.match(r"^(?:    )?(?:async\s+)?def\s+(\w+)\s*\(", line)
        if m:
            name = m.group(1)
            if not name.startswith("_") or name.startswith("__"):
                graph.entities.append(Entity(name=name, kind="function", file_path=path, line=i))


def _extract_js(content: str, path: str, graph: FileGraph) -> None:
    lines = content.splitlines()

    for i, line in enumerate(lines, 1):
        # ES module imports
        m = re.match(r"""^import\s+.*?from\s+['"]([^'"]+)['"]""", line)
        if m:
            module = m.group(1)
            graph.imports.append(module)
            graph.relationships.append(Relationship(
                source=path, target=module, kind="imports", source_file=path
            ))

        # Class definitions
        m = re.match(r"^(?:export\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?", line)
        if m:
            name   = m.group(1)
            parent = m.group(2)
            graph.entities.append(Entity(name=name, kind="class", file_path=path, line=i))
            if parent:
                graph.relationships.append(Relationship(
                    source=name, target=parent, kind="inherits", source_file=path
                ))

        # Function definitions
        m = re.match(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", line)
        if m:
            graph.entities.append(Entity(name=m.group(1), kind="function", file_path=path, line=i))


def _extract_generic(content: str, path: str, graph: FileGraph) -> None:
    """Minimal extraction for unsupported languages."""
    for line in content.splitlines():
        m = re.search(r"import\s+['\"]([^'\"]+)['\"]", line)
        if m:
            graph.imports.append(m.group(1))

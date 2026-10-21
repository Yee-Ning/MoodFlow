"""Exact boundary templates verified against one actual provider input."""

from dataclasses import dataclass
import json

from .narrative_normalization import canonical_json, payload_sha256


@dataclass(frozen=True)
class BoundaryContext:
    # JSON is immutable and preserves absent versus null versus empty fields.
    snapshot_json: str
    input_sha256: str
    invocation_id: str = ""
    attempt: int = 0


def boundary_context_from_input(sent, *, invocation_id="", attempt=0):
    value = {key: sent[key] for key in ("worldline_graph", "catalog_facts") if key in sent}
    return BoundaryContext(canonical_json(value), payload_sha256(value), invocation_id, attempt)


def _terminal_normalize(text):
    text = text.strip()
    return text[:-1] if text.endswith(("。", ".", "．")) else text


def boundary_template_check(paragraph, context):
    """Return the matched rule and its complete assertion evidence, or None."""
    if not isinstance(context, BoundaryContext):
        return None
    sent = json.loads(context.snapshot_json)
    graph = sent.get("worldline_graph")
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    edges = graph.get("edges") if isinstance(graph, dict) else None
    node = nodes[0] if isinstance(nodes, list) and len(nodes) == 1 and isinstance(nodes[0], dict) else None
    root_name = node.get("name") if node else None
    assertions = {"node_count": isinstance(nodes, list) and len(nodes) == 1,
        "root_id": bool(node) and isinstance(graph.get("root"), str) and node.get("id") == graph["root"],
        "root_type": bool(node) and node.get("type") == "artist",
        "root_name": isinstance(root_name, str) and bool(root_name.strip())}
    templates = []
    if isinstance(root_name, str):
        templates = [
            ("A2-G1", f"从当前的世界线图来看，它只包含一个根节点“{root_name}”，没有任何边。",
             {**assertions, "edge_count": isinstance(edges, list) and len(edges) == 0}),
            ("A2-G2", f"当前世界线图谱里只有“{root_name}”这一个艺人根节点。",
             {**assertions, "edge_count": isinstance(edges, list) and len(edges) == 0}),
        ]
    facts = sent.get("catalog_facts")
    templates.append(("A2-F1", "本次输入中的目录事实为空。", {
        "facts_present": "catalog_facts" in sent, "facts_type": isinstance(facts, list),
        "facts_empty": isinstance(facts, list) and len(facts) == 0}))
    for template_id, template, checks in templates:
        if _terminal_normalize(paragraph) == _terminal_normalize(template):
            return {"template_id": template_id, "input_sha256": context.input_sha256,
                "assertions": checks, "actual_input": sent, "matched": all(checks.values())}
    return None


def is_boundary_paragraph(paragraph, context=None):
    check = boundary_template_check(paragraph, context)
    return check is not None and check["matched"]

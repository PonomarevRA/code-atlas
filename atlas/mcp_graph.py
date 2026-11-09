"""Совместимый снимок Graphify MCP из общей карты, отдельно от исходного raw JSON."""

from copy import deepcopy
import json
from .member_preview import member_preview, printed_member_preview


def build_mcp_graph(graph: dict) -> dict:
    """Сохраняет все факты/направления и добавляет компактную проекцию для Graphify tools.

    Graphify печатает поле attributes, а не произвольные свойства узла. Полные
    attributeFacts остаются на узле; проекция поясняет scope и ведёт к occurrence ID.
    MultiGraph не теряет несколько отношений между одной парой исходных узлов.
    """
    nodes = deepcopy(graph["nodes"])
    community_ids = sorted({str(node["community"]) for node in nodes if node.get("community") is not None})
    community_numbers = {identifier: index for index, identifier in enumerate(community_ids)}
    for node in nodes:
        # Atlas использует строковые ID групп. Graphify MCP требует int для
        # восстановления communities; исходный ID остаётся отдельным свойством.
        community = node.get("community")
        if community is not None:
            node["atlasCommunityId"] = community
            node["community"] = community_numbers[str(community)]
            node.setdefault("community_name", str(community))
        node.setdefault("source_file", node.get("file", ""))
        if node.get("line") is not None:
            node.setdefault("source_location", f"L{node['line']}")
        attributes = node.get("attributes")
        attributes = dict(attributes) if isinstance(attributes, dict) else {}
        members = member_preview(node)
        if members:
            # Print values first: upstream Graphify caps serialized attributes at 1000 chars.
            ordered = {key: members[key] for key in ('health', 'documentationHealth', 'complexity', 'dataFacts', 'memberFacts', 'fieldFacts', 'healthIdentity', 'analysisFacts') if key in members}
            attributes = {**{'atlas.' + key: value for key, value in ordered.items()}, **attributes}
        if node.get("comment"):
            # Graphify печатает только первые 1000 символов attributes. Здесь
            # короткая проекция; полный текст остаётся на узле общего снимка.
            explanation = {"atlas.comment": node["comment"][:300],
                           "atlas.commentScope": node.get("commentScope")}
            if node.get("implementationComment"):
                explanation["atlas.implementationComment"] = node["implementationComment"][:240]
            if node.get("implementationName"):
                explanation["atlas.implementationName"] = node["implementationName"][:120]
            explanation["atlas.commentsTruncated"] = bool(
                (node.get("commentSource") or {}).get("truncated")
                or len(node["comment"]) > 300 or len(node.get("implementationComment", "")) > 240
                or len(node.get("implementationName", "")) > 120)
            explanation["atlas.commentSourceKind"] = (node.get("commentSource") or {}).get("sourceKind")
            attributes = {**attributes, **explanation}
        field = node.get("fieldFacts")
        if isinstance(field, dict):
            attributes["atlas.field"] = {key: field.get(key) for key in ("name", "type", "nullable", "nullableEvidence", "sourceHash")}
            attributes["atlas.fieldDescription"] = field.get("description", "")[:240]
        if node.get("kind") in {"attribute", "attribute_scope"}:
            for key in ("name", "scope", "target", "ownerNodeId", "declarationNodeId", "sourceHash", "sourceKind"):
                if key in node:
                    attributes["atlas." + key] = node[key]
            # Graphify режет печатные attributes до 1000 символов. Выражения
            # ограничены здесь, полный текст остаётся в общей модели get_node.
            arguments = node.get("arguments", [])
            argument_text = json.dumps(arguments, ensure_ascii=False)
            attributes["atlas.arguments"] = argument_text[:240]
            attributes["atlas.argumentsTruncated"] = len(argument_text) > 240
        facts = node.get("attributeFacts")
        if isinstance(facts, dict):
            names = []
            for group in facts.get("groups", []):
                for attribute in group.get("attributes", []):
                    names.append({"scope": group.get("scope"), "name": attribute.get("name"),
                                  "nodeId": attribute.get("attributeNodeId")})
            attributes["atlas.lexicalAttributes"] = names[:40]
            status = facts.get("status", "unavailable")
            attributes["atlas.attributesStatus"] = status
            attributes["atlas.attributesTruncated"] = bool(facts.get("truncated") or status != "parsed" or len(names) > 40)
            attributes["atlas.sourceKind"] = facts.get("sourceKind", "unavailable")
            if facts.get("sourceHash"):
                attributes["atlas.sourceHash"] = facts["sourceHash"]
        attributes.update(printed_member_preview(node))
        if node.get('semanticFacts'):
            attributes['atlas.semantic'] = {'facts': len(node['semanticFacts']),
                'runtimeSelectionKnown': False,
                'detailsTool': 'get_node'}
        if attributes:
            node["attributes"] = attributes
    edges = []
    for index, source in enumerate(graph["edges"]):
        edge = deepcopy(source)
        edge["key"] = index
        edges.append(edge)
    return {"directed": True, "multigraph": True, "nodes": nodes, "links": edges,
            "graph": {"provider": "Code Atlas published graph", "metadata": deepcopy(graph.get("metadata", {})),
                      "atlasCommunityNumbers": community_numbers}}

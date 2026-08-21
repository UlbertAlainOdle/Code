"""Persistent, typed relationship layer for long-term memories.

The graph deliberately stores compact public metadata instead of memory bodies.
Prompt retrieval continues to read the authoritative memory records from
KnowledgeBase; this file is an index and relationship ledger, not a second copy.
"""

import hashlib
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path


class MemoryGraphStore:
    SCHEMA_VERSION = 3
    LEGACY_SCHEMA_VERSIONS = {2}
    MANUAL_RELATIONS = {
        "related_to", "supports", "caused_by", "part_of", "follows",
    }
    DIRECT_MEMORY_RELATIONS = MANUAL_RELATIONS | {"conflicts_with", "supersedes", "superseded_by"}
    AUTO_CONNECTORS = {"belongs_to_project", "from_session", "mentions", "tagged_with"}
    DEEP_CONNECTORS = AUTO_CONNECTORS | {"occurred_on"}
    TAG_STOPWORDS = {
        "a", "an", "and", "are", "for", "from", "in", "is", "of", "on", "or", "the", "to", "with",
        "detail", "details", "general", "memory", "note", "notes", "semantic", "episodic", "conversation",
        "一个", "一些", "以及", "关于", "内容", "对话", "记忆", "记录", "这个", "那个", "的", "了", "和", "与",
    }

    def __init__(self, base_dir, filename="memory_graph.json"):
        self.base_dir = Path(base_dir).resolve()
        self.path = self.base_dir / filename
        self._lock = threading.RLock()
        self._adjacency_index = {}
        self._edge_index = {}
        self._relation_index = {}
        self._status_cache = {}
        self.graph = self._empty_graph()
        self.load()

    def _empty_graph(self):
        return {
            "schema_version": self.SCHEMA_VERSION,
            "updated_at": None,
            "source_hash": "",
            "memory_signatures": {},
            "nodes": {},
            "edges": [],
        }

    def load(self):
        with self._lock:
            if not self.path.is_file():
                self.graph = self._empty_graph()
                self._rebuild_indexes()
                return self.graph
            try:
                data = json.loads(self.path.read_text(encoding="utf-8-sig"))
                schema_version = data.get("schema_version")
                if schema_version != self.SCHEMA_VERSION and schema_version not in self.LEGACY_SCHEMA_VERSIONS:
                    raise ValueError("Unsupported memory graph schema")
                if not isinstance(data.get("nodes"), dict) or not isinstance(data.get("edges"), list):
                    raise ValueError("Invalid memory graph structure")
                # Version 2 graphs are upgraded in place. Their missing
                # signatures deliberately trigger one complete synchronization
                # before later updates can use the incremental path.
                data["schema_version"] = self.SCHEMA_VERSION
                if not isinstance(data.get("memory_signatures"), dict):
                    data["memory_signatures"] = {}
                self.graph = data
            except Exception:
                self.graph = self._empty_graph()
            self._rebuild_indexes()
            return self.graph

    def _save(self):
        self.base_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(self.graph, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, self.path)

    def _rebuild_indexes(self):
        """Build query indexes once after load or mutation, never per recall."""
        adjacency = {}
        edge_index = {}
        relation_index = {}
        for edge in self.graph.get("edges", []):
            edge_id = str(edge.get("id") or "")
            source = edge.get("source")
            target = edge.get("target")
            if not edge_id or not source or not target:
                continue
            edge_index[edge_id] = edge
            adjacency.setdefault(source, []).append((target, edge))
            adjacency.setdefault(target, []).append((source, edge))
            relation_index.setdefault(str(edge.get("relation") or "unknown"), set()).add(edge_id)
        self._adjacency_index = adjacency
        self._edge_index = edge_index
        self._relation_index = relation_index
        node_types = {}
        for node in self.graph.get("nodes", {}).values():
            node_type = str(node.get("type") or "unknown")
            node_types[node_type] = node_types.get(node_type, 0) + 1
        self._status_cache = {
            "schema_version": self.graph.get("schema_version", self.SCHEMA_VERSION),
            "updated_at": self.graph.get("updated_at"),
            "node_count": len(self.graph.get("nodes", {})),
            "edge_count": len(edge_index),
            "node_types": node_types,
            "relation_types": {
                relation: len(edge_ids)
                for relation, edge_ids in relation_index.items()
            },
            "indexed_adjacency_nodes": len(adjacency),
        }

    @staticmethod
    def _safe_list(value):
        if isinstance(value, list):
            return value
        if value in (None, ""):
            return []
        return [value]

    @staticmethod
    def _compact_key(value):
        normalized = re.sub(r"\s+", " ", str(value or "").strip().lower())
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _memory_node_id(memory_id):
        return f"memory:{memory_id}"

    def _edge(self, source, target, relation, confidence, source_kind="derived", directed=True):
        if not directed and source > target:
            source, target = target, source
        edge_key = f"{source}|{relation}|{target}|{source_kind}"
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.5
        return {
            "id": hashlib.sha1(edge_key.encode("utf-8")).hexdigest()[:20],
            "source": source,
            "target": target,
            "relation": relation,
            "confidence": round(max(0.0, min(1.0, confidence)), 4),
            "source_kind": source_kind,
            "directed": bool(directed),
        }

    def _memory_signature(self, memory):
        keys = (
            "id", "title", "date", "memory_layer", "is_archived", "project_id",
            "project_name", "category", "tags", "entities", "supersedes", "conflicts_with",
            "superseded_by", "source_detail",
        )
        payload = {key: memory.get(key) for key in keys}
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _signatures_hash(signatures):
        digest = hashlib.sha256()
        for memory_id in sorted(signatures):
            digest.update(memory_id.encode("utf-8"))
            digest.update(b":")
            digest.update(str(signatures[memory_id]).encode("ascii", errors="ignore"))
            digest.update(b"\n")
        return digest.hexdigest()

    def _memory_fragment(self, memory, memory_ids):
        """Return the compact nodes and derived edges owned by one memory."""
        nodes = {}
        edges = {}

        def add_node(node):
            nodes[node["id"]] = node

        def add_edge(edge):
            edges[edge["id"]] = edge

        memory_id = str(memory.get("id"))
        memory_node_id = self._memory_node_id(memory_id)
        add_node({
            "id": memory_node_id,
            "type": "memory",
            "ref_id": memory_id,
            "label": str(memory.get("title") or "Untitled")[:180],
            "layer": str(memory.get("memory_layer") or "semantic"),
            "date": str(memory.get("date") or "")[:10],
            "active": not bool(memory.get("is_archived")),
            "project_id": str(memory.get("project_id") or ""),
        })

        project_id = str(memory.get("project_id") or "").strip()
        if project_id:
            project_node_id = f"project:{project_id}"
            add_node({
                "id": project_node_id, "type": "project", "ref_id": project_id,
                "label": str(memory.get("project_name") or "Project")[:180], "active": True,
            })
            add_edge(self._edge(memory_node_id, project_node_id, "belongs_to_project", 0.92))

        date_text = str(memory.get("date") or "")[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
            date_node_id = f"date:{date_text}"
            add_node({"id": date_node_id, "type": "date", "ref_id": date_text, "label": date_text, "active": True})
            add_edge(self._edge(memory_node_id, date_node_id, "occurred_on", 0.72))

        detail = memory.get("source_detail") if isinstance(memory.get("source_detail"), dict) else {}
        session_id = str(detail.get("chat_session_id") or detail.get("session_id") or "").strip()
        if session_id:
            session_node_id = f"session:{self._compact_key(session_id)}"
            add_node({"id": session_node_id, "type": "session", "ref_id": session_id, "label": session_id[:180], "active": True})
            add_edge(self._edge(memory_node_id, session_node_id, "from_session", 0.96))

        project_keys = {
            str(memory.get("project_id") or "").strip().lower(),
            str(memory.get("project_name") or "").strip().lower(),
            str(memory.get("category") or "").strip().lower(),
            str(memory.get("memory_layer") or "").strip().lower(),
        }
        for tag in self._safe_list(memory.get("tags"))[:12]:
            tag_name = str(tag or "").strip()
            tag_key = tag_name.lower()
            if not tag_name or tag_key in self.TAG_STOPWORDS or tag_key in project_keys or len(tag_key) < 2:
                continue
            tag_node_id = f"tag:{self._compact_key(tag_name)}"
            add_node({"id": tag_node_id, "type": "tag", "ref_id": tag_name, "label": tag_name[:100], "active": True})
            add_edge(self._edge(memory_node_id, tag_node_id, "tagged_with", 0.68))

        for entity in self._safe_list(memory.get("entities"))[:20]:
            if isinstance(entity, dict):
                entity_name = str(entity.get("name") or entity.get("label") or "").strip()
                entity_type = str(entity.get("type") or "entity").strip().lower()
                confidence = entity.get("confidence", 0.82)
            else:
                entity_name = str(entity or "").strip()
                entity_type = "entity"
                confidence = 0.75
            if not entity_name:
                continue
            entity_node_id = f"entity:{self._compact_key(entity_type + ':' + entity_name)}"
            add_node({
                "id": entity_node_id, "type": "entity", "entity_type": entity_type,
                "ref_id": entity_name, "label": entity_name[:140], "active": True,
            })
            add_edge(self._edge(memory_node_id, entity_node_id, "mentions", confidence))

        for related_id in self._safe_list(memory.get("supersedes"))[:20]:
            if str(related_id) in memory_ids:
                add_edge(self._edge(memory_node_id, self._memory_node_id(str(related_id)), "supersedes", 1.0))
        superseded_by = str(memory.get("superseded_by") or "").strip()
        if superseded_by in memory_ids:
            add_edge(self._edge(self._memory_node_id(superseded_by), memory_node_id, "supersedes", 1.0))
        for related_id in self._safe_list(memory.get("conflicts_with"))[:20]:
            if str(related_id) in memory_ids:
                add_edge(self._edge(memory_node_id, self._memory_node_id(str(related_id)), "conflicts_with", 0.96, directed=False))
        return nodes, edges

    def _complete_graph(self, memories, signatures, manual_edges):
        nodes = {}
        edges = {}
        memory_ids = set(signatures)
        for memory in memories:
            fragment_nodes, fragment_edges = self._memory_fragment(memory, memory_ids)
            nodes.update(fragment_nodes)
            edges.update(fragment_edges)
        for edge in manual_edges:
            if edge.get("source") in nodes and edge.get("target") in nodes:
                edges[edge["id"]] = edge
        return nodes, edges

    def ensure_synced(self, memories, force=False):
        memories = [memory for memory in (memories or []) if isinstance(memory, dict) and memory.get("id")]
        by_memory_id = {str(memory.get("id")): memory for memory in memories}
        signatures = {
            memory_id: self._memory_signature(memory)
            for memory_id, memory in by_memory_id.items()
        }
        source_hash = self._signatures_hash(signatures)
        with self._lock:
            if not force and self.graph.get("source_hash") == source_hash:
                return {"changed": False, **self.status()}

            manual_edges = [
                dict(edge) for edge in self.graph.get("edges", [])
                if edge.get("source_kind") == "manual"
            ]
            previous_signatures = self.graph.get("memory_signatures") or {}
            changed_ids = {
                memory_id for memory_id, signature in signatures.items()
                if previous_signatures.get(memory_id) != signature
            }
            removed_ids = set(previous_signatures) - set(signatures)

            # A legacy graph, an explicit rebuild, or a broad batch change uses
            # the deterministic full path. Ordinary additions and edits only
            # touch their local fragments and connector memberships.
            change_count = len(changed_ids) + len(removed_ids)
            broad_change = change_count > max(250, len(signatures) // 3)
            if force or not previous_signatures or broad_change:
                nodes, edges = self._complete_graph(memories, signatures, manual_edges)
                sync_mode = "full"
                updated_count = len(signatures)
            else:
                affected_ids = set(changed_ids) | set(removed_ids)
                # Direct memory relationships can be declared from either end.
                # Refresh referrers too so adding/removing a target never leaves
                # a stale supersession or conflict edge behind.
                for memory_id, memory in by_memory_id.items():
                    references = {
                        str(value) for value in (
                            self._safe_list(memory.get("supersedes"))
                            + self._safe_list(memory.get("conflicts_with"))
                            + self._safe_list(memory.get("superseded_by"))
                        ) if value
                    }
                    if references & affected_ids:
                        affected_ids.add(memory_id)

                affected_nodes = {self._memory_node_id(memory_id) for memory_id in affected_ids}
                removed_nodes = {self._memory_node_id(memory_id) for memory_id in removed_ids}
                nodes = dict(self.graph.get("nodes") or {})
                edges = {}
                for edge in self.graph.get("edges", []):
                    touches_affected = edge.get("source") in affected_nodes or edge.get("target") in affected_nodes
                    touches_removed = (
                        edge.get("source") in removed_nodes
                        or edge.get("target") in removed_nodes
                    )
                    if touches_removed or (touches_affected and edge.get("source_kind") != "manual"):
                        continue
                    edges[edge["id"]] = dict(edge)
                for node_id in affected_nodes:
                    nodes.pop(node_id, None)

                memory_ids = set(signatures)
                for memory_id in affected_ids & memory_ids:
                    fragment_nodes, fragment_edges = self._memory_fragment(by_memory_id[memory_id], memory_ids)
                    nodes.update(fragment_nodes)
                    edges.update(fragment_edges)

                # Shared connector nodes are compact and derived. Remove those
                # whose final membership dropped to zero after the local patch.
                referenced_nodes = {
                    endpoint
                    for edge in edges.values()
                    for endpoint in (edge.get("source"), edge.get("target"))
                }
                nodes = {
                    node_id: node for node_id, node in nodes.items()
                    if node.get("type") == "memory" or node_id in referenced_nodes
                }
                edges = {
                    edge_id: edge for edge_id, edge in edges.items()
                    if edge.get("source") in nodes and edge.get("target") in nodes
                }
                sync_mode = "incremental"
                updated_count = len(affected_ids)

            self.graph = {
                "schema_version": self.SCHEMA_VERSION,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "source_hash": source_hash,
                "memory_signatures": signatures,
                "nodes": nodes,
                "edges": sorted(edges.values(), key=lambda edge: edge["id"]),
            }
            self._rebuild_indexes()
            self._save()
            return {
                "changed": True,
                "sync_mode": sync_mode,
                "updated_memory_count": updated_count,
                **self.status(),
            }

    def status(self):
        return {
            **self._status_cache,
            "node_types": dict(self._status_cache.get("node_types") or {}),
            "relation_types": dict(self._status_cache.get("relation_types") or {}),
        }

    def _adjacency(self):
        return self._adjacency_index

    def adjacent(self, node_id, relation="", exclude_relations=None):
        relation = str(relation or "").strip()
        excluded = {str(value) for value in (exclude_relations or ())}
        return [
            (other_id, edge)
            for other_id, edge in self._adjacency_index.get(node_id, ())
            if (not relation or edge.get("relation") == relation)
            and edge.get("relation") not in excluded
        ]

    def expand_memory_ids(self, seed_memory_ids, mode="auto", max_results=8):
        mode = str(mode or "auto").strip().lower()
        if mode == "off":
            return []
        nodes = self.graph.get("nodes", {})
        adjacency = self._adjacency()
        connectors = self.DEEP_CONNECTORS if mode == "deep" else self.AUTO_CONNECTORS
        results = {}
        seed_node_ids = {self._memory_node_id(str(memory_id)) for memory_id in (seed_memory_ids or [])}

        def record(candidate_node_id, graph_score, path, relations):
            if candidate_node_id in seed_node_ids:
                return
            node = nodes.get(candidate_node_id) or {}
            if node.get("type") != "memory" or not node.get("active", True):
                return
            candidate = {
                "memory_id": node.get("ref_id"),
                "graph_score": round(graph_score, 4),
                "path": path,
                "relations": relations,
            }
            previous = results.get(candidate["memory_id"])
            if not previous or candidate["graph_score"] > previous["graph_score"]:
                results[candidate["memory_id"]] = candidate

        for seed_node_id in seed_node_ids:
            if seed_node_id not in nodes:
                continue
            for neighbor_id, first_edge in adjacency.get(seed_node_id, []):
                first_relation = str(first_edge.get("relation") or "")
                first_confidence = float(first_edge.get("confidence", 0.5) or 0.5)
                neighbor = nodes.get(neighbor_id) or {}
                if neighbor.get("type") == "memory":
                    record(
                        neighbor_id,
                        first_confidence,
                        [seed_node_id, neighbor_id],
                        [first_relation],
                    )
                    continue
                if first_relation not in connectors:
                    continue
                # Traverse the complete connector membership. The final result
                # and prompt are still bounded, but a high-degree tag can no
                # longer make later memories unreachable merely by insertion
                # order.
                for candidate_id, second_edge in adjacency.get(neighbor_id, []):
                    if candidate_id == seed_node_id:
                        continue
                    second_relation = str(second_edge.get("relation") or "")
                    if second_relation != first_relation:
                        continue
                    second_confidence = float(second_edge.get("confidence", 0.5) or 0.5)
                    record(
                        candidate_id,
                        min(first_confidence, second_confidence) * 0.9,
                        [seed_node_id, neighbor_id, candidate_id],
                        [first_relation, second_relation],
                    )

        try:
            max_results = max(1, min(10000, int(max_results)))
        except (TypeError, ValueError):
            max_results = 8
        return sorted(
            results.values(),
            key=lambda item: (item["graph_score"], str(item.get("memory_id") or "")),
            reverse=True,
        )[:max_results]

    def neighbors(self, memory_id):
        node_id = self._memory_node_id(str(memory_id or ""))
        nodes = self.graph.get("nodes", {})
        edges = []
        neighbor_nodes = {}
        for other_id, edge in self._adjacency_index.get(node_id, ()):
            edges.append(edge)
            if other_id in nodes:
                neighbor_nodes[other_id] = nodes[other_id]
        return {
            "center": nodes.get(node_id),
            "nodes": list(neighbor_nodes.values()),
            "edges": edges,
        }

    def add_manual_edge(self, source_memory_id, target_memory_id, relation="related_to", confidence=0.9):
        relation = str(relation or "related_to").strip().lower()
        if relation not in self.MANUAL_RELATIONS:
            raise ValueError("Unsupported relationship type")
        source = self._memory_node_id(str(source_memory_id or ""))
        target = self._memory_node_id(str(target_memory_id or ""))
        nodes = self.graph.get("nodes", {})
        if source == target or source not in nodes or target not in nodes:
            raise ValueError("Both memories must exist and be different")
        directed = relation not in {"related_to", "conflicts_with"}
        edge = self._edge(source, target, relation, confidence, source_kind="manual", directed=directed)
        with self._lock:
            by_id = {item.get("id"): item for item in self.graph.get("edges", [])}
            by_id[edge["id"]] = edge
            self.graph["edges"] = sorted(by_id.values(), key=lambda item: item["id"])
            self.graph["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._rebuild_indexes()
            self._save()
        return edge

    def remove_manual_edge(self, edge_id):
        with self._lock:
            before = len(self.graph.get("edges", []))
            self.graph["edges"] = [
                edge for edge in self.graph.get("edges", [])
                if not (edge.get("id") == edge_id and edge.get("source_kind") == "manual")
            ]
            if len(self.graph["edges"]) == before:
                return False
            self.graph["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._rebuild_indexes()
            self._save()
            return True

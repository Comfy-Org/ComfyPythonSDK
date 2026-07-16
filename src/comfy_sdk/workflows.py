"""Workflow construction and mutation.

A :class:`Workflow` is a thin, local wrapper over the raw API-format graph. The
graph stays a freely-mutable ``dict`` (``wf.json``); ``set_input`` is sugar for
``wf.json[node][\"inputs\"][field] = value`` that also accepts an asset handle
(substituted into a ``core/ASSET`` object at submit time). Construction does no
network I/O in v1.
"""

from __future__ import annotations

import json as _json
from os import PathLike
from typing import Any


class Workflow:
    def __init__(self, graph: dict[str, Any]) -> None:
        self.json = graph

    def set_input(self, node_id: str, field: str, value: Any) -> None:
        """Set ``node.inputs.field``. ``value`` may be a plain JSON value or an
        asset handle; handles are substituted into ``core/ASSET`` objects when
        the workflow is submitted.
        """
        node = self.json.setdefault(node_id, {})
        inputs = node.setdefault("inputs", {})
        inputs[field] = value

    def __repr__(self) -> str:
        return f"Workflow(nodes={len(self.json)})"


class WorkflowFactory:
    """``client.workflows`` — alternative constructors for :class:`Workflow`.

    Namespaced on the client (rather than free-standing) because construction is
    expected to become client-bound once server-side subgraphs land; in v1 it is
    purely local.
    """

    def from_file(self, path: str | PathLike[str]) -> Workflow:
        with open(path, encoding="utf-8") as fh:
            return Workflow(_json.load(fh))

    def from_json(self, graph: dict[str, Any]) -> Workflow:
        return Workflow(graph)

    def from_str(self, text: str) -> Workflow:
        return Workflow(_json.loads(text))

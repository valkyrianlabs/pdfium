"""Read gclient DEPS files without executing them.

DEPS is Python syntax that gclient `exec`s. We only need `vars` and `deps`, so the file is parsed
with `ast` and a tiny evaluator that understands literals, string concatenation, dicts and the
`Var()`/`Str()` helpers. Anything else (e.g. conditions) is kept as raw text.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass


class DepsError(ValueError):
    pass


@dataclass(frozen=True)
class DepsEntry:
    path: str
    url: str  # repository URL without the @revision
    revision: str
    condition: str | None


def _evaluate(node: ast.AST, variables: dict[str, object]) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _evaluate(node.left, variables) + _evaluate(node.right, variables)  # type: ignore[operator]
    if isinstance(node, ast.Dict):
        return {_evaluate(k, variables): _evaluate(v, variables) for k, v in zip(node.keys, node.values) if k is not None}
    if isinstance(node, ast.List):
        return [_evaluate(item, variables) for item in node.elts]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and len(node.args) == 1:
        argument = _evaluate(node.args[0], variables)
        if node.func.id == "Var":
            if argument not in variables:
                raise DepsError(f"Var({argument!r}) is not defined")
            return variables[argument]
        if node.func.id == "Str":
            return argument
    if isinstance(node, ast.Name) and node.id in ("True", "False", "None"):
        return {"True": True, "False": False, "None": None}[node.id]
    raise DepsError(f"unsupported DEPS expression: {ast.dump(node)[:120]}")


def parse_deps(text: str) -> dict[str, DepsEntry]:
    tree = ast.parse(text, filename="DEPS")
    assignments: dict[str, ast.AST] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            assignments[statement.targets[0].id] = statement.value
    variables: dict[str, object] = {}
    if "vars" in assignments:
        raw_vars = assignments["vars"]
        if not isinstance(raw_vars, ast.Dict):
            raise DepsError("DEPS vars is not a dict")
        # vars may reference earlier vars; evaluate in order.
        for key, value in zip(raw_vars.keys, raw_vars.values):
            name = _evaluate(key, variables)  # type: ignore[arg-type]
            try:
                variables[name] = _evaluate(value, variables)  # type: ignore[index]
            except DepsError:
                variables[name] = None  # type: ignore[index]
    if "deps" not in assignments or not isinstance(assignments["deps"], ast.Dict):
        raise DepsError("DEPS has no deps dict")
    entries: dict[str, DepsEntry] = {}
    raw_deps = assignments["deps"]
    for key, value in zip(raw_deps.keys, raw_deps.values):  # type: ignore[attr-defined]
        path = _evaluate(key, variables)
        try:
            spec = _evaluate(value, variables)
        except DepsError:
            continue  # CIPD/GCS entries with unsupported shapes; not needed
        condition = None
        if isinstance(spec, dict):
            if spec.get("dep_type", "git") != "git" or "url" not in spec:
                continue
            condition = spec.get("condition")
            spec = spec["url"]
        if not isinstance(spec, str) or "@" not in spec:
            continue
        url, _, revision = spec.rpartition("@")
        entries[str(path)] = DepsEntry(path=str(path), url=url, revision=revision, condition=condition)
    return entries

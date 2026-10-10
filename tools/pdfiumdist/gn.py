"""A static reader for the subset of GN that PDFium's BUILD.gn files use.

This is deliberately *not* a GN interpreter: it never runs GN, imports no .gni files and knows
nothing about toolchains. It tokenizes and parses BUILD.gn files, evaluates `if` conditions
against an explicit build-argument profile (see `[audit.args]` in upstream.toml) and records,
per target, the list-valued variables the Meson build has to mirror (sources, defines, deps,
include_dirs, ...). Anything it cannot decide statically (an unknown identifier in a condition,
a function call in an expression) is reported as an *undecided* item instead of being guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --- tokens ----------------------------------------------------------------------------------

_TOKEN = re.compile(
    r"""
    (?P<ws>[ \t\r\n]+)
  | (?P<comment>\#[^\n]*)
  | (?P<string>"(?:\\.|[^"\\])*")
  | (?P<number>-?\d+)
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>\+=|-=|==|!=|<=|>=|&&|\|\||[=+\-!<>(){}\[\],.])
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    line: int


class GnSyntaxError(ValueError):
    pass


def tokenize(text: str, origin: str) -> list[Token]:
    tokens: list[Token] = []
    pos = 0
    line = 1
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match:
            raise GnSyntaxError(f"{origin}:{line}: unexpected character {text[pos]!r}")
        kind = match.lastgroup
        value = match.group()
        if kind not in ("ws", "comment"):
            tokens.append(Token(kind, value, line))
        line += value.count("\n")
        pos = match.end()
    tokens.append(Token("eof", "", line))
    return tokens


# --- AST -------------------------------------------------------------------------------------


@dataclass
class Assign:
    name: str
    op: str
    value: Any
    line: int


@dataclass
class Call:
    name: str
    args: list[Any]
    block: list[Any] | None
    line: int


@dataclass
class Cond:
    condition: Any
    then: list[Any]
    otherwise: list[Any] | None
    line: int
    text: str


# Expression nodes are tuples: ("lit", value) ("id", name) ("not", e) ("bin", op, l, r)
# ("list", [e...]) ("call", name, [args]) ("member", e, name) ("index", e, i)


class _Parser:
    def __init__(self, tokens: list[Token], origin: str, text: str):
        self.tokens = tokens
        self.pos = 0
        self.origin = origin
        self.lines = text.split("\n")

    def peek(self, offset: int = 0) -> Token:
        return self.tokens[self.pos + offset]

    def take(self, value: str | None = None, kind: str | None = None) -> Token:
        token = self.tokens[self.pos]
        if value is not None and token.value != value:
            raise GnSyntaxError(f"{self.origin}:{token.line}: expected {value!r}, got {token.value!r}")
        if kind is not None and token.kind != kind:
            raise GnSyntaxError(f"{self.origin}:{token.line}: expected {kind}, got {token.value!r}")
        self.pos += 1
        return token

    def statements(self, until: str | None) -> list[Any]:
        result = []
        while True:
            token = self.peek()
            if token.kind == "eof":
                if until is not None:
                    raise GnSyntaxError(f"{self.origin}: unexpected end of file")
                return result
            if until is not None and token.value == until:
                return result
            result.append(self.statement())

    def statement(self) -> Any:
        token = self.take(kind="ident")
        nxt = self.peek()
        if token.value == "if" and nxt.value == "(":
            return self.conditional(token)
        if nxt.value in ("=", "+=", "-="):
            self.take()
            return Assign(token.value, nxt.value, self.expression(), token.line)
        if nxt.value == "(":
            self.take("(")
            args = self.arguments(")")
            block = None
            if self.peek().value == "{":
                self.take("{")
                block = self.statements("}")
                self.take("}")
            return Call(token.value, args, block, token.line)
        if nxt.value in (".", "["):
            # Scope member / list element assignment (`foo.bar = ...`): keep the base name.
            target = self.postfix(("id", token.value))
            op = self.take()
            if op.value not in ("=", "+=", "-="):
                raise GnSyntaxError(f"{self.origin}:{op.line}: unsupported statement")
            return Assign(_describe(target), op.value, self.expression(), token.line)
        raise GnSyntaxError(f"{self.origin}:{token.line}: unsupported statement at {token.value!r}")

    def conditional(self, token: Token) -> Cond:
        self.take("(")
        start = self.pos
        condition = self.expression()
        text = " ".join(t.value for t in self.tokens[start : self.pos])
        self.take(")")
        self.take("{")
        then = self.statements("}")
        self.take("}")
        otherwise = None
        if self.peek().value == "else":
            self.take("else")
            if self.peek().value == "if":
                otherwise = [self.conditional(self.take("if"))]
            else:
                self.take("{")
                otherwise = self.statements("}")
                self.take("}")
        return Cond(condition, then, otherwise, token.line, text)

    def arguments(self, closing: str) -> list[Any]:
        args = []
        while self.peek().value != closing:
            args.append(self.expression())
            if self.peek().value == ",":
                self.take(",")
        self.take(closing)
        return args

    # Precedence climbing: || < && < == != < > <= >= < + - < unary !
    _BINARY = [("||",), ("&&",), ("==", "!=", "<", ">", "<=", ">="), ("+", "-")]

    def expression(self, level: int = 0) -> Any:
        if level == len(self._BINARY):
            return self.unary()
        left = self.expression(level + 1)
        while self.peek().kind == "op" and self.peek().value in self._BINARY[level]:
            op = self.take().value
            right = self.expression(level + 1)
            left = ("bin", op, left, right)
        return left

    def unary(self) -> Any:
        if self.peek().value == "!":
            self.take("!")
            return ("not", self.unary())
        return self.postfix(self.primary())

    def postfix(self, node: Any) -> Any:
        while self.peek().value in (".", "["):
            if self.take().value == ".":
                node = ("member", node, self.take(kind="ident").value)
            else:
                index = self.expression()
                self.take("]")
                node = ("index", node, index)
        return node

    def primary(self) -> Any:
        token = self.take()
        if token.kind == "string":
            return ("lit", _unquote(token.value))
        if token.kind == "number":
            return ("lit", int(token.value))
        if token.kind == "ident":
            if token.value in ("true", "false"):
                return ("lit", token.value == "true")
            if self.peek().value == "(":
                self.take("(")
                return ("call", token.value, self.arguments(")"))
            return ("id", token.value)
        if token.value == "(":
            inner = self.expression()
            self.take(")")
            return inner
        if token.value == "[":
            return ("list", self.arguments("]"))
        if token.value == "{":
            # A scope literal; its contents are irrelevant for the audit.
            self.statements("}")
            self.take("}")
            return ("unknown", "scope literal")
        raise GnSyntaxError(f"{self.origin}:{token.line}: unexpected {token.value!r}")


def _unquote(raw: str) -> str:
    body = raw[1:-1]
    return re.sub(r"\\(.)", r"\1", body)


def _describe(node: Any) -> str:
    kind = node[0]
    if kind == "id":
        return node[1]
    if kind == "member":
        return f"{_describe(node[1])}.{node[2]}"
    if kind == "index":
        return f"{_describe(node[1])}[...]"
    return kind


def parse(text: str, origin: str) -> list[Any]:
    return _Parser(tokenize(text, origin), origin, text).statements(None)


# --- evaluation ------------------------------------------------------------------------------


class Unknown:
    """A value the static reader cannot determine."""

    def __init__(self, why: str):
        self.why = why

    def __repr__(self) -> str:
        return f"Unknown({self.why})"


# Target-defining functions. Templates PDFium defines itself are treated like source sets.
TARGET_FUNCTIONS = {
    "source_set",
    "static_library",
    "shared_library",
    "component",
    "executable",
    "loadable_module",
    "group",
    "test",
    "pdfium_unittest_source_set",
    "pdfium_embeddertest_source_set",
    "rust_static_library",
    "action",
    "action_foreach",
    "copy",
}
RECORDED_VARIABLES = (
    "sources",
    "defines",
    "deps",
    "public_deps",
    "include_dirs",
    "cflags",
    "cflags_c",
    "cflags_cc",
    "configs",
    "public_configs",
    "all_dependent_configs",
    "libs",
)
# Config-like blocks; pkg_config() carries the USE_SYSTEM_* defines for system libraries.
CONFIG_FUNCTIONS = {"config", "pkg_config"}
IGNORED_FUNCTIONS = {"import", "assert", "template", "declare_args", "print", "not_needed", "set_defaults"}


@dataclass
class Target:
    label: str  # //dir:name
    kind: str
    file: str
    line: int
    values: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Undecided:
    file: str
    line: int
    what: str


@dataclass
class GnFile:
    path: str  # relative to the source root, e.g. core/fxcrt/BUILD.gn
    targets: dict[str, Target] = field(default_factory=dict)
    configs: dict[str, Target] = field(default_factory=dict)
    declared_args: list[str] = field(default_factory=list)
    undecided: list[Undecided] = field(default_factory=list)


class _Scope:
    def __init__(self, parent: "_Scope | None"):
        self.parent = parent
        self.values: dict[str, Any] = {}

    def lookup(self, name: str) -> Any:
        scope: _Scope | None = self
        while scope is not None:
            if name in scope.values:
                return scope.values[name]
            scope = scope.parent
        raise KeyError(name)

    def defined(self, name: str) -> bool:
        try:
            self.lookup(name)
            return True
        except KeyError:
            return False


class Evaluator:
    def __init__(self, root: Path, args: dict[str, Any]):
        self.root = root
        self.args = dict(args)

    def read(self, relative: str) -> GnFile:
        path = self.root / relative
        result = GnFile(path=relative)
        statements = parse(path.read_text(encoding="utf-8"), relative)
        self._directory = str(Path(relative).parent).replace("\\", "/")
        if self._directory == ".":
            self._directory = ""
        self._file = result
        top = _Scope(None)
        top.values.update(self.args)
        self._run(statements, top, target=None)
        return result

    # statements

    def _run(self, statements: list[Any], scope: _Scope, target: Target | None) -> None:
        for statement in statements:
            if isinstance(statement, Assign):
                self._assign(statement, scope)
            elif isinstance(statement, Cond):
                self._conditional(statement, scope, target)
            elif isinstance(statement, Call):
                self._call(statement, scope, target)

    def _assign(self, statement: Assign, scope: _Scope) -> None:
        value = self._eval(statement.value, scope)
        if "." in statement.name or "[" in statement.name:
            return
        if statement.op == "=":
            scope.values[statement.name] = value
            return
        try:
            current = scope.lookup(statement.name)
        except KeyError:
            current = [] if isinstance(value, list) else Unknown(f"undefined {statement.name}")
        if statement.op == "+=":
            new = _add(current, value)
        else:
            new = _subtract(current, value)
        scope.values[statement.name] = new

    def _conditional(self, statement: Cond, scope: _Scope, target: Target | None) -> None:
        value = self._eval(statement.condition, scope)
        if isinstance(value, Unknown) or not isinstance(value, bool):
            self._file.undecided.append(
                Undecided(self._file.path, statement.line, f"if ({statement.text}): {getattr(value, 'why', value)!s}")
            )
            return
        branch = statement.then if value else statement.otherwise
        if branch:
            # GN `if` blocks do not open a new scope.
            self._run(branch, scope, target)

    def _call(self, statement: Call, scope: _Scope, target: Target | None) -> None:
        name = statement.name
        if name in IGNORED_FUNCTIONS:
            if name == "declare_args" and statement.block:
                for inner in statement.block:
                    if isinstance(inner, Assign):
                        self._file.declared_args.append(inner.name)
            return
        if name == "forward_variables_from":
            self._file.undecided.append(Undecided(self._file.path, statement.line, "forward_variables_from()"))
            return
        if name == "foreach":
            self._file.undecided.append(Undecided(self._file.path, statement.line, "foreach()"))
            return
        if statement.block is None:
            return
        if name in TARGET_FUNCTIONS or name in CONFIG_FUNCTIONS:
            label_name = self._eval(statement.args[0], scope) if statement.args else Unknown("no name")
            if not isinstance(label_name, str):
                self._file.undecided.append(Undecided(self._file.path, statement.line, f"{name}() with computed name"))
                return
            inner = _Scope(scope)
            for variable in RECORDED_VARIABLES:
                inner.values[variable] = []
            record = Target(label=f"//{self._directory}:{label_name}", kind=name, file=self._file.path, line=statement.line)
            self._run(statement.block, inner, record)
            for variable in RECORDED_VARIABLES:
                value = inner.values.get(variable, [])
                if isinstance(value, Unknown):
                    self._file.undecided.append(
                        Undecided(self._file.path, statement.line, f"{record.label} {variable}: {value.why}")
                    )
                    continue
                items = [item for item in value if isinstance(item, str)]
                if len(items) != len(value):
                    self._file.undecided.append(
                        Undecided(self._file.path, statement.line, f"{record.label} {variable}: non-string items")
                    )
                if items:
                    record.values[variable] = [self._normalize(variable, item) for item in items]
            bucket = self._file.configs if name in CONFIG_FUNCTIONS else self._file.targets
            bucket[record.label] = record
            return
        self._file.undecided.append(Undecided(self._file.path, statement.line, f"unhandled block call {name}()"))

    def _normalize(self, variable: str, item: str) -> str:
        if variable in ("sources", "include_dirs"):
            return _source_path(self._directory, item)
        if variable in ("deps", "public_deps", "configs", "public_configs", "all_dependent_configs"):
            return _label(self._directory, item)
        return item

    # expressions

    def _eval(self, node: Any, scope: _Scope) -> Any:
        kind = node[0]
        if kind == "lit":
            return node[1]
        if kind == "id":
            try:
                return scope.lookup(node[1])
            except KeyError:
                return Unknown(f"unknown identifier {node[1]}")
        if kind == "list":
            return [self._eval(item, scope) for item in node[1]]
        if kind == "not":
            value = self._eval(node[1], scope)
            return (not value) if isinstance(value, bool) else Unknown(getattr(value, "why", "non-boolean"))
        if kind == "bin":
            return self._binary(node[1], node[2], node[3], scope)
        if kind == "call":
            if node[1] == "defined" and node[2] and node[2][0][0] == "id":
                return scope.defined(node[2][0][1])
            return Unknown(f"function {node[1]}()")
        return Unknown(f"{kind} expression")

    def _binary(self, op: str, left_node: Any, right_node: Any, scope: _Scope) -> Any:
        left = self._eval(left_node, scope)
        if op == "&&":
            if left is False:
                return False
            right = self._eval(right_node, scope)
            if right is False:
                return False
            if left is True and right is True:
                return True
            return Unknown(_why(left, right))
        if op == "||":
            if left is True:
                return True
            right = self._eval(right_node, scope)
            if right is True:
                return True
            if left is False and right is False:
                return False
            return Unknown(_why(left, right))
        right = self._eval(right_node, scope)
        if isinstance(left, Unknown) or isinstance(right, Unknown):
            return Unknown(_why(left, right))
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == "+":
            return _add(left, right)
        if op == "-":
            return _subtract(left, right)
        try:
            return {"<": left < right, ">": left > right, "<=": left <= right, ">=": left >= right}[op]
        except TypeError:
            return Unknown(f"cannot compare with {op}")


def _why(left: Any, right: Any) -> str:
    for value in (left, right):
        if isinstance(value, Unknown):
            return value.why
    return "non-boolean operand"


def _add(left: Any, right: Any) -> Any:
    if isinstance(left, Unknown) or isinstance(right, Unknown):
        return left if isinstance(left, Unknown) else right
    if isinstance(left, list):
        return left + (right if isinstance(right, list) else [right])
    if isinstance(left, str) and isinstance(right, str):
        return left + right
    if isinstance(left, int) and isinstance(right, int):
        return left + right
    return Unknown("unsupported +")


def _subtract(left: Any, right: Any) -> Any:
    if isinstance(left, Unknown) or isinstance(right, Unknown):
        return left if isinstance(left, Unknown) else right
    if isinstance(left, list):
        remove = right if isinstance(right, list) else [right]
        return [item for item in left if item not in remove]
    if isinstance(left, int) and isinstance(right, int):
        return left - right
    return Unknown("unsupported -")


def _join(directory: str, relative: str) -> str:
    parts = [p for p in directory.split("/") if p] if directory else []
    for piece in relative.split("/"):
        if piece in ("", "."):
            continue
        if piece == "..":
            if parts:
                parts.pop()
            continue
        parts.append(piece)
    return "/".join(parts)


def _source_path(directory: str, item: str) -> str:
    """A path relative to the PDFium source root (`//foo` → `foo`)."""
    if item.startswith("//"):
        return item[2:]
    return _join(directory, item)


def _label(directory: str, item: str) -> str:
    """Normalize a GN label to `//dir:name` (external `//third_party/...` labels stay as written)."""
    if item.startswith("//"):
        path, _, name = item[2:].partition(":")
        return f"//{path}:{name or path.rsplit('/', 1)[-1]}"
    if item.startswith(":"):
        return f"//{directory}{item}"
    path, _, name = item.partition(":")
    joined = _join(directory, path)
    return f"//{joined}:{name or (joined.rsplit('/', 1)[-1] if joined else '')}"


def label_file(label: str) -> str:
    """The BUILD.gn path (relative to the source root) that defines `label`."""
    directory = label[2:].split(":", 1)[0]
    return f"{directory}/BUILD.gn" if directory else "BUILD.gn"

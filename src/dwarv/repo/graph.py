"""DWARV_PLAN.md section 11.7: a lightweight code knowledge graph, queried
per-turn for only the context a given question actually needs, replacing
snapshot_repo_files()'s whole-repo character-budget dump.

MVP-scale, same honesty standard as the function it replaces (see
docs/DECISIONS.md on snapshot_repo_files's own documented limitation):
Python only (via the stdlib `ast` module, no new dependency), and the
call graph is name-based matching, not real type-resolved static
analysis -- two functions named `run` in different classes are treated as
the same node. This is a real, stated simplification, not hidden as if it
were precise.
"""

import ast
from dataclasses import dataclass, field
from pathlib import Path

_SKIP_DIR_NAMES = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".pytest_cache",
    ".mypy_cache",
}


@dataclass
class Symbol:
    id: str  # "relative/path.py::Class.method" or "relative/path.py::func"
    kind: str  # "function" | "class" | "module"
    name: str
    file: str  # relative path, posix-style
    line_start: int
    line_end: int
    source: str


@dataclass
class RepoGraph:
    symbols: dict[str, Symbol] = field(default_factory=dict)
    calls: dict[str, set[str]] = field(default_factory=dict)  # symbol id -> callee ids
    called_by: dict[str, set[str]] = field(default_factory=dict)  # symbol id -> caller ids
    file_symbols: dict[str, list[str]] = field(default_factory=dict)  # file -> symbol ids, in order


def build_graph(root: Path) -> RepoGraph:
    graph = RepoGraph()
    name_to_ids: dict[str, set[str]] = {}  # bare name -> every symbol id with that name

    for path in sorted(root.rglob("*.py")):
        if any(part in _SKIP_DIR_NAMES for part in path.relative_to(root).parts):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        rel = path.relative_to(root).as_posix()
        lines = source.splitlines()
        graph.file_symbols[rel] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                qualname = _qualname(tree, node)
                sym_id = f"{rel}::{qualname}"
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                end_line = getattr(node, "end_lineno", node.lineno)
                sym = Symbol(
                    id=sym_id,
                    kind=kind,
                    name=qualname,
                    file=rel,
                    line_start=node.lineno,
                    line_end=end_line,
                    source="\n".join(lines[node.lineno - 1 : end_line]),
                )
                graph.symbols[sym_id] = sym
                graph.file_symbols[rel].append(sym_id)
                name_to_ids.setdefault(node.name, set()).add(sym_id)
                graph.calls.setdefault(sym_id, set())
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    for call_node in ast.walk(node):
                        if isinstance(call_node, ast.Call):
                            called_name = _call_target_name(call_node)
                            if called_name:
                                graph.calls[sym_id].add(called_name)

    # Resolve call targets (bare names collected above) to real symbol ids
    # now that every symbol in the repo is known, and build the reverse
    # (called_by) index at the same time.
    for sym_id, called_names in list(graph.calls.items()):
        resolved: set[str] = set()
        for name in called_names:
            resolved |= name_to_ids.get(name, set())
        resolved.discard(sym_id)
        graph.calls[sym_id] = resolved
        for callee_id in resolved:
            graph.called_by.setdefault(callee_id, set()).add(sym_id)

    return graph


def _qualname(tree: ast.AST, target: ast.AST) -> str:
    """'ClassName.method_name' for a method, else just the function/class's
    own name -- found by walking the tree looking for which ClassDef (if
    any) directly contains `target` as a body statement."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and target in node.body:
            return f"{node.name}.{target.name}"
    return target.name


def _call_target_name(call_node: ast.Call) -> str | None:
    func = call_node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr  # best-effort: ignores the receiver, matches by method name alone
    return None


def query_relevant_context(graph: RepoGraph, query_text: str, max_total_chars: int = 4000) -> str:
    """Finds symbols named in `query_text` (exact, case-sensitive name
    match against known function/class names -- simple and precise rather
    than fuzzy, matching the Graphify-inspired "cited, not approximate"
    goal), includes their source plus immediate callers/callees, and falls
    back to a table-of-contents (symbol signatures only, no bodies) when
    nothing matches so a vague question still gets *something* oriented
    rather than silence. Bounded by max_total_chars like
    snapshot_repo_files() was, so this is a strict budget reduction, not
    an unbounded addition."""
    words = {w.strip("()[]{}:,.\"'") for w in query_text.split()}
    matched = [
        sym_id
        for sym_id, sym in graph.symbols.items()
        if sym.name in words or sym.name.rsplit(".", 1)[-1] in words
    ]

    if not matched:
        return _table_of_contents(graph, max_total_chars)

    include_ids: list[str] = []
    seen: set[str] = set()
    for sym_id in matched:
        if sym_id not in seen:
            include_ids.append(sym_id)
            seen.add(sym_id)
        for neighbor_id in graph.calls.get(sym_id, set()) | graph.called_by.get(sym_id, set()):
            if neighbor_id not in seen:
                include_ids.append(neighbor_id)
                seen.add(neighbor_id)

    blocks = []
    total = 0
    for sym_id in include_ids:
        sym = graph.symbols[sym_id]
        block = (
            f"```python:{sym.file}  (lines {sym.line_start}-{sym.line_end})\n{sym.source}\n```\n"
        )
        if total + len(block) > max_total_chars:
            break
        blocks.append(block)
        total += len(block)
    return "\n".join(blocks)


def _table_of_contents(graph: RepoGraph, max_total_chars: int) -> str:
    lines = []
    total = 0
    for file, sym_ids in graph.file_symbols.items():
        if not sym_ids:
            continue
        header = f"# {file}\n"
        if total + len(header) > max_total_chars:
            break
        lines.append(header)
        total += len(header)
        for sym_id in sym_ids:
            sym = graph.symbols[sym_id]
            entry = f"  {sym.kind} {sym.name} (line {sym.line_start})\n"
            if total + len(entry) > max_total_chars:
                break
            lines.append(entry)
            total += len(entry)
    return "".join(lines)

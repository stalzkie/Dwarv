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
import os
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

# Found live-testing GPU offload on this repo itself: a differently-named
# virtualenv (here, ".venv_compress", a leftover from an earlier
# experiment -- 9,898 real .py files) isn't caught by the exact-name list
# above, so build_graph() spent minutes ast.parse()-ing third-party
# torch/transformers code that has nothing to do with the user's actual
# project -- both a real hang and a correctness problem (the "relevant
# context" query would surface unrelated library internals). Every
# virtualenv, regardless of its directory name, has this exact marker
# file at its root (both ".venv" and ".venv_compress" here have one) --
# detecting it structurally catches variants no fixed name list can
# anticipate (".venv311", "env", a custom name, etc.).
_VENV_MARKER_FILE = "pyvenv.cfg"


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


def _collect_py_files(root: Path) -> list[Path]:
    """os.walk (not Path.rglob) so unwanted directories -- by name, or by
    carrying a venv's pyvenv.cfg marker -- are pruned via dirnames[:]
    *before* os.walk descends into them, instead of walking the whole
    subtree first and filtering results after. For a large third-party
    venv (thousands of files, e.g. ".venv_compress" in this very repo)
    that's the difference between skipping the directory instantly and
    walking+parsing everything inside it."""
    py_files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _SKIP_DIR_NAMES and not (Path(dirpath) / d / _VENV_MARKER_FILE).exists()
        ]
        py_files.extend(Path(dirpath) / f for f in filenames if f.endswith(".py"))
    return py_files


def build_graph(root: Path) -> RepoGraph:
    graph = RepoGraph()
    name_to_ids: dict[str, set[str]] = {}  # bare name -> every symbol id with that name

    for path in sorted(_collect_py_files(root)):
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


def _render_source_blocks(
    graph: RepoGraph, sym_ids: list[str], max_total_chars: int
) -> tuple[str, bool]:
    """Renders each symbol's real source as a fenced block, stopping once
    max_total_chars would be exceeded. Returns (text, truncated) so a
    caller can tell "everything fit" apart from "this is a partial dump"
    -- query_relevant_context's whole-repo fallback below needs that
    distinction to know whether a table-of-contents is actually necessary."""
    blocks = []
    total = 0
    truncated = False
    for sym_id in sym_ids:
        sym = graph.symbols[sym_id]
        block = (
            f"```python:{sym.file}  (lines {sym.line_start}-{sym.line_end})\n{sym.source}\n```\n"
        )
        if total + len(block) > max_total_chars:
            truncated = True
            break
        blocks.append(block)
        total += len(block)
    return "\n".join(blocks), truncated


def query_relevant_context(graph: RepoGraph, query_text: str, max_total_chars: int = 4000) -> str:
    """Finds symbols named in `query_text` (exact, case-sensitive name
    match against known function/class names, or a mentioned file's own
    basename -- simple and precise rather than fuzzy, matching the
    Graphify-inspired "cited, not approximate" goal), includes their
    source plus immediate callers/callees (or, for a file match, every
    symbol in that file). When nothing matches by name at all -- a vague
    "fix the bug" with no symbol/file named, live-observed to be a real
    failure mode on a tiny demo repo where the model never saw the actual
    buggy code -- falls back to the FULL repo's source if that fits the
    budget (common for a small repo, where there's nothing to gain by
    hiding it), and only to a bodiless table-of-contents when the repo is
    genuinely too large to dump whole. Bounded by max_total_chars like
    snapshot_repo_files() was, so this is a strict budget reduction for
    large repos, not an unbounded addition."""
    words = {w.strip("()[]{}:,.\"'") for w in query_text.split()}
    mentioned_files = {f for f in graph.file_symbols if Path(f).name in words or f in words}

    matched = [
        sym_id
        for sym_id, sym in graph.symbols.items()
        if sym.name in words or sym.name.rsplit(".", 1)[-1] in words or sym.file in mentioned_files
    ]

    if not matched:
        # Nothing named a known symbol or file -- try the whole repo
        # before giving up to a bodiless table of contents; for a small
        # repo this is cheap and means a vague "fix the bug" still sees
        # real code instead of just symbol names.
        whole_text, truncated = _render_source_blocks(graph, list(graph.symbols), max_total_chars)
        if not truncated:
            return whole_text
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

    text, _truncated = _render_source_blocks(graph, include_ids, max_total_chars)
    return text


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

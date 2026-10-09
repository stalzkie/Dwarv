import json
from dataclasses import dataclass
from pathlib import Path

_ROOT_MARKERS = (".git", "pyproject.toml", "setup.py", "package.json")
_SKIP_DIR_NAMES = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".pytest_cache",
    ".mypy_cache",
}
_TEXT_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".md",
    ".txt",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
}


@dataclass
class RepoContext:
    root: Path
    is_git_repo: bool
    test_command: list[str] | None  # None if nothing discoverable


def detect_repo_context(start_dir: str | Path = ".") -> RepoContext:
    """Find the repo root above `start_dir` and an existing test command, if
    one is discoverable. If nothing is discoverable, the agent must say so
    plainly rather than claim an unearned verification."""
    root = _find_repo_root(Path(start_dir).resolve())
    is_git_repo = (root / ".git").exists()
    return RepoContext(root=root, is_git_repo=is_git_repo, test_command=_detect_test_command(root))


def _find_repo_root(start: Path) -> Path:
    current = start
    while True:
        if any((current / marker).exists() for marker in _ROOT_MARKERS):
            return current
        if current.parent == current:
            return start  # no markers found anywhere above start; fall back to start itself
        current = current.parent


def _detect_test_command(root: Path) -> list[str] | None:
    if (root / "pytest.ini").exists() or (root / "tests").is_dir():
        return ["pytest"]
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            if "[tool.pytest" in pyproject.read_text(encoding="utf-8"):
                return ["pytest"]
        except OSError:
            pass

    package_json = root / "package.json"
    if package_json.exists():
        try:
            pkg = json.loads(package_json.read_text(encoding="utf-8"))
            if "test" in pkg.get("scripts", {}):
                return ["npm", "test"]
        except (OSError, ValueError):
            pass

    return None


def snapshot_repo_files(root: Path, max_total_chars: int = 8000) -> str:
    """A small, fenced-block snapshot of the repo's source files, so the
    model actually sees the code it's being asked about instead of guessing
    blind. MVP-scale only -- reads whatever fits under max_total_chars in
    path order, not real retrieval/ranking; see docs/DECISIONS.md."""
    blocks = []
    total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in _TEXT_EXTENSIONS:
            continue
        if any(part in _SKIP_DIR_NAMES for part in path.relative_to(root).parts):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(root).as_posix()
        block = f"```{path.suffix.lstrip('.')}:{rel}\n{content}\n```\n"
        if total + len(block) > max_total_chars:
            continue
        blocks.append(block)
        total += len(block)
    return "\n".join(blocks)

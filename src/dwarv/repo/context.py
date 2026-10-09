import json
from dataclasses import dataclass
from pathlib import Path

_ROOT_MARKERS = (".git", "pyproject.toml", "setup.py", "package.json")


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

import difflib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Patch:
    file_path: Path  # relative to a repo root
    new_content: str
    old_content: str | None  # None if the file is being created
    diff_text: str  # unified diff, for showing the user before applying


def make_patch(repo_root: Path, relative_path: str, new_content: str) -> Patch:
    """Build a Patch from the model's proposed full file content, diffed
    against whatever is currently on disk (or nothing, if the file is new).
    We diff full-file-replacement ourselves rather than asking the model to
    emit a unified diff -- LLMs are far more reliable at producing a correct
    full file than a byte-exact patch; see docs/DECISIONS.md."""
    target = repo_root / relative_path
    old_content = target.read_text(encoding="utf-8") if target.exists() else None
    diff_text = "".join(
        difflib.unified_diff(
            (old_content or "").splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{relative_path}",
            tofile=f"b/{relative_path}",
        )
    )
    return Patch(
        file_path=Path(relative_path),
        new_content=new_content,
        old_content=old_content,
        diff_text=diff_text,
    )


def apply_patch(patch: Patch, target_root: Path) -> None:
    """Write patch.new_content to target_root/patch.file_path. Callers are
    responsible for only calling this against a disposable worktree until
    verification passes -- this function has no idea whether target_root is
    real or disposable, by design (keeps it trivially testable)."""
    dest = target_root / patch.file_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(patch.new_content, encoding="utf-8")

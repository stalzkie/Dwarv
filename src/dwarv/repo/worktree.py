import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def disposable_worktree(repo_root: Path, is_git_repo: bool):
    """Yields a Path to a disposable copy of `repo_root` for trial edits and
    test runs. Uses `git worktree add` (fast, no duplicated object storage)
    for git repos, a plain recursive copy otherwise. Always cleaned up on
    exit, even on error -- the real working tree is never touched by
    whatever happens inside this context."""
    if is_git_repo:
        tmp_parent = Path(tempfile.mkdtemp(prefix="dwarv-worktree-"))
        wt_path = tmp_parent / "wt"  # must not exist yet for `git worktree add`
        try:
            subprocess.run(
                ["git", "worktree", "add", "--detach", str(wt_path), "HEAD"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )
            yield wt_path
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt_path)],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )
            shutil.rmtree(tmp_parent, ignore_errors=True)
    else:
        tmp_dir = Path(tempfile.mkdtemp(prefix="dwarv-worktree-"))
        try:
            shutil.copytree(
                repo_root, tmp_dir, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git")
            )
            yield tmp_dir
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

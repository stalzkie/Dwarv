import subprocess
from pathlib import Path

from dwarv.repo.context import detect_repo_context, snapshot_repo_files
from dwarv.repo.patch import apply_patch, make_patch
from dwarv.repo.worktree import disposable_worktree


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_app.py").write_text(
        "from app import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


def test_detect_repo_context_finds_git_root_and_pytest(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _init_git_repo(repo)

    ctx = detect_repo_context(repo / "tests")

    assert ctx.root == repo
    assert ctx.is_git_repo is True
    assert ctx.test_command == ["pytest"]


def test_detect_repo_context_no_test_command_when_nothing_discoverable(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    (repo / "readme.txt").write_text("hi", encoding="utf-8")

    ctx = detect_repo_context(repo)

    assert ctx.test_command is None


def test_disposable_worktree_never_touches_real_tree_until_applied_for_real(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _init_git_repo(repo)
    real_file_before = (repo / "app.py").read_text(encoding="utf-8")

    with disposable_worktree(repo, is_git_repo=True) as wt:
        assert wt != repo
        assert (wt / "app.py").exists()

        patch = make_patch(repo, "app.py", "def add(a, b):\n    return a + b\n")
        apply_patch(patch, wt)

        assert (wt / "app.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"
        assert (repo / "app.py").read_text(encoding="utf-8") == real_file_before  # untouched

    assert not wt.exists()  # cleaned up after the context exits

    # only now, after "verification passed" in the caller's workflow, apply for real
    apply_patch(patch, repo)
    assert (repo / "app.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"


def test_disposable_worktree_plain_copy_for_non_git_repo(tmp_path):
    repo = tmp_path / "plain"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")

    with disposable_worktree(repo, is_git_repo=False) as wt:
        assert wt != repo
        assert (wt / "app.py").read_text(encoding="utf-8") == "x = 1\n"
        (wt / "app.py").write_text("x = 2\n", encoding="utf-8")
        assert (repo / "app.py").read_text(encoding="utf-8") == "x = 1\n"

    assert not wt.exists()


def test_make_patch_diff_text_reflects_the_change(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")

    patch = make_patch(repo, "app.py", "x = 2\n")

    assert "-x = 1" in patch.diff_text
    assert "+x = 2" in patch.diff_text


def test_make_patch_new_file_has_no_old_content(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()

    patch = make_patch(repo, "new_file.py", "print('hi')\n")

    assert patch.old_content is None
    assert "+print('hi')" in patch.diff_text


def test_snapshot_repo_files_includes_source_and_skips_noise(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "image.png").write_bytes(b"\x89PNG")
    git_dir = repo / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]\n", encoding="utf-8")

    snapshot = snapshot_repo_files(repo)

    assert "```py:app.py" in snapshot
    assert "x = 1" in snapshot
    assert "image.png" not in snapshot
    assert ".git" not in snapshot


def test_snapshot_repo_files_respects_char_budget(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / "big.py").write_text("x = 1\n" * 1000, encoding="utf-8")

    snapshot = snapshot_repo_files(repo, max_total_chars=50)

    assert snapshot == ""

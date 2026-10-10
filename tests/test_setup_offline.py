import platform
import tarfile
import zipfile

from typer.testing import CliRunner

from dwarv.cli import _extract_archive, _flatten_single_subdir, app

runner = CliRunner()


# --- _flatten_single_subdir / _extract_archive: real bug found live on
# macOS -- llama.cpp's .tar.gz releases (confirmed for macOS and Linux,
# unlike the flat Windows .zip) wrap every file in one top-level
# directory, so llama-server ended up one level deeper than every
# exe_path calculation expects, and setup-offline reported it "still
# missing" even though extraction succeeded. ---


def test_flatten_single_subdir_moves_nested_contents_up(tmp_path):
    wrapper = tmp_path / "llama-b11516"
    wrapper.mkdir()
    (wrapper / "llama-server").write_bytes(b"x")
    (wrapper / "LICENSE").write_bytes(b"x")

    _flatten_single_subdir(tmp_path)

    assert (tmp_path / "llama-server").exists()
    assert (tmp_path / "LICENSE").exists()
    assert not wrapper.exists()


def test_flatten_single_subdir_leaves_already_flat_dir_alone(tmp_path):
    (tmp_path / "llama-server").write_bytes(b"x")
    (tmp_path / "LICENSE").write_bytes(b"x")

    _flatten_single_subdir(tmp_path)

    assert (tmp_path / "llama-server").exists()
    assert (tmp_path / "LICENSE").exists()


def test_flatten_single_subdir_leaves_multiple_top_level_entries_alone(tmp_path):
    (tmp_path / "some_dir").mkdir()
    (tmp_path / "some_dir" / "inner").write_bytes(b"x")
    (tmp_path / "a_loose_file").write_bytes(b"x")

    _flatten_single_subdir(tmp_path)

    assert (tmp_path / "some_dir" / "inner").exists()  # untouched
    assert (tmp_path / "a_loose_file").exists()


def test_extract_archive_tar_gz_with_wrapper_directory_flattens_correctly(tmp_path):
    """Real reproduction of the exact llama.cpp release archive shape
    (one top-level "llama-bNNNNN/" directory wrapping everything) --
    confirmed via the actual b11516 macOS/Linux assets, not assumed."""
    archive_path = tmp_path / "llama-release.tar.gz"
    src = tmp_path / "to_archive" / "llama-b11516"
    src.mkdir(parents=True)
    (src / "llama-server").write_bytes(b"fake binary")
    (src / "LICENSE").write_bytes(b"license text")
    with tarfile.open(archive_path, "w:gz") as tf:
        tf.add(src, arcname="llama-b11516")

    dest_dir = tmp_path / "extracted"
    _extract_archive(archive_path, dest_dir, "tar.gz")

    assert (dest_dir / "llama-server").read_bytes() == b"fake binary"
    assert not (dest_dir / "llama-b11516").exists()


def test_extract_archive_zip_flat_layout_is_unaffected(tmp_path):
    """The Windows .zip releases are already flat -- confirms the
    flatten step is a correct no-op for that layout, not just untested."""
    archive_path = tmp_path / "llama-release.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("llama-server.exe", "fake binary")
        zf.writestr("LICENSE", "license text")

    dest_dir = tmp_path / "extracted"
    _extract_archive(archive_path, dest_dir, "zip")

    assert (dest_dir / "llama-server.exe").read_bytes() == b"fake binary"


def _fake_config(model_entries: list[dict]) -> dict:
    key = f"{platform.system()},{platform.machine()}"
    return {
        "llama_cpp": {
            "release_tag": "test",
            "release_url_base": "https://example.invalid/release",
            "assets": {key: ["llama-server-test.zip", "zip", "llama-server-test-exe"]},
        },
        "models": model_entries,
    }


def test_setup_offline_downloads_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("DWARV_CACHE_DIR", str(tmp_path))
    config = _fake_config(
        [
            {
                "id": "small",
                "hf_repo": "org/repo",
                "filename": "small.gguf",
                "quant": "Q4_K_M",
                "file_size_bytes": None,
            }
        ]
    )
    monkeypatch.setattr("dwarv.cli.load_models_config", lambda *a, **k: config)

    calls = []

    def fake_download(url, dest, label):
        calls.append((url, str(dest), label))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")

    def fake_extract(archive_path, dest_dir, archive_type):
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "llama-server-test-exe").write_bytes(b"x")

    monkeypatch.setattr("dwarv.cli._download", fake_download)
    monkeypatch.setattr("dwarv.cli._extract_archive", fake_extract)

    result = runner.invoke(app, ["setup-offline"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 2  # llama-server archive + the one model
    assert (tmp_path / "llama.cpp" / "llama-server-test-exe").exists()
    assert (tmp_path / "models" / "small.gguf").exists()


def test_setup_offline_skips_when_already_present(tmp_path, monkeypatch):
    monkeypatch.setenv("DWARV_CACHE_DIR", str(tmp_path))
    config = _fake_config(
        [
            {
                "id": "small",
                "hf_repo": "org/repo",
                "filename": "small.gguf",
                "quant": "Q4_K_M",
                "file_size_bytes": 1,
            }
        ]
    )
    monkeypatch.setattr("dwarv.cli.load_models_config", lambda *a, **k: config)

    exe_dir = tmp_path / "llama.cpp"
    exe_dir.mkdir(parents=True)
    (exe_dir / "llama-server-test-exe").write_bytes(b"x")
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True)
    (models_dir / "small.gguf").write_bytes(b"x")  # 1 byte, matches file_size_bytes=1

    calls = []
    monkeypatch.setattr("dwarv.cli._download", lambda *a, **k: calls.append(a))
    monkeypatch.setattr("dwarv.cli._extract_archive", lambda *a, **k: calls.append(a))

    result = runner.invoke(app, ["setup-offline"])

    assert result.exit_code == 0, result.output
    assert calls == []


def test_setup_offline_unknown_platform_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("DWARV_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(
        "dwarv.cli.load_models_config", lambda *a, **k: {"llama_cpp": {"assets": {}}, "models": []}
    )

    result = runner.invoke(app, ["setup-offline"])

    assert result.exit_code == 1

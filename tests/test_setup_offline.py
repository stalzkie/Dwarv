import platform

from typer.testing import CliRunner

from dwarv.cli import app

runner = CliRunner()


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

import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import httpx
import psutil
import typer
from rich.console import Console

from dwarv.models.suite import load_models_config, resolve_model_paths

app = typer.Typer(name="dwarv", help="A local, conversational coding assistant.")
console = Console()


def _gpu_summary() -> str:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return "none detected"
    try:
        out = subprocess.run(
            [nvidia_smi, "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        line = out.stdout.strip().splitlines()
        return line[0] if line else "nvidia-smi present, no GPU reported"
    except Exception as exc:
        return f"nvidia-smi present but failed: {exc}"


def _llama_server_version() -> str:
    llama_server = _llama_server_path()
    if not llama_server.exists():
        return "not found (run `dwarv setup-offline`)"
    try:
        out = subprocess.run(
            [str(llama_server), "--version"], capture_output=True, text=True, timeout=5
        )
        return (out.stdout or out.stderr).strip() or "found, version unknown"
    except Exception as exc:
        return f"found but failed to run: {exc}"


def _docker_available() -> bool:
    docker = shutil.which("docker")
    if not docker:
        return False
    try:
        out = subprocess.run([docker, "info"], capture_output=True, text=True, timeout=5)
        return out.returncode == 0
    except Exception:
        return False


def _sandbox_tier() -> tuple[int, str]:
    """Pick the verifier sandbox tier per DWARV_PLAN.md section 2.3."""
    if _docker_available():
        return 1, "Docker (--network none) -- strongest, cross-platform"
    system = platform.system()
    if system in ("Linux", "Darwin"):
        has_unshare = system == "Linux" and shutil.which("unshare") is not None
        detail = (
            "resource.setrlimit + unshare -n"
            if has_unshare
            else "resource.setrlimit only (no unshare on this OS)"
        )
        return 2, detail
    return 3, "Windows Job Object + timeout -- reduced isolation, no rlimit/unshare equivalent"


def _cache_dir() -> Path:
    """Default ./cache inside the project; DWARV_CACHE_DIR overrides it (e.g. for a
    disk-constrained dev machine that needs the ~15GB model suite on another drive)."""
    override = os.environ.get("DWARV_CACHE_DIR")
    return Path(override) if override else Path.cwd() / "cache"


def _llama_server_asset_key() -> str:
    return f"{platform.system()},{platform.machine()}"


def _llama_server_exe_name(config: dict) -> str | None:
    assets = config.get("llama_cpp", {}).get("assets", {})
    entry = assets.get(_llama_server_asset_key())
    return entry[2] if entry else None


def _llama_server_path() -> Path:
    config = load_models_config()
    exe_name = _llama_server_exe_name(config) or "llama-server"
    return _cache_dir() / "llama.cpp" / exe_name


def _download(url: str, dest: Path, label: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        written = 0
        next_milestone = 10
        with open(tmp, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                f.write(chunk)
                written += len(chunk)
                if total and (written / total) * 100 >= next_milestone:
                    console.print(f"  {label}: {next_milestone}% ({written / (1024**2):.0f}MB)")
                    next_milestone += 10
    tmp.replace(dest)
    console.print(f"  {label}: done ({dest.stat().st_size / (1024**2):.0f}MB)")


def _extract_archive(archive_path: Path, dest_dir: Path, archive_type: str) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    if archive_type == "zip":
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(dest_dir)
    elif archive_type == "tar.gz":
        with tarfile.open(archive_path) as tf:
            tf.extractall(dest_dir)
    else:
        raise ValueError(f"unknown archive type {archive_type!r}")


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Running `dwarv` with no subcommand starts a chat session in the current directory."""
    if ctx.invoked_subcommand is None:
        chat()


@app.command()
def chat() -> None:
    """Start a conversational coding session in the current directory. (not yet implemented)"""
    console.print("[yellow]dwarv chat is not implemented yet (Step 5/6).[/yellow]")


@app.command()
def doctor() -> None:
    """Print OS, CPU, RAM, GPU, Python, llama-server, and sandbox-tier info."""
    vm = psutil.virtual_memory()
    tier, tier_detail = _sandbox_tier()
    console.print("[bold]dwarv doctor[/bold]")
    console.print(f"OS: {platform.platform()}")
    console.print(
        f"CPU count: {psutil.cpu_count(logical=True)} logical / {psutil.cpu_count(logical=False)} physical"
    )
    console.print(
        f"RAM: {vm.total / (1024**3):.1f} GB total, {vm.available / (1024**3):.1f} GB available"
    )
    console.print(f"GPU: {_gpu_summary()}")
    console.print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    console.print(f"llama-server: {_llama_server_version()}")
    console.print(f"Cache dir: {_cache_dir()}")
    console.print(f"Docker: {'available' if _docker_available() else 'not available'}")
    console.print(f"Sandbox tier: {tier} ({tier_detail})")


@app.command(name="setup-offline")
def setup_offline() -> None:
    """Download llama-server and the 3 bundled Qwen2.5-Coder models into the cache dir."""
    cache_dir = _cache_dir()
    config = load_models_config()
    console.print(f"[bold]dwarv setup-offline[/bold] -> {cache_dir}")

    llama_cpp_cfg = config.get("llama_cpp", {})
    asset_key = _llama_server_asset_key()
    asset_entry = llama_cpp_cfg.get("assets", {}).get(asset_key)
    if asset_entry is None:
        console.print(f"[red]No llama-server asset configured for {asset_key}.[/red]")
        console.print(
            "Add an entry to configs/models.yaml's llama_cpp.assets, or install llama-server manually."
        )
        raise typer.Exit(code=1)
    asset_name, archive_type, exe_name = asset_entry
    llama_dir = cache_dir / "llama.cpp"
    exe_path = llama_dir / exe_name
    if exe_path.exists():
        console.print(f"llama-server already present at {exe_path}, skipping download.")
    else:
        url = f"{llama_cpp_cfg['release_url_base']}/{asset_name}"
        archive_path = cache_dir / asset_name
        console.print(
            f"Downloading llama-server ({llama_cpp_cfg.get('release_tag', '?')}) from {url}"
        )
        _download(url, archive_path, "llama-server")
        console.print(f"Extracting to {llama_dir}")
        _extract_archive(archive_path, llama_dir, archive_type)
        archive_path.unlink(missing_ok=True)
        if not exe_path.exists():
            console.print(f"[red]Extraction finished but {exe_path} is still missing.[/red]")
            raise typer.Exit(code=1)

    models_dir = cache_dir / "models"
    for entry in config.get("models", []):
        dest = models_dir / entry["filename"]
        expected_size = entry.get("file_size_bytes")
        if dest.exists() and (expected_size is None or dest.stat().st_size == expected_size):
            console.print(f"{entry['id']}: already present at {dest}, skipping download.")
            continue
        url = f"https://huggingface.co/{entry['hf_repo']}/resolve/main/{entry['filename']}"
        console.print(f"Downloading {entry['id']} ({entry['quant']}) from {url}")
        _download(url, dest, entry["id"])

    console.print(
        "[green]Setup complete.[/green] Set DWARV_CACHE_DIR to reuse this cache from another shell."
    )


@app.command(name="check-offline")
def check_offline() -> None:
    """Load the small model, generate once, and verify no non-local network connections
    were opened by the server process -- the automatable half of the offline proof."""
    tier, tier_detail = _sandbox_tier()
    console.print(f"[bold]dwarv check-offline[/bold] (sandbox tier {tier}: {tier_detail})")

    config = load_models_config()
    cache_dir = _cache_dir()
    model_paths = resolve_model_paths(config, cache_dir)
    small_path = model_paths.get("small")
    llama_server = _llama_server_path()

    if not small_path or not Path(small_path).exists() or not llama_server.exists():
        console.print(
            "[red]Missing llama-server or the small model -- run `dwarv setup-offline` first.[/red]"
        )
        raise typer.Exit(code=1)

    from dwarv.runtime.base import GenParams
    from dwarv.runtime.llamacpp import LlamaCppRuntime

    runtime = LlamaCppRuntime(str(llama_server), {"small": small_path})
    try:
        runtime.load("small", ctx_size=2048)
        pid = runtime.pid()
        proc = psutil.Process(pid)
        result = runtime.generate(
            messages=[{"role": "user", "content": "Say hello in one word."}],
            params=GenParams(max_tokens=16),
        )
        remote_connections = [
            c
            for c in proc.net_connections(kind="inet")
            if c.raddr and c.raddr.ip not in ("127.0.0.1", "::1")
        ]
    finally:
        runtime.unload()

    if remote_connections:
        console.print(f"[red]llama-server opened non-local connections: {remote_connections}[/red]")
        raise typer.Exit(code=1)
    console.print(
        f"[green]OK[/green] -- generated {len(result.text)} chars, zero non-local connections observed."
    )


@app.command()
def eval() -> None:
    """Run the internal eval harness (developer tool; never the end-user surface). (not yet implemented)"""
    console.print("[yellow]dwarv eval is not implemented yet (Step 9, internal only).[/yellow]")


@app.command()
def gui() -> None:
    """Start the optional, read-only session-transparency panel. (not yet implemented)"""
    console.print("[yellow]dwarv gui is not implemented yet (Step 10A, stretch goal).[/yellow]")


if __name__ == "__main__":
    app()

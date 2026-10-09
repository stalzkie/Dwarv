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

from dwarv.models.suite import (
    DEFAULT_CTX_SIZE,
    DEFAULT_SAFETY_MARGIN,
    find_best_quant_for_tier,
    find_quant_entry,
    load_models_config,
    resolve_model_paths,
    save_quant_choices,
)
from dwarv.verify.sandbox import docker_available as _docker_available
from dwarv.verify.sandbox import sandbox_tier as _sandbox_tier

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
    """Start a conversational coding session in the current directory."""
    llama_server = _llama_server_path()
    cache_dir = _cache_dir()
    if not llama_server.exists():
        console.print(
            "[yellow]No bundled models found yet -- `setup-offline` is a one-time "
            "download (~15GB: llama-server + all 3 models, or less if your hardware "
            "needs a compressed quant for a tier).[/yellow]"
        )
        try:
            run_now = typer.confirm("Run `dwarv setup-offline` now?", default=True)
        except Exception:
            run_now = False  # non-interactive/no stdin -- don't silently start a big download
        if not run_now:
            console.print(
                "[red]Skipped -- run `dwarv setup-offline` yourself, then `dwarv` again.[/red]"
            )
            raise typer.Exit(code=1)
        setup_offline()
        if not llama_server.exists():
            console.print(
                "[red]Setup finished but llama-server is still missing -- see the output above.[/red]"
            )
            raise typer.Exit(code=1)

    from dwarv.agent.session import run_repl
    from dwarv.runtime.base import RuntimeCrashed

    try:
        run_repl(str(llama_server), str(cache_dir))
    except RuntimeCrashed as exc:
        console.print(f"[red]llama-server crashed: {exc}[/red]")
        raise typer.Exit(code=1) from exc


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

    # DWARV_PLAN.md section 11.6: hardware-aware per-tier quant choice.
    # Each tier downloads its default quant unless that doesn't fit this
    # machine's real available RAM, in which case the highest-quality
    # bartowski I-quant that does fit is downloaded instead -- so a
    # low-spec machine never wastes bandwidth/disk on a file it can't
    # actually load. budget_mb mirrors choose_model()'s own
    # safety-margined calculation so "what setup-offline downloads" and
    # "what choose_model() would pick" agree.
    vm = psutil.virtual_memory()
    sys_available_mb = vm.available / (1024 * 1024)
    budget_mb = sys_available_mb * (1 - DEFAULT_SAFETY_MARGIN)

    models_dir = cache_dir / "models"
    quant_choices: dict[str, str] = {}
    for entry in config.get("models", []):
        level, fits = find_best_quant_for_tier(entry, budget_mb, DEFAULT_CTX_SIZE)
        if level is not None:
            quant_choices[entry["id"]] = level
            source = find_quant_entry(entry, level)
            if not fits:
                console.print(
                    f"[yellow]{entry['id']}: even the most compressed quant ({level}) may not "
                    "comfortably fit this machine -- downloading it anyway as the best available "
                    "option.[/yellow]"
                )
        else:
            source = entry
        dest = models_dir / source["filename"]
        expected_size = source.get("file_size_bytes")
        if dest.exists() and (expected_size is None or dest.stat().st_size == expected_size):
            console.print(f"{entry['id']}: already present at {dest}, skipping download.")
            continue
        url = f"https://huggingface.co/{source['hf_repo']}/resolve/main/{source['filename']}"
        label = f"{entry['id']} ({level or entry['quant']})"
        console.print(f"Downloading {label} from {url}")
        _download(url, dest, entry["id"])

    save_quant_choices(cache_dir, quant_choices)

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
def eval(
    run_id: str = typer.Option(..., help="Unique id for this run; re-using one resumes it."),  # noqa: B008
    systems: list[str] = typer.Option(  # noqa: B008
        ["fixed", "retry", "retry_escalate", "dwarv"], help="Systems to run."
    ),
    profiles: list[str] = typer.Option(  # noqa: B008
        ["static_loose"], help="Budget profiles (configs/budgets.yaml)."
    ),
    n_tasks: int = typer.Option(5, help="How many tasks from eval_tasks/subset_v1.json to run."),  # noqa: B008
    seeds: list[int] = typer.Option([1], help="Seeds (repeat runs for variance)."),  # noqa: B008
) -> None:
    """Run the internal eval harness -- a developer tool, never the end-user
    surface. Writes to eval_results/<run_id>/ and prints the summary table."""
    import json

    from dwarv.eval.analyze import analyze_run
    from dwarv.eval.harness import EVAL_RESULTS_DIR, run_harness

    subset_path = Path(__file__).resolve().parents[2] / "eval_tasks" / "subset_v1.json"
    with open(subset_path, encoding="utf-8") as f:
        subset = json.load(f)
    task_ids = subset["task_ids"][:n_tasks]

    cache_dir = str(_cache_dir())
    console.print(
        f"[bold]dwarv eval[/bold] run_id={run_id} tasks={len(task_ids)} systems={systems} profiles={profiles}"
    )
    jsonl_path = run_harness(run_id, systems, profiles, task_ids, seeds, cache_dir)
    analyze_run(jsonl_path.parent)
    console.print(f"[green]Done.[/green] Results in {EVAL_RESULTS_DIR / run_id}")
    console.print((jsonl_path.parent / "summary.md").read_text(encoding="utf-8"))


@app.command(hidden=True)
def gui(
    port: int = typer.Option(8765, help="Local port to bind (127.0.0.1 only)."),  # noqa: B008
) -> None:
    """Start the optional, read-only session-transparency panel (Step 10A).
    Localhost-only; reads whichever session is currently active in this
    cache dir, if any -- never starts or controls a session itself."""
    try:
        import uvicorn
    except ImportError as exc:
        console.print('[red]gui extras not installed -- run `pip install -e ".[gui]"`.[/red]')
        raise typer.Exit(code=1) from exc

    from dwarv.gui.server import create_app

    log_dir = _cache_dir() / "sessions"
    url = f"http://127.0.0.1:{port}"
    console.print(f"[bold]dwarv gui[/bold] -> {url}  (read-only, localhost-only; Ctrl+C to stop)")
    uvicorn.run(create_app(log_dir), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    app()

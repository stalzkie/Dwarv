import platform
import shutil
import subprocess
import sys

import psutil
import typer
from rich.console import Console

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
    llama_server = shutil.which("llama-server")
    if not llama_server:
        return "not found on PATH"
    try:
        out = subprocess.run([llama_server, "--version"], capture_output=True, text=True, timeout=5)
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
    console.print(f"Docker: {'available' if _docker_available() else 'not available'}")
    console.print(f"Sandbox tier: {tier} ({tier_detail})")


@app.command(name="setup-offline")
def setup_offline() -> None:
    """Download the 3 bundled Qwen2.5-Coder models into ./cache/. (not yet implemented)"""
    console.print("[yellow]dwarv setup-offline is not implemented yet (Step 1).[/yellow]")


@app.command(name="check-offline")
def check_offline() -> None:
    """Verify offline operation using the detected sandbox tier. (not yet implemented)"""
    tier, tier_detail = _sandbox_tier()
    console.print(
        f"[yellow]dwarv check-offline is not implemented yet (Step 1). Would use sandbox tier {tier} ({tier_detail}).[/yellow]"
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

"""ClaudeSync CLI — bi-directional Claude Code context sync over SSH."""
from __future__ import annotations

import platform
import subprocess
import tempfile
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from typing import Optional

from .backup import list_backups, restore_backup
from .config import Config, Remote, SyncSettings, load_config, save_config
from .conflicts import ConflictReport, FileState, apply_conflict_resolutions, detect_conflicts
from .engine import Engine, SyncSummary
from .filters import PROJECT_SYNC_ITEMS, get_global_include_paths, get_global_sync_includes
from .manifest import (
    build_local_manifest,
    get_remote_manifest,
    update_manifest_for_remote,
)
from .sanitize import merge_pulled_claude_json, write_sanitized_temp

app = typer.Typer(
    name="claudesync",
    help="Bi-directional Claude Code context sync over SSH.",
    no_args_is_help=True,
)
remote_app = typer.Typer(help="Manage remote machines.", no_args_is_help=True)
project_app = typer.Typer(help="Manage registered projects.", no_args_is_help=True)
backup_app = typer.Typer(help="Manage conflict backups.", no_args_is_help=True)
autostart_app = typer.Typer(help="Manage auto-sync on macOS.", no_args_is_help=True)

app.add_typer(remote_app, name="remote")
app.add_typer(project_app, name="project")
app.add_typer(backup_app, name="backup")
app.add_typer(autostart_app, name="autostart")

console = Console()


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@app.command()
def init() -> None:
    """Interactive setup wizard — configure a remote and save config."""
    console.print("[bold cyan]ClaudeSync Setup Wizard[/bold cyan]\n")

    config = load_config()

    remote_name = typer.prompt("Remote name", default="home")
    host = typer.prompt("Remote host (IP or hostname)")
    user = typer.prompt("Remote user", default=Path.home().name)
    ssh_key = typer.prompt("SSH key path", default="~/.ssh/id_ed25519")
    remote_home = typer.prompt("Remote home directory", default=f"/home/{user}")

    remote = Remote(host=host, user=user, ssh_key=ssh_key, remote_home=remote_home)
    config.remotes[remote_name] = remote

    console.print(f"\n[dim]Testing SSH connection to {remote.address}...[/dim]")
    engine = Engine(remote)
    if engine.check_connection():
        console.print("[green]✓ SSH connection successful[/green]")
    else:
        console.print("[yellow]⚠ Could not connect — check host/user/key and try again[/yellow]")
        console.print("[dim]Config saved anyway. Run 'claudesync remote add' to reconfigure.[/dim]")

    save_config(config)
    console.print("\n[green]Config saved to ~/.claudesync/config.toml[/green]")
    console.print(f"  Remote '{remote_name}' → {remote.address}")
    console.print("\nNext steps:")
    console.print("  claudesync project add ~/Projects/MyProject")
    console.print(f"  claudesync push {remote_name}")


# ---------------------------------------------------------------------------
# pair
# ---------------------------------------------------------------------------

@app.command()
def pair(
    name: str = typer.Option(..., "--name", "-n", help="Name for this remote (e.g. 'studio')"),
    address: str = typer.Option(..., "--address", "-a", help="user@host of the remote machine"),
    key: str = typer.Option("~/.ssh/id_ed25519", "--key", "-k", help="SSH private key path"),
    no_push: bool = typer.Option(False, "--no-push", help="Skip initial push (just configure)"),
) -> None:
    """Pair with another machine — add remote, verify SSH, and do initial push."""
    if "@" not in address:
        console.print("[red]Error: address must be in user@host format[/red]")
        raise typer.Exit(1)

    user, host = address.split("@", 1)

    console.print(f"[bold cyan]Pairing with [white]{name}[/white] ({address})[/bold cyan]\n")

    # Step 1: test connection and detect remote home
    console.print("[dim]Testing SSH connection...[/dim]")
    tmp_remote = Remote(host=host, user=user, ssh_key=key, remote_home=f"/home/{user}")
    engine = Engine(tmp_remote)

    if not engine.check_connection():
        console.print(f"[red]✗ Cannot connect to {address}.[/red]")
        console.print("[dim]Check: is the host reachable? Is the SSH key correct?[/dim]")
        raise typer.Exit(1)

    console.print("[green]✓ SSH connection OK[/green]")

    # Auto-detect remote home via `echo $HOME`
    res = subprocess.run(
        engine._ssh_cmd() + ["echo $HOME"],
        capture_output=True, text=True, timeout=10,
    )
    remote_home = res.stdout.strip() if res.returncode == 0 and res.stdout.strip() else f"/home/{user}"
    console.print(f"[dim]Remote home: {remote_home}[/dim]")

    # Step 2: save config
    config = load_config()
    remote = Remote(host=host, user=user, ssh_key=key, remote_home=remote_home)
    config.remotes[name] = remote
    save_config(config)
    console.print(f"[green]✓ Remote '{name}' saved to config[/green]")

    if no_push:
        console.print("\n[bold]Pairing complete (no push).[/bold]")
        console.print(f"Run [cyan]claudesync push {name}[/cyan] when ready.")
        return

    # Step 3: initial push
    console.print(f"\n[dim]Running initial push to {name}...[/dim]")
    engine = Engine(remote)
    project_paths = config.project_paths()

    with console.status("Building manifests..."):
        local_manifest, remote_manifest = _build_manifests(engine, project_paths, include_history=config.sync.include_history)

    with console.status("Detecting conflicts..."):
        last_sync = get_remote_manifest(name)
        report = detect_conflicts(name, local_manifest, remote_manifest, last_sync)
        report = apply_conflict_resolutions(report, config.sync.backup_count)

    _print_conflict_report(report)

    with console.status("Pushing files..."):
        sanitized_tmp = write_sanitized_temp()
        try:
            summary = engine.push(project_paths, sanitized_claude_json=sanitized_tmp, include_history=config.sync.include_history)
        finally:
            sanitized_tmp.unlink(missing_ok=True)

    if not summary.errors:
        update_manifest_for_remote(name, local_manifest)

    _print_summary(summary, "push")

    if not summary.errors:
        console.print(f"\n[bold green]✓ Paired with {name}![/bold green]")
        console.print(f"\nOn [bold]{name}[/bold], run:")
        console.print(f"  [cyan]claudesync remote add here {_get_local_address()} --remote-home {Path.home()}[/cyan]")
        console.print("  [cyan]claudesync pull here[/cyan]")
    else:
        console.print("\n[yellow]⚠ Pairing incomplete — push encountered errors. Fix the issues above and retry.[/yellow]")


def _get_local_address() -> str:
    """Best-effort: return current machine's user@hostname."""
    import socket
    hostname = socket.gethostname()
    return f"{Path.home().name}@{hostname}"


# ---------------------------------------------------------------------------
# remote
# ---------------------------------------------------------------------------

@remote_app.command("add")
def remote_add(
    name: str = typer.Argument(..., help="Remote name (e.g. 'home', 'work')"),
    address: str = typer.Argument(..., help="user@host"),
    ssh_key: str = typer.Option("~/.ssh/id_ed25519", "--key", "-k"),
    remote_home: str = typer.Option("", "--remote-home", "-r", help="Remote home dir (auto-detected if empty)"),
) -> None:
    """Add a remote machine."""
    if "@" not in address:
        console.print("[red]Error: address must be in user@host format[/red]")
        raise typer.Exit(1)

    user, host = address.split("@", 1)
    if not remote_home:
        remote_home = f"/home/{user}"

    config = load_config()
    config.remotes[name] = Remote(host=host, user=user, ssh_key=ssh_key, remote_home=remote_home)
    save_config(config)
    console.print(f"[green]Added remote '{name}' → {address}[/green]")


@remote_app.command("list")
def remote_list() -> None:
    """List configured remotes."""
    config = load_config()
    if not config.remotes:
        console.print("[dim]No remotes configured. Run: claudesync remote add <name> <user@host>[/dim]")
        return

    table = Table(title="Remotes", show_header=True)
    table.add_column("Name", style="cyan")
    table.add_column("Host")
    table.add_column("User")
    table.add_column("SSH Key")
    table.add_column("Remote Home")

    for name, r in config.remotes.items():
        table.add_row(name, r.host, r.user, r.ssh_key, r.remote_home)

    console.print(table)


# ---------------------------------------------------------------------------
# project
# ---------------------------------------------------------------------------

@project_app.command("add")
def project_add(
    path: str = typer.Argument(..., help="Project directory path"),
) -> None:
    """Register a project directory for syncing."""
    resolved = str(Path(path).expanduser().resolve())
    config = load_config()
    if resolved not in config.projects:
        config.projects.append(resolved)
        save_config(config)
        console.print(f"[green]Added project: {resolved}[/green]")
    else:
        console.print(f"[dim]Project already registered: {resolved}[/dim]")


@project_app.command("list")
def project_list() -> None:
    """List registered projects."""
    config = load_config()
    if not config.projects:
        console.print("[dim]No projects registered. Run: claudesync project add <path>[/dim]")
        return

    table = Table(title="Projects", show_header=True)
    table.add_column("Path", style="cyan")
    table.add_column("Exists", justify="center")

    for p in config.projects:
        exists = "✓" if Path(p).exists() else "✗"
        table.add_row(p, exists)

    console.print(table)


# ---------------------------------------------------------------------------
# push / pull
# ---------------------------------------------------------------------------

@app.command()
def push(
    remote_name: str = typer.Argument(..., help="Remote name to push to"),
) -> None:
    """Sync local → remote."""
    config, remote, engine, project_paths = _setup_sync(remote_name)

    console.print(f"[bold]Pushing to [cyan]{remote_name}[/cyan] ({remote.address})...[/bold]")
    _require_connection(engine, remote)

    with console.status("Building manifests..."):
        local_manifest, remote_manifest = _build_manifests(engine, project_paths, include_history=config.sync.include_history)

    with console.status("Detecting conflicts..."):
        last_sync = get_remote_manifest(remote_name)
        report = detect_conflicts(remote_name, local_manifest, remote_manifest, last_sync)
        report = apply_conflict_resolutions(report, config.sync.backup_count)

    _print_conflict_report(report)

    with console.status("Syncing files..."):
        sanitized_tmp = write_sanitized_temp()
        try:
            summary = engine.push(project_paths, sanitized_claude_json=sanitized_tmp, include_history=config.sync.include_history)
        finally:
            sanitized_tmp.unlink(missing_ok=True)

    if not summary.errors:
        update_manifest_for_remote(remote_name, local_manifest)

    _print_summary(summary, "push")


@app.command()
def pull(
    remote_name: str = typer.Argument(..., help="Remote name to pull from"),
) -> None:
    """Sync remote → local."""
    config, remote, engine, project_paths = _setup_sync(remote_name)

    console.print(f"[bold]Pulling from [cyan]{remote_name}[/cyan] ({remote.address})...[/bold]")
    _require_connection(engine, remote)

    with console.status("Building manifests..."):
        local_manifest, remote_manifest = _build_manifests(engine, project_paths, include_history=config.sync.include_history)

    with console.status("Detecting conflicts..."):
        last_sync = get_remote_manifest(remote_name)
        report = detect_conflicts(remote_name, local_manifest, remote_manifest, last_sync)
        report = apply_conflict_resolutions(report, config.sync.backup_count)

    _print_conflict_report(report)

    with console.status("Syncing files..."):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            tmp_claude_json = Path(tf.name)

        try:
            summary = engine.pull(project_paths, temp_claude_json_dest=tmp_claude_json, include_history=config.sync.include_history)
            if tmp_claude_json.exists() and tmp_claude_json.stat().st_size > 0:
                merge_pulled_claude_json(tmp_claude_json)
        finally:
            tmp_claude_json.unlink(missing_ok=True)

    if not summary.errors:
        update_manifest_for_remote(remote_name, build_local_manifest(_collect_local_files(project_paths, include_history=config.sync.include_history)))

    _print_summary(summary, "pull")


# ---------------------------------------------------------------------------
# status / diff
# ---------------------------------------------------------------------------

@app.command()
def status(
    remote_name: str = typer.Argument(..., help="Remote name"),
) -> None:
    """Show what would change (dry-run)."""
    _, remote, engine, project_paths = _setup_sync(remote_name)

    console.print(f"[bold]Status vs [cyan]{remote_name}[/cyan] ({remote.address})[/bold]\n")
    _require_connection(engine, remote)

    output = engine.dry_run(project_paths, direction="push")
    console.print(output if output.strip() else "[dim]Everything up to date[/dim]")


@app.command()
def diff(
    remote_name: str = typer.Argument(..., help="Remote name"),
) -> None:
    """Show file-level diffs between local and remote."""
    _, remote, engine, project_paths = _setup_sync(remote_name)

    console.print(f"[bold]Diff vs [cyan]{remote_name}[/cyan] ({remote.address})[/bold]\n")
    _require_connection(engine, remote)

    with console.status("Building manifests..."):
        local_manifest, remote_manifest = _build_manifests(engine, project_paths)

    last_sync = get_remote_manifest(remote_name)
    report = detect_conflicts(remote_name, local_manifest, remote_manifest, last_sync)

    if not report.modified_files:
        console.print("[dim]No differences found[/dim]")
        return

    table = Table(title="File Differences", show_header=True)
    table.add_column("Status", style="bold")
    table.add_column("File")

    state_styles = {
        FileState.MODIFIED_LOCAL: "[yellow]local[/yellow]",
        FileState.MODIFIED_REMOTE: "[blue]remote[/blue]",
        FileState.CONFLICT: "[red]conflict[/red]",
        FileState.LOCAL_ONLY: "[green]local-only[/green]",
        FileState.REMOTE_ONLY: "[cyan]remote-only[/cyan]",
    }

    for fc in report.modified_files:
        label = state_styles.get(fc.state, "[dim]?[/dim]")
        table.add_row(label, fc.path)

    console.print(table)


# ---------------------------------------------------------------------------
# backup
# ---------------------------------------------------------------------------

@backup_app.command("list")
def backup_list() -> None:
    """List conflict backups."""
    entries = list_backups()
    if not entries:
        console.print("[dim]No backups found[/dim]")
        return

    table = Table(title="Backups", show_header=True)
    table.add_column("ID", style="cyan")
    table.add_column("Created")
    table.add_column("Original Path")

    for entry in entries:
        table.add_row(entry.backup_id, entry.created_at, entry.original_path)

    console.print(table)


@backup_app.command("restore")
def backup_restore(
    backup_id: str = typer.Argument(..., help="Backup ID to restore"),
    original_path: Optional[str] = typer.Argument(None, help="Specific file to restore (or all files if omitted)"),
) -> None:
    """Restore a backed-up file."""
    try:
        restored = restore_backup(backup_id, original_path)
        for path in restored:
            console.print(f"[green]Restored:[/green] {path}")
    except (ValueError, FileNotFoundError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _setup_sync(remote_name: str) -> tuple[Config, Remote, Engine, list[Path]]:
    """Load config, resolve remote, create engine, and get project paths."""
    config = load_config()
    remote = config.get_remote(remote_name)
    engine = Engine(remote)
    return config, remote, engine, config.project_paths()


def _require_connection(engine: Engine, remote: Remote) -> None:
    """Check SSH connection; print error and exit if unreachable."""
    with console.status("Testing SSH connection..."):
        if not engine.check_connection():
            console.print(f"[red]Cannot connect to {remote.address}. Check SSH config.[/red]")
            raise typer.Exit(1)


def _collect_local_files(project_paths: list[Path], include_history: bool = False) -> list[str]:
    """Collect all syncable local file paths (global + per-project)."""
    local_files = get_global_include_paths(include_history=include_history)
    for proj in project_paths:
        for item in PROJECT_SYNC_ITEMS:
            p = proj / item
            if p.exists():
                local_files.append(str(p))
    return local_files


def _build_manifests(
    engine: Engine,
    project_paths: list[Path],
    include_history: bool = False,
) -> tuple[dict, dict]:
    """Build local and remote file manifests including all project files."""
    local_manifest = build_local_manifest(_collect_local_files(project_paths, include_history=include_history))

    # Translate local paths to remote paths before querying remote hashes
    remote_paths = [_local_to_remote_path(p, project_paths, engine.remote) for p in local_manifest]
    raw_remote = engine.get_remote_file_hashes(remote_paths)

    # Re-key the remote result back to local path keys
    remote_manifest: dict = {}
    for local_path in local_manifest:
        remote_path = _local_to_remote_path(local_path, project_paths, engine.remote)
        if remote_path in raw_remote:
            remote_manifest[local_path] = raw_remote[remote_path]

    return local_manifest, remote_manifest


def _local_to_remote_path(local_path: str, project_paths: list[Path], remote: Remote) -> str:
    """Translate a local absolute path to its remote equivalent."""
    lp = Path(local_path)
    for proj in project_paths:
        try:
            rel = lp.relative_to(proj)
            return f"{remote.remote_home}/{proj.name}/{rel.as_posix()}"
        except ValueError:
            continue
    try:
        rel = lp.relative_to(Path.home())
        return f"{remote.remote_home}/{rel.as_posix()}"
    except ValueError:
        return local_path


def _human_age(mtime: float | None) -> str:
    """Return human-readable age like '2 hours ago', '3 days ago'."""
    import time
    if mtime is None:
        return "unknown time"
    delta = time.time() - mtime
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta / 60)} min ago"
    if delta < 86400:
        hours = int(delta / 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(delta / 86400)
    return f"{days} day{'s' if days != 1 else ''} ago"


def _print_conflict_report(report: ConflictReport) -> None:
    conflicts = [c for c in report.conflicts if c.state == FileState.CONFLICT]
    if not conflicts:
        return

    console.print(f"\n[yellow]⚠ {len(conflicts)} conflict(s) resolved (last-write-wins):[/yellow]")
    for fc in conflicts:
        name = Path(fc.path).name
        local_age = _human_age(fc.local_mtime)
        remote_age = _human_age(fc.remote_mtime)

        local_label = "[red]← LOST[/red]" if fc.winner == "remote" else "[green]← WON[/green]"
        remote_label = "[green]← WON[/green]" if fc.winner == "remote" else "[red]← LOST[/red]"

        console.print(f"  [bold]{name}[/bold]")
        console.print(f"    local:  modified {local_age}  {local_label}")
        console.print(f"    remote: modified {remote_age}  {remote_label}")
        if fc.backup_path:
            console.print(f"    backup: [dim]{fc.backup_path}[/dim]")
    console.print()


def _print_summary(summary: SyncSummary, direction: str) -> None:
    arrow = "→" if direction == "push" else "←"
    n = summary.files_transferred
    errors = summary.errors

    console.print(f"\n[bold green]✓ {direction.capitalize()} complete[/bold green]")
    console.print(f"  {arrow} {n} file(s) transferred")

    if errors:
        console.print("\n[yellow]Warnings:[/yellow]")
        for err in errors:
            console.print(f"  {err.strip()}")


# ---------------------------------------------------------------------------
# autostart (macOS launchd)
# ---------------------------------------------------------------------------

@autostart_app.command("enable")
def autostart_enable(
    remote_name: str = typer.Argument(..., help="Remote name to auto-pull from"),
    interval: int = typer.Option(300, "--interval", "-i", help="Sync interval in seconds (default 300 = 5 min)"),
) -> None:
    """Install a launchd job to auto-pull from a remote every N seconds."""
    if platform.system() != "Darwin":
        console.print("[red]autostart is macOS-only (uses launchd).[/red]")
        raise typer.Exit(1)

    if interval <= 0:
        console.print(f"[red]Error: --interval must be a positive integer, got {interval}.[/red]")
        raise typer.Exit(1)

    config = load_config()
    try:
        config.get_remote(remote_name)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e

    import shutil
    claudesync_path = shutil.which("claudesync") or "claudesync"

    from .autostart import install_plist
    try:
        plist_path = install_plist(remote_name, claudesync_path, interval)
        console.print(f"[green]✓ Auto-sync enabled for '{remote_name}'[/green]")
        console.print(f"  Interval: every {interval}s")
        console.print(f"  Plist:    {plist_path}")
        console.print(f"  Logs:     ~/.claudesync/logs/autosync-{remote_name}.log")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Failed to load plist: {e}[/red]")
        raise typer.Exit(1) from e


@autostart_app.command("disable")
def autostart_disable(
    remote_name: str = typer.Argument(..., help="Remote name to stop auto-syncing"),
) -> None:
    """Remove the launchd auto-sync job for a remote."""
    if platform.system() != "Darwin":
        console.print("[red]autostart is macOS-only (uses launchd).[/red]")
        raise typer.Exit(1)

    from .autostart import uninstall_plist
    try:
        if uninstall_plist(remote_name):
            console.print(f"[green]✓ Auto-sync disabled for '{remote_name}'[/green]")
        else:
            console.print(f"[dim]No auto-sync job found for '{remote_name}'[/dim]")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Failed to unload plist: {e}[/red]")
        raise typer.Exit(1) from e

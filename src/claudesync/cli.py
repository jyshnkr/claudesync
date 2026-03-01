"""ClaudeSync CLI — bi-directional Claude Code context sync over SSH."""
from __future__ import annotations

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
from .filters import PROJECT_SYNC_ITEMS, get_global_include_paths
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

app.add_typer(remote_app, name="remote")
app.add_typer(project_app, name="project")
app.add_typer(backup_app, name="backup")

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
    console.print(f"\n[green]Config saved to ~/.claudesync/config.toml[/green]")
    console.print(f"  Remote '{remote_name}' → {remote.address}")
    console.print("\nNext steps:")
    console.print(f"  claudesync project add ~/Projects/MyProject")
    console.print(f"  claudesync push {remote_name}")


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
        local_manifest, remote_manifest = _build_manifests(engine, project_paths)

    with console.status("Detecting conflicts..."):
        last_sync = get_remote_manifest(remote_name)
        report = detect_conflicts(remote_name, local_manifest, remote_manifest, last_sync)
        report = apply_conflict_resolutions(report, config.sync.backup_count)

    _print_conflict_report(report)

    with console.status("Syncing files..."):
        sanitized_tmp = write_sanitized_temp()
        try:
            summary = engine.push(project_paths, sanitized_claude_json=sanitized_tmp)
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
        local_manifest, remote_manifest = _build_manifests(engine, project_paths)

    with console.status("Detecting conflicts..."):
        last_sync = get_remote_manifest(remote_name)
        report = detect_conflicts(remote_name, local_manifest, remote_manifest, last_sync)
        report = apply_conflict_resolutions(report, config.sync.backup_count)

    _print_conflict_report(report)

    with console.status("Syncing files..."):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            tmp_claude_json = Path(tf.name)

        try:
            summary = engine.pull(project_paths, temp_claude_json_dest=tmp_claude_json)
            if tmp_claude_json.exists() and tmp_claude_json.stat().st_size > 0:
                merge_pulled_claude_json(tmp_claude_json)
        finally:
            tmp_claude_json.unlink(missing_ok=True)

    if not summary.errors:
        update_manifest_for_remote(remote_name, local_manifest)

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
        raise typer.Exit(1)


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


def _build_manifests(
    engine: Engine,
    project_paths: list[Path],
) -> tuple[dict, dict]:
    """Build local and remote file manifests including all project files."""
    local_files = get_global_include_paths()
    for proj in project_paths:
        for item in PROJECT_SYNC_ITEMS:
            p = proj / item
            if p.exists():
                local_files.append(str(p))
    local_manifest = build_local_manifest(local_files)
    remote_manifest = engine.get_remote_file_hashes(list(local_manifest.keys()))
    return local_manifest, remote_manifest


def _print_conflict_report(report: ConflictReport) -> None:
    conflicts = [c for c in report.conflicts if c.state == FileState.CONFLICT]
    if not conflicts:
        return

    console.print(f"\n[yellow]⚠ {len(conflicts)} conflict(s) resolved (last-write-wins):[/yellow]")
    for fc in conflicts:
        winner_label = f"[green]{fc.winner}[/green]" if fc.winner else "?"
        backup_note = f" (backup: {fc.backup_path})" if fc.backup_path else ""
        console.print(f"  {fc.path} → winner: {winner_label}{backup_note}")
    console.print()


def _print_summary(summary: SyncSummary, direction: str) -> None:
    arrow = "→" if direction == "push" else "←"
    n = summary.files_transferred
    errors = summary.errors

    console.print(f"\n[bold green]✓ {direction.capitalize()} complete[/bold green]")
    console.print(f"  {arrow} {n} file(s) transferred")

    if errors:
        console.print(f"\n[yellow]Warnings:[/yellow]")
        for err in errors:
            console.print(f"  {err.strip()}")

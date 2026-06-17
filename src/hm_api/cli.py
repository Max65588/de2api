"""CLI entry point."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from .accounts import export_accounts, import_accounts
from .config import APP_CONFIG_FILE, DEFAULT_HOST, DEFAULT_PORT
from .login import is_logged_in, load_session, login
from .server import run_server
from .settings import create_default_app_config, load_app_config

app = typer.Typer(help="hm-api - DevEco Code OpenAI-compatible API CLI")
console = Console()


def _empty_as_none(value: str | None) -> str | None:
    return value if value else None


@app.command("login")
def login_cmd(
    proxy: Optional[str] = typer.Option(
        "", "--proxy", "-p", help="HTTP/HTTPS proxy for login requests"
    ),
    no_browser: Optional[bool] = typer.Option(
        False, "--no-browser", help="Print login URL instead of opening browser"
    ),
    timeout: Optional[int] = typer.Option(
        600, "--timeout", "-t", help="Login callback timeout in seconds", min=60
    ),
) -> None:
    """Login with Huawei DevEco account via browser OAuth."""
    proxy = _empty_as_none(proxy)
    if no_browser:
        console.print("[bold blue]Use the URL below to login:[/bold blue]")
    else:
        console.print("[bold blue]Opening browser for DevEco login...[/bold blue]")
    result = asyncio.run(login(proxy=proxy, no_browser=no_browser or False, timeout=timeout or 600))
    if result.success and result.user_info:
        console.print(
            f"[bold green]Login successful![/bold green] Welcome, {result.user_info.user_name}"
        )
    elif result.cancelled:
        console.print("[yellow]Login cancelled by user.[/yellow]")
        raise typer.Exit(1)
    elif result.unsupported_region:
        console.print("[red]Only China site accounts are currently supported.[/red]")
        raise typer.Exit(1)
    else:
        console.print(f"[red]Login failed:[/red] {result.error}")
        raise typer.Exit(1)


@app.command("init-config")
def init_config_cmd(
    force: Optional[bool] = typer.Option(
        False, "--force", help="Overwrite existing cred/config.json"
    ),
) -> None:
    """Create a local runtime config with admin password and API keys."""
    try:
        data = create_default_app_config(force=bool(force))
    except FileExistsError:
        console.print(f"[yellow]{APP_CONFIG_FILE} already exists. Use --force to overwrite.[/yellow]")
        raise typer.Exit(1)

    console.print(f"[green]Created {APP_CONFIG_FILE}[/green]")
    console.print(f"[blue]Admin password:[/blue] {data['admin_password']}")
    console.print(f"[blue]API key ({data['api_keys'][0]['name']}):[/blue] {data['api_keys'][0]['key']}")


@app.command("export-accounts")
def export_accounts_cmd(
    output: Path = typer.Argument(..., help="Path to write the accounts export JSON"),
) -> None:
    """Export logged-in DevEco accounts for migration to another machine."""
    count = export_accounts(output)
    console.print(f"[green]Exported {count} account(s) to {output}[/green]")
    console.print("[yellow]This file contains login credentials. Transfer and store it securely.[/yellow]")


@app.command("import-accounts")
def import_accounts_cmd(
    input_file: Path = typer.Argument(..., help="Path to an accounts export JSON"),
) -> None:
    """Import DevEco accounts exported by export-accounts."""
    count = import_accounts(input_file)
    console.print(f"[green]Imported {count} account(s) from {input_file}[/green]")


@app.command()
def serve(
    host: Optional[str] = typer.Option(
        DEFAULT_HOST, "--host", "-h", help="Host to bind the server"
    ),
    port: Optional[int] = typer.Option(
        DEFAULT_PORT, "--port", "-p", help="Port to bind the server", min=1, max=65535
    ),
    proxy: Optional[str] = typer.Option(
        "", "--proxy", help="HTTP/HTTPS proxy for upstream requests"
    ),
    key: Optional[str] = typer.Option(
        None, "--key", "-k", help="Compatibility override: add one CLI API key named cli"
    ),
    admin_password: Optional[str] = typer.Option(
        None,
        "--admin-password",
        help="Compatibility override for the web admin UI password",
    ),
) -> None:
    """Start the OpenAI-compatible API server."""
    admin_password = _empty_as_none(admin_password)
    app_config = load_app_config()
    effective_admin_password = admin_password or app_config.admin_password

    if not is_logged_in() and not effective_admin_password:
        console.print(
            "[yellow]Not logged in. Run [bold]hm-api login[/bold] first, or run [bold]hm-api init-config[/bold] then add users in the web UI.[/yellow]"
        )
        raise typer.Exit(1)

    import asyncio

    session = asyncio.run(load_session())
    if session:
        console.print(
            f"[blue]Logged in as {session.get('user_name') or session.get('user_id')}.[/blue]"
        )

    proxy = _empty_as_none(proxy)
    key = _empty_as_none(key)

    console.print(f"[bold green]Starting server at http://{host}:{port}[/bold green]")
    console.print(f"[bold blue]Admin UI: http://{host}:{port}/admin[/bold blue]")
    config_key_count = len([item for item in app_config.api_keys if item.enabled])
    if key or config_key_count:
        console.print(f"[dim]API key authentication enabled ({config_key_count + (1 if key else 0)} key(s)).[/dim]")
    else:
        console.print("[dim]API key authentication disabled.[/dim]")
    if effective_admin_password:
        console.print("[dim]Admin UI authentication enabled.[/dim]")
    else:
        console.print("[dim]Admin UI disabled; pass --admin-password to enable it.[/dim]")
    if proxy:
        console.print(f"[dim]Upstream proxy: {proxy}[/dim]")

    run_server(
        host=host or DEFAULT_HOST,
        port=port or DEFAULT_PORT,
        api_key=key,
        proxy=proxy,
        admin_password=admin_password,
    )


@app.command()
def status() -> None:
    """Show current login status."""
    import asyncio

    if is_logged_in():
        session = asyncio.run(load_session())
        if session:
            console.print(
                f"[green]Logged in[/green] as {session.get('user_name') or session.get('user_id')}"
            )
        else:
            console.print("[green]Logged in[/green]")
    else:
        console.print("[red]Not logged in[/red]")


if __name__ == "__main__":
    app()

"""Click entrypoint for the Costwise CLI."""

from __future__ import annotations

import click

from costwise.config.loader import load_config
from costwise.tracking.store import TrackingStore


@click.group()
@click.version_option(package_name="costwise")
def cli() -> None:
    """Costwise — intelligent cost optimization for AI coding agents."""


@cli.command()
@click.option("--host", default=None, help="Proxy listen host")
@click.option("--port", default=None, type=int, help="Proxy listen port")
@click.option("--upstream", default=None, help="Upstream LLM API base URL")
def proxy(host: str | None, port: int | None, upstream: str | None) -> None:
    """Start the Costwise proxy server."""
    import uvicorn

    from costwise.config.loader import load_config
    from costwise.proxy.server import create_app
    from costwise.tracking.store import TrackingStore

    config = load_config()

    if host:
        config.proxy.host = host
    if port:
        config.proxy.port = port
    if upstream:
        config.proxy.upstream = upstream

    store = TrackingStore(config.tracking.db_path)
    app = create_app(config, store)

    click.echo(f"Costwise proxy starting on {config.proxy.host}:{config.proxy.port}")
    if config.proxy.vertex.enabled:
        click.echo(f"Vertex AI: project={config.proxy.vertex.project_id} region={config.proxy.vertex.region}")
    else:
        click.echo(f"Upstream: {config.proxy.upstream}")
    click.echo(f"Tracking DB: {config.tracking.db_path}")

    uvicorn.run(app, host=config.proxy.host, port=config.proxy.port, log_level="info")


@cli.command()
@click.option("--host", default="127.0.0.1", help="Dashboard listen host")
@click.option("--port", default=8789, type=int, help="Dashboard listen port")
def dashboard(host: str, port: int) -> None:
    """Start the Costwise cost dashboard."""
    import uvicorn

    from costwise.dashboard.app import create_dashboard_app

    config = load_config()
    store = TrackingStore(config.tracking.db_path)
    app = create_dashboard_app(config, store)

    click.echo(f"Costwise dashboard starting on {host}:{port}")
    click.echo(f"Tracking DB: {config.tracking.db_path}")

    uvicorn.run(app, host=host, port=port, log_level="info")


@cli.command("mcp")
def mcp_cmd() -> None:
    """Start the Costwise MCP server (stdio)."""
    from costwise.mcp.server import mcp as mcp_server

    mcp_server.run()


# Register subcommands
from costwise.cli.gain_cmd import gain  # noqa: E402
from costwise.cli.doctor_cmd import doctor  # noqa: E402
from costwise.cli.wrap_cmd import wrap  # noqa: E402
from costwise.cli.setup_cmd import setup  # noqa: E402

cli.add_command(gain)
cli.add_command(doctor)
cli.add_command(wrap)
cli.add_command(setup)

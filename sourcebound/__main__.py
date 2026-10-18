"""Run the Sourcebound server: ``python -m sourcebound``."""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from .config import ConfigError, load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sourcebound", description="Run the Sourcebound research agent web app.")
    parser.add_argument("--host", help="interface to bind (default from SOURCEBOUND_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, help="port to listen on (default from SOURCEBOUND_PORT or 8000)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    from .app import create_app

    app = create_app(settings)
    mode = f"AI writing available ({settings.llm.model})" if settings.llm.enabled else "extractive mode (no AI provider configured)"
    host = args.host or settings.host
    port = args.port or settings.port
    logging.getLogger("sourcebound").info("Sourcebound starting on http://%s:%d — %s", host, port, mode)
    uvicorn.run(app, host=host, port=port, log_level="info", proxy_headers=False, server_header=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

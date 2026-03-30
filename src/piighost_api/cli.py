"""CLI entrypoint for the piighost-api server."""

import argparse
import logging
import sys

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="piighost-api",
        description="PII anonymization inference server powered by piighost.",
    )
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser(
        "serve",
        help="Start the API server.",
    )
    serve.add_argument(
        "pipeline",
        help="Pipeline import path in module:variable format (e.g. myconfig:pipeline).",
    )
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host (default: 127.0.0.1).",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Bind port (default: 8000).",
    )
    serve.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Log level (default: info).",
    )

    args = parser.parse_args()

    if args.command != "serve":
        parser.print_help()
        sys.exit(1)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # Store pipeline path for the app factory to pick up.
    # Litestar's app factory pattern: we pass a callable string to uvicorn,
    # but since we need the pipeline arg, we use an env var.
    import os
    os.environ["PIIGHOST_PIPELINE"] = args.pipeline

    uvicorn.run(
        "piighost_api.cli:_create_app",
        factory=True,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )


def _create_app():
    """App factory called by uvicorn."""
    import os
    from piighost_api.app import create_app

    pipeline_path = os.environ["PIIGHOST_PIPELINE"]
    return create_app(pipeline_path)

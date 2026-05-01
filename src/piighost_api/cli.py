"""Typer CLI entrypoint for piighost-api.

Subcommands:
* ``serve``: start the API server (existing behaviour).
* ``dataset extract``: pull HITL / model traces from Langfuse into a
  JSONL training dataset.
* ``dataset metrics``: compute per-label P/R/F1 on a JSONL dataset.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import typer
import uvicorn

from piighost_api.dataset.extract import (
    ANONYMIZE_TRACE_NAME,
    HITL_TRACE_NAME,
    DatasetMode,
    record_from_trace,
)
from piighost_api.dataset.metrics import (
    MatchMode,
    OutputFormat,
    SourceFilter,
    aggregate,
    render_csv,
    render_json,
    render_table,
)


app = typer.Typer(no_args_is_help=True, add_completion=False)
dataset_app = typer.Typer(no_args_is_help=True, help="HITL dataset operations.")
app.add_typer(dataset_app, name="dataset")


@app.command()
def serve(
    pipeline: str = typer.Argument(
        ..., help="Pipeline import path in module:variable format."
    ),
    host: str = typer.Option("127.0.0.1", help="Bind host."),
    port: int = typer.Option(8000, help="Bind port."),
    log_level: str = typer.Option(
        "info", help="Log level (debug | info | warning | error)."
    ),
) -> None:
    """Start the API server."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    os.environ["PIIGHOST_PIPELINE"] = pipeline
    uvicorn.run(
        "piighost_api.cli:_create_app",
        factory=True,
        host=host,
        port=port,
        log_level=log_level,
    )


@dataset_app.command("extract")
def dataset_extract(
    output: Path = typer.Option(..., "--output", "-o", help="JSONL file to write."),
    since: datetime | None = typer.Option(
        None, "--since", help="Skip traces older than this ISO timestamp."
    ),
    until: datetime | None = typer.Option(
        None, "--until", help="Skip traces newer than this ISO timestamp."
    ),
    mode: DatasetMode = typer.Option(DatasetMode.all, "--mode"),
    limit: int | None = typer.Option(None, "--limit", help="Stop after N records."),
) -> None:
    """Extract HITL + non-HITL traces from Langfuse into a JSONL dataset."""
    # Auto-load a .env from the current working directory if python-dotenv
    # is available. Operators typically keep their LANGFUSE_* keys in the
    # repo's .env (the dev-mode workflow already sources it for `make
    # docker-up*`), and a CLI run from the repo root should pick that up
    # without forcing the user to `set -a && source .env && set +a`.
    try:
        from dotenv import load_dotenv  # pyrefly: ignore[missing-import]

        load_dotenv()
    except ModuleNotFoundError:
        pass

    if not os.getenv("LANGFUSE_PUBLIC_KEY") or not os.getenv("LANGFUSE_SECRET_KEY"):
        typer.echo(
            "Missing LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY. "
            "Set them with the keys from your Langfuse project settings.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Lazy import so the whole `--help` command tree works when the
    # `dataset` extra is not installed. The friendly error below
    # converts the ModuleNotFoundError into the same shape as the
    # missing-credentials branch above, instead of a raw traceback.
    try:
        from langfuse import Langfuse  # pyrefly: ignore[missing-import]
    except ModuleNotFoundError:
        typer.echo(
            "The 'langfuse' package is required for `dataset extract`. "
            "Install it with `uv sync --extra dataset` "
            "(or `pip install langfuse>=3.0`).",
            err=True,
        )
        raise typer.Exit(code=1)

    client = Langfuse()
    fetch_kwargs: dict = {}
    if since is not None:
        fetch_kwargs["from_timestamp"] = since
    if until is not None:
        fetch_kwargs["to_timestamp"] = until

    names_to_fetch = []
    if mode in (DatasetMode.all, DatasetMode.hitl):
        names_to_fetch.append(HITL_TRACE_NAME)
    if mode in (DatasetMode.all, DatasetMode.model_only):
        names_to_fetch.append(ANONYMIZE_TRACE_NAME)

    written = 0
    skipped = 0
    with output.open("w", encoding="utf-8") as fh:
        for name in names_to_fetch:
            traces = client.api.trace.list(name=name, **fetch_kwargs).data
            for trace in traces:
                if name == ANONYMIZE_TRACE_NAME:
                    full = client.api.trace.get(trace.id)
                    record = record_from_trace(full, mode=mode)
                else:
                    record = record_from_trace(trace, mode=mode)
                if record is None:
                    skipped += 1
                    continue
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                written += 1
                if limit is not None and written >= limit:
                    break
            if limit is not None and written >= limit:
                break

    typer.echo(f"Wrote {written} records to {output} ({skipped} skipped).")


@dataset_app.command("metrics")
def dataset_metrics(
    input: Path = typer.Option(..., "--input", "-i", help="JSONL file to read."),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write the report to this path instead of stdout."
    ),
    output_format: OutputFormat = typer.Option(OutputFormat.table, "--output-format"),
    match_mode: MatchMode = typer.Option(MatchMode.strict, "--match-mode"),
    iou_threshold: float = typer.Option(
        0.5, "--iou-threshold", help="Span-IoU floor in lenient mode."
    ),
    source: SourceFilter = typer.Option(
        SourceFilter.all, "--source", help="Restrict aggregation to one record source."
    ),
) -> None:
    """Compute per-label P/R/F1 from a HITL JSONL dataset."""
    records = []
    with input.open(encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped:
                records.append(json.loads(stripped))

    per_label, confusion = aggregate(
        records,
        match_mode=match_mode,
        source_filter=source,
        iou_threshold=iou_threshold,
    )

    if output_format is OutputFormat.table:
        out = render_table(per_label, confusion)
    elif output_format is OutputFormat.csv:
        out = render_csv(per_label)
    else:
        out = render_json(per_label, confusion)

    if output is None:
        typer.echo(out)
    else:
        output.write_text(out, encoding="utf-8")


def _create_app():
    """App factory called by uvicorn (preserved from the argparse CLI)."""
    from piighost_api.app import create_app

    pipeline_path = os.environ["PIIGHOST_PIPELINE"]
    return create_app(pipeline_path)


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()

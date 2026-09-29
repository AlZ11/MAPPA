"""Logging: JSON lines to a file for machines, readable lines on the console for people.

Why structured logs: a 1,000-app run produces thousands of events. Keyed fields
(``app_id``, ``status``, ``attempt``, ``run_id``) can be filtered and counted afterwards
with ``jq`` or DuckDB (e.g. the "top 10 failure reasons" in the coverage report)
instead of grepping prose.

Console output goes to stderr so stdout stays clean for command results.

Tracebacks never include local variables. structlog and rich include them by default,
and locals can hold the AndroZoo key or megabytes of raw HTML.
"""

import logging
import sys
from pathlib import Path

import structlog
from structlog.tracebacks import ExceptionDictTransformer
from structlog.typing import Processor

# The handlers this module installed, so reconfiguring replaces only ours and leaves
# other handlers on the root logger (e.g. pytest's capture) alone.
_installed: list[logging.Handler] = []


def configure_logging(log_file: Path | None = None, *, verbose: bool = False) -> None:
    """Send log events to the console (INFO, or DEBUG if ``verbose``) and, if
    ``log_file`` is given, append every event at DEBUG as JSON lines."""
    remove_handlers()

    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    colors = sys.stderr.isatty()
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(
                    colors=colors,
                    exception_formatter=structlog.dev.RichTracebackFormatter(
                        color_system="truecolor" if colors else None, show_locals=False
                    ),
                ),
            ],
        )
    )
    handlers: list[logging.Handler] = [console]

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        to_file = logging.FileHandler(log_file, encoding="utf-8")
        to_file.setLevel(logging.DEBUG)
        to_file.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                foreign_pre_chain=shared,
                processors=[
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.ExceptionRenderer(
                        ExceptionDictTransformer(show_locals=False)
                    ),
                    structlog.processors.JSONRenderer(),
                ],
            )
        )
        handlers.append(to_file)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in handlers:
        root.addHandler(handler)
        _installed.append(handler)


def remove_handlers() -> None:
    """Detach and close the handlers installed by ``configure_logging``."""
    root = logging.getLogger()
    while _installed:
        handler = _installed.pop()
        root.removeHandler(handler)
        handler.close()


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.stdlib.get_logger(name)

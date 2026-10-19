"""Logging configuration shared by the supervisor, the collector (worker) and the dashboard.

Each process writes its own rotating file in logs/ (2 MB x 5 files), so two processes never
rotate the same file. Important events are ALSO stored in the SQLite `events` table so the
dashboard can show them.
"""
import logging
import logging.handlers
import sys

FORMAT = "%(asctime)s %(levelname)-8s %(name)-10s %(message)s"


def configure(name, settings, level=logging.INFO, console=True):
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    formatter = logging.Formatter(FORMAT)

    file_handler = logging.handlers.RotatingFileHandler(
        settings.log_dir / f"{name}.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if console and sys.stderr is not None:          # pythonw.exe on Windows has no stderr
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        root.addHandler(stream)

    logging.getLogger("waitress.queue").setLevel(logging.ERROR)
    logger = logging.getLogger(name)

    def log_uncaught(exc_type, exc, tb):
        logger.critical("APPLICATION_ERROR uncaught exception", exc_info=(exc_type, exc, tb))
    sys.excepthook = log_uncaught
    return logger

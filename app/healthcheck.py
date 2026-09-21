"""Docker HEALTHCHECK: sale 1 si logs/.heartbeat es viejo o no existe."""

from __future__ import annotations

import os
import sys

from app.watchdog import heartbeat_is_fresh


def main() -> int:
    log_dir = (os.environ.get("LOG_DIR") or "logs").strip() or "logs"
    raw = (os.environ.get("WATCHDOG_SECONDS") or "180").strip() or "180"
    try:
        watchdog_seconds = float(raw)
    except ValueError:
        watchdog_seconds = 180.0
    return 0 if heartbeat_is_fresh(log_dir, watchdog_seconds) else 1


if __name__ == "__main__":
    sys.exit(main())

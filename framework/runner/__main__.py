"""framework/runner/__main__.py

Enables ``python -m framework.runner`` — dispatches to the scheduled_run
CLI. Equivalent to ``python -m framework.runner.scheduled_run``.
"""

from framework.runner.scheduled_run import main

if __name__ == "__main__":
    raise SystemExit(main())
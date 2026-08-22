"""framework/runner/__init__.py

Public surface for the scheduled-run module (T6: CAP-7).
``framework.runner.scheduled_run`` is the standalone process the 15:00
cron (or systemd timer) invokes. It must never be imported from inside
the Streamlit UI process — per scheduler.md §"Hard rule".

This ``__init__.py`` is intentionally empty (no imports): pulling
``AKShareAdapter`` into the UI process via ``from framework.runner import ...``
would defeat the runner-is-isolated guarantee.
"""
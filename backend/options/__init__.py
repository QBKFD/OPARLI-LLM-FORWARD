"""COMEX gold options (OG on GC futures) from IBKR, read-only.

backend.options.chain     chain discovery, batched live-then-frozen market data,
                          COMEX hours and CME holidays (shared with
                          tools/options_probe.py, which it was factored out of)
backend.options.store     the snapshot store: SQLite, its own file
backend.options.snapshot  the daily snapshot: python -m backend.options

Stdlib + ib_insync (imported only when a snapshot really connects). Imports
no other backend package."""

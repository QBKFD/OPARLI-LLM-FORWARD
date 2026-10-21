"""OPARLI's one importable package: the live scanner and everything it runs on.

backend.datafeed  IBKR -> Redis stream -> point-in-time SQLite store
backend.scoring   TAKE outcomes; backend.risk: brackets and sizing
backend.agents    agents v2 (LangGraph), not deployed

The scanner itself is still backend/deterministic_scanner.py (run as a script)
until it moves into backend.scanner. Nothing is imported here, so importing
one subpackage loads no other."""

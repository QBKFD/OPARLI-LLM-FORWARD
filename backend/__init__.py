"""OPARLI's one importable package: the live scanner and everything it runs on.

backend.scanner   the level scanner: python -m backend.scanner (replay, selftest, live)
backend.levels    the level engine, pure (build, touch)
backend.datafeed  IBKR -> Redis stream -> point-in-time SQLite store
backend.scoring   TAKE outcomes; backend.risk: brackets and sizing
backend.agents    agents v2 (LangGraph), not deployed

Nothing is imported here, so importing one subpackage loads no other."""

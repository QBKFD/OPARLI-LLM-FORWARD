"""Shared builders for the status board tests."""

from tools.status import config

NOW = 1792040400.0               # 2026-10-15 05:00:00 UTC, a Thursday (market open)


def thresholds():
    return config.load()


def facts(**over):
    """A healthy ibkr-mode deployment, as the watchdog reports it."""
    f = dict(now=str(int(NOW)), market_open="1", bar_source="ibkr", stream_expected="0",
             gateway_ok="1", gateway_fails="0", gateway_relogin_limit="0",
             scanner_state="running", scanner_health="healthy", scanner_unhealthy_checks="0",
             scanner_restarts_5min="0", scanner_last_bar_age_min="0", scanner_last_bar_ts="2026-10-15T04:59Z",
             scanner_last_alert_ts="2026-10-15T03:12:00Z",
             feed_state="missing", feed_restarts_5min="-1", redis_state="missing", redis_fails="0",
             recorder_state="missing", recorder_lag_min="-1", rejected_total="-1", rejected_last_growth_ts="0",
             journal_ok="1", journal_take="1", journal_skip="2", journal_missed="0", journal_undecided="0",
             journal_undecided_oldest_min="-1",
             backup_configured="1", backup_age_h="3", disk_pct="40", mem_avail_pct="55", swap_mb="0",
             agents_deployed="0")
    f.update({k: str(v) for k, v in over.items()})
    return f


def stream_facts(**over):
    """The stream profile running, everything healthy."""
    f = facts(bar_source="stream", stream_expected="1", feed_state="running", feed_restarts_5min="0",
              feed_last_bar_age_min="0", feed_last_bar_ts="2026-10-15T04:59Z", redis_state="running",
              recorder_state="running", recorder_lag_min="0", rejected_total="0")
    f.update({k: str(v) for k, v in over.items()})
    return f

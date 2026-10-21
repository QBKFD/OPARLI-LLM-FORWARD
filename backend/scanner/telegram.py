"""Telegram: the alert text, the reply format and its parser, the bot API
wrapper (sendMessage, getUpdates), and handling one reply into a decision."""
from __future__ import annotations

from typing import NamedTuple

import pandas as pd

from backend.scanner.journal.outcomes import take_problem
from backend.scanner.journal.writes import record_decision

# ── Telegram (stdlib only — message carries facts, never a recommendation) ──
def format_alert(a: dict) -> str:
    """EXACTLY the fields the spec allows: id, ts UTC, level type(s), level
    price, current price, session, confluence. No bias, no commentary."""
    return (f"{a['alert_id']}\n"
            f"{a['alert_ts_utc']} UTC\n"
            f"{a['session']}\n"
            f"{a['level_types']} @ {a['level_price']}\n"
            f"price {a['price_at_alert']}\n"
            f"confluence {a['confluence_count']}")


def parse_reply(text: str) -> dict | None:
    """Parse a human reply. Deterministic, no interpretation.
       '<id> TAKE long 4128 sl 4122 tp 4140 <reason>'  or  '<id> SKIP <reason>'."""
    tok = text.strip().split()
    if len(tok) < 2:
        return None
    alert_id, verb = tok[0], tok[1].upper()
    if verb == "SKIP":
        return dict(alert_id=alert_id, decision="SKIP", reasoning=" ".join(tok[2:]))
    if verb == "TAKE":
        d = dict(alert_id=alert_id, decision="TAKE", direction=None,
                 entry=None, stop=None, target=None, reasoning="")
        rest = tok[2:]
        if rest and rest[0].lower() in ("long", "short"):
            d["direction"] = rest[0].lower(); rest = rest[1:]
        if rest and _isnum(rest[0]):
            d["entry"] = float(rest[0]); rest = rest[1:]
        i = 0
        while i < len(rest):
            w = rest[i].lower()
            if w == "sl" and i + 1 < len(rest) and _isnum(rest[i + 1]):
                d["stop"] = float(rest[i + 1]); i += 2; continue
            if w == "tp" and i + 1 < len(rest) and _isnum(rest[i + 1]):
                d["target"] = float(rest[i + 1]); i += 2; continue
            break
        d["reasoning"] = " ".join(rest[i:])
        return d
    return None


def _isnum(s: str) -> bool:
    try:
        float(s); return True
    except ValueError:
        return False


class Telegram:
    """Thin sendMessage / getUpdates wrapper. Token + chat id from env."""
    def __init__(self):
        import os
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not self.token or not self.chat_id:
            raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the environment")
        self._base = f"https://api.telegram.org/bot{self.token}"

    def _get(self, method, params):
        import json
        import urllib.parse
        import urllib.request
        url = f"{self._base}/{method}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=70) as r:
            return json.load(r)

    def send(self, text: str):
        return self._get("sendMessage", {"chat_id": self.chat_id, "text": text})

    def poll(self, offset: int):
        """Long-poll replies. Returns (messages, next_offset): TelegramMessage
        for each text message from our chat, oldest first."""
        res = self._get("getUpdates", {"offset": offset, "timeout": 60})
        return parse_updates(res.get("result", []), self.chat_id, offset)


class TelegramMessage(NamedTuple):
    """One reply: Telegram's update id, the message's own date (UTC, whole
    seconds, set by Telegram when it was sent) and its text. A NamedTuple, not
    a dataclass: tests load this file by path without registering the module."""
    update_id: int
    date: pd.Timestamp
    text: str


def parse_updates(updates, chat_id, offset: int, log=print):
    """getUpdates results -> ([TelegramMessage], next_offset). Updates from
    other chats, non-text and edited messages are skipped but still move the
    offset; a message without a usable date is skipped with a log line, never
    given another time."""
    msgs, nxt = [], offset
    for u in updates:
        nxt = max(nxt, u["update_id"] + 1)
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != str(chat_id) or "text" not in m:
            continue
        date = m.get("date")
        if isinstance(date, bool) or not isinstance(date, int) or date <= 0:
            log(f"telegram update {u['update_id']}: no usable message date ({date!r}); skipped",
                flush=True)
            continue
        msgs.append(TelegramMessage(u["update_id"], pd.Timestamp(date, unit="s", tz="UTC"),
                                    m["text"]))
    return msgs, nxt


def _send_quietly(tg, text: str):
    """Confirmation messages are best-effort: the decision is already logged."""
    try:
        tg.send(text)
    except Exception as e:
        print(f"telegram send failed ({e!r})", flush=True)


def _handle_reply(con, lock, tg, msg: TelegramMessage, epoch: int, log=print):
    """One Telegram reply -> decision row (both bar sources). Only sent alerts
    can be decided (a missed_while_down id was never shown). An id that exists
    in several epochs (a switch day) means the alert of this process's epoch,
    else the newest one.

    The decision time is the message's date, never the time it was processed.
    An update already in the journal (redelivered after a restart) is skipped
    without a second confirmation. The confirmation says when a TAKE can't be
    scored, and when the alert already had a decision (the first one counts)."""
    r = parse_reply(msg.text)
    if not r:
        return
    with lock:
        row = con.execute("SELECT alert_ts_utc, epoch FROM alerts WHERE alert_id=? "
                          "AND status = 'sent' ORDER BY epoch = ? DESC, rowid DESC LIMIT 1",
                          (r["alert_id"], epoch)).fetchone()
        if not row:
            _send_quietly(tg, f"unknown alert_id {r['alert_id']}")
            return
        first = con.execute("SELECT decision FROM decisions WHERE alert_id=? AND epoch=? "
                            "ORDER BY rowid LIMIT 1", (r["alert_id"], row[1])).fetchone()
        if not record_decision(con, r["alert_id"], r["decision"], msg.date, row[0],
                               r.get("direction"), r.get("entry"), r.get("stop"), r.get("target"),
                               r.get("reasoning"), epoch=row[1], update_id=msg.update_id,
                               received_ts=pd.Timestamp.now(tz="UTC")):
            log(f"telegram update {msg.update_id} already journaled; skipped", flush=True)
            return
        notes = []
        if r["decision"] == "TAKE":
            problem = take_problem(r.get("direction"), r.get("entry"), r.get("stop"), r.get("target"))
            if problem:
                notes.append(f"outcome not scorable: {problem}")
        if first:
            notes.append(f"already decided {first[0]}; the first decision counts")
        _send_quietly(tg, f"logged {r['decision']} for {r['alert_id']}"
                      + (f" ({'; '.join(notes)})" if notes else ""))

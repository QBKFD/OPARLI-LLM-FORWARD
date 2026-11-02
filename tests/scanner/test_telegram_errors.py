"""F1: only transport errors are caught around Telegram calls. A transport
error (network, HTTP, a reply that isn't JSON) is logged and retried; any
other error, e.g. a bug in parsing the replies, propagates instead of being
logged as "telegram poll failed"."""
import http.client
import json
import socket
import urllib.error

import pytest

from backend.scanner.engine import _alert_sender
from backend.scanner.telegram import TRANSPORT_ERRORS, _send_quietly, poll_replies
from tests.scanner.scanner_helpers import Log

TRANSPORT = [urllib.error.URLError("no route"), ConnectionResetError("reset by peer"),
             socket.timeout("timed out"), TimeoutError("read timeout"),
             urllib.error.HTTPError("https://api.telegram.org", 502, "Bad Gateway", None, None),
             http.client.IncompleteRead(b"x"), http.client.RemoteDisconnected("closed"),
             json.JSONDecodeError("Expecting value", "<html>", 0)]


class FakeTg:
    chat_id = "42"

    def __init__(self, result=None, error=None):
        self.result, self.error, self.sent = result, error, []

    def fetch(self, offset):
        if self.error:
            raise self.error
        return self.result

    def send(self, text):
        if self.error:
            raise self.error
        self.sent.append(text)


def _update(uid, text="A1 SKIP x", chat="42", date=1760400000):
    return {"update_id": uid, "message": {"chat": {"id": chat}, "text": text, "date": date}}


@pytest.mark.parametrize("err", TRANSPORT, ids=lambda e: type(e).__name__)
def test_a_transport_error_is_logged_and_retried_with_the_same_offset(err):
    assert isinstance(err, TRANSPORT_ERRORS)
    log, slept = Log(), []
    msgs, offset = poll_replies(FakeTg(error=err), 7, sleep=slept.append, log=log)
    assert (msgs, offset) == ([], 7)                 # same offset: fetched again next round
    assert slept == [5] and log.having("telegram poll failed")


def test_replies_are_parsed_and_the_offset_moves():
    tg = FakeTg(result={"ok": True, "result": [_update(10), _update(11, chat="99")]})
    msgs, offset = poll_replies(tg, 7, sleep=pytest.fail, log=Log())
    assert [m.update_id for m in msgs] == [10] and offset == 12


@pytest.mark.parametrize("bad", [{"ok": True, "result": [{"message": {}}]},          # no update_id
                                 {"ok": True, "result": [_update(10) | {"message": "text"}]}],   # message not a dict
                         ids=["no-update-id", "message-not-a-dict"])
def test_a_parse_bug_surfaces_instead_of_being_logged_as_a_poll_failure(bad):
    log = Log()
    with pytest.raises((KeyError, AttributeError)):
        poll_replies(FakeTg(result=bad), 7, sleep=pytest.fail, log=log)
    assert not log.having("telegram poll failed")


def test_a_non_transport_error_in_the_fetch_surfaces():
    with pytest.raises(RuntimeError):
        poll_replies(FakeTg(error=RuntimeError("a bug")), 7, sleep=pytest.fail, log=Log())


@pytest.mark.parametrize("err", TRANSPORT, ids=lambda e: type(e).__name__)
def test_confirmations_and_alerts_survive_transport_errors(err, capsys):
    _send_quietly(FakeTg(error=err), "logged SKIP for A1")
    _alert_sender(FakeTg(error=err), dry=False)(dict(alert_id="A1", alert_ts_utc="2025-06-16 00:06:00",
                                                     session="ASIA", level_types="PDH", level_price=1.0,
                                                     price_at_alert=1.0, confluence_count=1))
    out = capsys.readouterr().out
    assert "telegram send failed (" in out and "telegram send failed for A1:" in out


def test_send_bugs_surface():
    with pytest.raises(TypeError):
        _send_quietly(FakeTg(error=TypeError("a bug")), "logged SKIP for A1")
    with pytest.raises(TypeError):
        _alert_sender(FakeTg(error=TypeError("a bug")), dry=False)(
            dict(alert_id="A1", alert_ts_utc="2025-06-16 00:06:00", session="ASIA", level_types="PDH",
                 level_price=1.0, price_at_alert=1.0, confluence_count=1))


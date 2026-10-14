"""
End-to-end integration test for the OPARLI agent pipeline.

THE CLAIM UNDER TEST
    One scan on fixed historical data produces one decision that REACHES the
    Risk Manager.

That is the minimum the architecture claims and, before the fixes this test was
written alongside, it was false for four independent reasons — every one of
which this test catches:

  1. Volume never voted but occupied a slot in the confluence denominator.
  2. Bollinger's band offset was applied as a fraction of price, so it never
     voted either. With 2 of 6 voters dead, confluence could not reach 4.
  3. TimeframeCascade gated on `confidence > 0.60` against a confidence ladder
     whose 3-indicator rung is exactly 0.60, so a 3-indicator signal was
     rejected by an off-by-epsilon comparison.
  4. Sentiment emitted BULLISH/BEARISH on a 0-100 scale into a consumer that
     scores LONG/SHORT on 0-1, contributing 0.0 while holding 15% of the weight.

plus the wiring defects: Scanner addressed its ANALYSIS_REQUEST to the Meta
agent (which does not handle that type) instead of to the analysts; Meta sent
TRADE_SIGNAL to a Risk Manager that only handles TRADE_REQUEST; the Sentiment
analyst was never instantiated although Meta blocks until all three analysts
report; and no agent was ever activated.

WHAT IS REAL HERE
    The MessageBus, the routing, every agent's process_message, the
    TimeframeCascade, ConfluenceChecker, MetaDecisionService, governance, and
    run_risk_validation are the production objects.

WHAT IS STUBBED, AND WHY
    - Scanner.technical_analyst.get_latest_analysis and
      Scanner.chart_generator.generate_charts. Both read Postgres directly with
      no as_of bound (a separate, known defect: the Scanner's Stage-1 data path
      has not been moved onto MarketDataProvider yet). Stubbing exactly these
      two seams keeps the test hermetic without hiding anything this test is
      about — Stage 1's quality logic itself runs for real, on the real cascade
      output for the fixture bar.
    - The Visual and Sentiment LLM clients. Deterministic canned responses;
      this test asserts plumbing, not model behaviour.

The price fixture is synthetic and seeded, so the test needs no database, no
network, and no untracked CSV: an uptrend that pulls back into support, leaving
price oversold and below the lower Bollinger band. That is a 4-indicator LONG
once Volume and Bollinger are fixed, and a 3-indicator (blocked) LONG before.
"""

import base64
import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
import pytz

from agents.base_agent import AgentType, Message, MessageType
from agents.meta_agent import MetaAgentNew
from agents.riskmanager_agent import RiskManagerAgentNew
from agents.scanner_agent import ScannerAgentNew
from agents.sentimentanalyst_agent import SentimentAnalystAgent
from agents.technicalanalyst_agent import TechnicalAnalystAgent
from agents.visual_analyst_agent import VisualAnalystAgent
from core.message_bus import MessageBus
from services.analysis.visual_analysis import VisualAnalysisService
from services.confluence_checker import ConfluenceChecker
from services.market_data_provider import HistoricalDataProvider
from services.technical_service import run_technical_analysis

SYMBOL = "XAUUSD"
FIXTURE_START = pytz.utc.localize(datetime(2023, 6, 1))

# Hourly shape: a long steady climb, then a sharp 7-hour pullback. Tuned against
# the real indicator code so the final bar sits oversold, below the lower band,
# and on 20-bar support. See the module docstring.
N_UP, UP_SLOPE = 113, 1.2
N_DOWN, DOWN_SLOPE = 7, 4.0
START_PRICE = 1900.0


# ---------------------------------------------------------------- fixture data


def _synthetic_1min() -> pd.DataFrame:
    """Deterministic 1-minute OHLCV frame realising the hourly shape above."""
    hourly = [START_PRICE]
    for _ in range(N_UP):
        hourly.append(hourly[-1] + UP_SLOPE)
    for _ in range(N_DOWN):
        hourly.append(hourly[-1] - DOWN_SLOPE)
    hourly = hourly[1:]

    rng = np.random.default_rng(7)
    closes = []
    prev = hourly[0] - UP_SLOPE
    for hour_close in hourly:
        step = (hour_close - prev) / 60.0
        path = prev + step * np.arange(1, 61) + rng.normal(0, 0.05, 60)
        path[-1] = hour_close  # pin the hourly close so aggregation is exact
        closes.extend(path.tolist())
        prev = hour_close

    close = np.asarray(closes)
    index = pd.date_range(FIXTURE_START, periods=len(close), freq="1min", tz="UTC")
    return pd.DataFrame(
        {
            "Open": np.concatenate([[close[0]], close[:-1]]),
            "High": close + 0.30,
            "Low": close - 0.30,
            "Close": close,
            "Volume": np.full(len(close), 1000.0),
        },
        index=index,
    )


@pytest.fixture(scope="module")
def price_frame() -> pd.DataFrame:
    return _synthetic_1min()


@pytest.fixture(scope="module")
def provider(price_frame) -> HistoricalDataProvider:
    return HistoricalDataProvider(price_frame, symbol=SYMBOL)


@pytest.fixture(scope="module")
def as_of(price_frame) -> datetime:
    """One minute past the last fixture bar, so the whole frame is in the past."""
    return price_frame.index[-1].to_pydatetime() + timedelta(minutes=1)


# ------------------------------------------------------------------ LLM stubs


class _StubVisionLLM:
    """Canned vision response. Mirrors the JSON contract in DEFAULT_VISUAL_PROMPT."""

    def __init__(self, signal="BUY", confidence=85):
        self._payload = json.dumps(
            {
                "signal": signal,
                "confidence": confidence,
                "patterns": ["bullish order block"],
                "support_levels": [],
                "resistance_levels": [],
                "trend": "bullish",
                "trend_strength": "moderate",
                "reasoning": "stub",
                "warnings": [],
            }
        )

    def generate_with_image(self, *args, **kwargs):
        return self._payload

    def generate(self, *args, **kwargs):
        return self._payload


class _StubAnthropic:
    """Minimal stand-in for `anthropic.Anthropic` as SentimentAnalystAgent uses it."""

    def __init__(self, sentiment="BULLISH", confidence=80):
        payload = json.dumps(
            {
                "sentiment": sentiment,
                "confidence": confidence,
                "drivers": ["stub"],
                "market_psychology": "stub",
                "contrarian_signals": [],
                "time_horizon": "short-term",
                "reasoning": "stub",
            }
        )
        block = type("Block", (), {"text": payload})()
        response = type("Response", (), {"content": [block]})()
        self.messages = type("Messages", (), {"create": lambda _self, **kw: response})()


# ------------------------------------------------------------------- pipeline


def _build_pipeline(provider):
    """Wire the real agents onto a real MessageBus with injected data sources."""
    bus = MessageBus()

    account_state = {
        "current_balance": 10_000.0,
        "daily_pnl_pct": 0.0,
        "drawdown_pct": 0.0,
        "open_positions": 0,
        "trades_today": 0,
    }

    technical = TechnicalAnalystAgent(provider=provider)

    visual = VisualAnalystAgent()
    visual.analysis_service = VisualAnalysisService(llm_client=_StubVisionLLM())

    sentiment = SentimentAnalystAgent()
    sentiment.client = _StubAnthropic()

    # llm_client=None -> MetaDecisionService takes its deterministic threshold
    # path, so this test asserts routing and arithmetic, not model output.
    meta = MetaAgentNew(llm_client=None, account_state_fn=lambda: dict(account_state))
    risk = RiskManagerAgentNew(account_state_fn=lambda: dict(account_state))

    agents = {
        AgentType.TECHNICAL_ANALYST: technical,
        AgentType.VISUAL_ANALYST: visual,
        AgentType.SENTIMENT_ANALYST: sentiment,
        AgentType.META_AGENT: meta,
        AgentType.RISK_MANAGER: risk,
    }
    for agent_type, agent in agents.items():
        agent.activate()
        bus.register_agent(agent_type, agent.process_message)

    return bus, agents


def _pump(bus, agents, rounds=6):
    """Drain every mailbox until the bus goes quiet."""
    for _ in range(rounds):
        pending = sum(bus.get_pending_count(a) for a in agents)
        if not pending:
            return
        for agent_type in agents:
            bus.process_messages(agent_type, max_messages=20)


def _scan(provider, as_of, bus):
    """
    Run one Scanner pass and put its output on the bus.

    Stage 1's quality logic is the real thing; only the two DB-bound data reads
    are substituted (see module docstring).
    """
    analysis = run_technical_analysis(provider, SYMBOL, as_of=as_of)

    scanner = ScannerAgentNew(config={"symbols": [SYMBOL]})
    scanner.activate()
    scanner.technical_analyst.get_latest_analysis = lambda symbol: analysis
    # Chart bytes are never looked at (the vision LLM is stubbed), but they must
    # be valid base64 because the Visual analyst decodes before dispatching.
    scanner.chart_generator.generate_charts = lambda symbol: {
        "15min": {"image_base64": base64.b64encode(b"stub-png").decode(), "timeframe": "15min"}
    }

    trigger = {
        "triggered": True,
        "triggers": ["candle_close_1h", "volume_spike"],
        "reason": "fixture",
        "priority": 8,
        "metadata": {},
    }
    messages = scanner._run_two_stage_analysis(SYMBOL, trigger)
    messages = [] if messages is None else (
        messages if isinstance(messages, list) else [messages]
    )
    for message in messages:
        message.data.setdefault("as_of", as_of)
        message.data.setdefault(
            "market_data", {"price": provider.get_price(SYMBOL, as_of=as_of)}
        )
        bus.send_message(message)

    return scanner, analysis, messages


# ----------------------------------------------------------------- the claim


def test_scan_reaches_risk_manager(provider, as_of):
    """One scan -> one decision that arrives at the Risk Manager and is judged."""
    bus, agents = _build_pipeline(provider)
    risk = agents[AgentType.RISK_MANAGER]

    received = []
    real_process = risk.process_message

    def spy(message):
        if message.type == MessageType.TRADE_REQUEST:
            received.append(message)
        return real_process(message)

    bus.register_agent(AgentType.RISK_MANAGER, spy)

    _scanner, analysis, messages = _scan(provider, as_of, bus)

    by_tf = {
        tf: (a["signal"], a["confidence"], a["confluence_score"])
        for tf, a in analysis["timeframe_analysis"].items()
    }
    assert analysis["signal"] in ("LONG", "SHORT"), (
        f"cascade produced {analysis['signal']} on the fixture bar — the "
        f"pipeline cannot be exercised at all. By timeframe: {by_tf}"
    )
    assert messages, "Scanner passed Stage 1 but emitted no analysis request"

    _pump(bus, agents)

    assert len(received) == 1, (
        f"expected exactly 1 TRADE_REQUEST at the Risk Manager, got "
        f"{len(received)}"
    )

    request = received[0].data
    assert request["direction"] in ("LONG", "SHORT")
    assert request["entry_price"] > 0
    assert request["market_context"]["atr"] > 0, (
        "Risk Manager received a request with no ATR — it would reject every "
        "trade as 'invalid_data' regardless of the decision"
    )

    # Arrival is the claim; a real verdict is the point of arriving.
    verdict = risk.validate_trade_request(request)
    assert verdict["status"] in ("APPROVED", "REJECTED")
    assert verdict.get("category") not in ("invalid_data", "system_error"), (
        f"Risk Manager could not evaluate the request: {verdict}"
    )


def test_analysis_request_reaches_all_three_analysts(provider, as_of):
    """Scanner fans out to the analysts, not to the Meta agent."""
    bus, agents = _build_pipeline(provider)

    seen = {t: [] for t in agents}
    for agent_type, agent in agents.items():
        real = agent.process_message
        bus.register_agent(
            agent_type,
            lambda m, _t=agent_type, _r=real: (seen[_t].append(m.type), _r(m))[1],
        )

    _scan(provider, as_of, bus)
    _pump(bus, agents)

    for analyst in (
        AgentType.TECHNICAL_ANALYST,
        AgentType.VISUAL_ANALYST,
        AgentType.SENTIMENT_ANALYST,
    ):
        assert MessageType.ANALYSIS_REQUEST in seen[analyst], (
            f"{analyst.value} never received the scan request"
        )


def test_meta_waits_for_all_three_then_decides(provider, as_of):
    """Meta holds until all three analysts report, then emits exactly once."""
    bus, agents = _build_pipeline(provider)
    meta = agents[AgentType.META_AGENT]

    _scan(provider, as_of, bus)

    bus.process_messages(AgentType.TECHNICAL_ANALYST, max_messages=20)
    bus.process_messages(AgentType.META_AGENT, max_messages=20)
    assert meta.pending_analyses.get(SYMBOL) is not None
    assert bus.get_pending_count(AgentType.RISK_MANAGER) == 0, (
        "Meta decided before all analysts reported"
    )

    _pump(bus, agents)
    assert SYMBOL not in meta.pending_analyses, "Meta never cleared its pending slot"


# --------------------------------------------------- the four blocking defects


def _votes(provider, as_of, timeframe="1h"):
    analysis = run_technical_analysis(provider, SYMBOL, as_of=as_of)
    return analysis["timeframe_analysis"][timeframe]


def test_fix1_volume_is_not_a_silent_abstainer(provider, as_of):
    """Volume must not sit in the denominator without ever voting."""
    detail = _votes(provider, as_of)["confluence_details"]
    assert "Volume" not in detail["details"], (
        "Volume is still counted as a voter; it can only ever return NEUTRAL, "
        "so it depresses every confluence score by occupying a slot"
    )
    assert len(detail["details"]) == len(ConfluenceChecker.VOTERS)


def test_fix2_bollinger_votes_when_price_is_below_the_band(provider, as_of):
    """Bollinger's offset is band-relative, so a real excursion registers."""
    tf = _votes(provider, as_of)
    indicators = tf["indicators"]
    assert indicators["close"] < indicators["bb_lower"], "fixture is not below the band"
    assert tf["confluence_details"]["details"]["Bollinger"] == "LONG", (
        "price is below the lower band but Bollinger abstained — the offset is "
        "still being applied as a fraction of price, not of band width"
    )


@pytest.mark.parametrize("agreeing,expected", [(3, 0.60), (4, 0.75), (5, 0.90)])
def test_fix3_cascade_admits_every_rung_of_the_confidence_ladder(agreeing, expected):
    """
    Every confidence the ladder can emit must be able to clear the gate it is
    compared against. The 3-indicator rung is exactly the Case A/B gate value,
    so a strict `>` silently rejected it.
    """
    from services.timeframe_cascade import TimeframeCascade

    signal, confidence, score = ConfluenceChecker._determine_signal(
        ["a", "b", "c", "d", "e"][:agreeing], [], []
    )
    assert (signal, confidence, score) == ("LONG", expected, agreeing)

    cascade = TimeframeCascade()
    lead = {"signal": "LONG", "confidence": confidence, "timeframe": "1h"}
    assert cascade._check_case_a({}, lead, [{}, {}]), (
        f"a {agreeing}-indicator LONG at confidence {confidence} cannot pass "
        f"the Case A gate"
    )


def test_fix4_sentiment_speaks_the_meta_agents_language(provider, as_of):
    """BULLISH/0-100 must reach the Meta agent as LONG/0-1, and actually score."""
    from services.analysis.meta_decision import MetaDecisionService

    sentiment = SentimentAnalystAgent()
    sentiment.client = _StubAnthropic(sentiment="BULLISH", confidence=80)
    sentiment.activate()

    result = sentiment.process_message(
        Message(
            msg_type=MessageType.ANALYSIS_REQUEST,
            sender=AgentType.SCANNER,
            recipient=AgentType.SENTIMENT_ANALYST,
            data={"symbol": SYMBOL, "market_data": {"price": 1950.0}},
        )
    )

    assert result.data["signal"] == "LONG", "sentiment still emits BULLISH/BEARISH"
    assert 0.0 <= result.data["confidence"] <= 1.0, "sentiment confidence is not 0-1"

    service = MetaDecisionService(weights={"visual": 0.35, "technical": 0.50, "sentiment": 0.15})
    with_sentiment, _, _ = service.calculate_weighted_score(
        {"signal": "PASS", "confidence": 0.0},
        {"signal": "PASS", "confidence": 0.0},
        {"signal": result.data["signal"], "confidence": result.data["confidence"]},
    )
    assert with_sentiment > 0.0, (
        "sentiment holds 15% of the weight budget but contributed nothing"
    )

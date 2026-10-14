import pandas as pd

TIER_1_EVENTS = [
    # FOMC / Federal Reserve
    'Federal Funds Rate',
    'FOMC Statement',
    'FOMC Press Conference',
    'FOMC Meeting Minutes',
    'FOMC Economic Projections',
    'Fed Announcement',
    'Fed Chair Powell Speaks',
    'Fed Chair Powell Testifies',

    # US Inflation
    'CPI m/m',
    'CPI y/y',
    'Core CPI m/m',
    'Core PCE Price Index m/m',

    # US Employment (NFP cluster)
    'Non-Farm Employment Change',
    'Unemployment Rate',
    'Average Hourly Earnings m/m',

    # ECB
    'Main Refinancing Rate',
    'ECB Press Conference',
    'Monetary Policy Statement',
]

TIER_2_EVENTS = [
    # USD: Producer prices and consumption
    'PPI m/m',
    'Core PPI m/m',
    'Retail Sales m/m',
    'Core Retail Sales m/m',

    # USD: Growth
    'Advance GDP q/q',
    'Prelim GDP q/q',
    'Final GDP q/q',
    'Employment Cost Index q/q',

    # USD: Employment (non-NFP)
    'Unemployment Claims',
    'ADP Non-Farm Employment Change',
    'JOLTS Job Openings',

    # USD: Activity / Sentiment
    'ISM Manufacturing PMI',
    'ISM Services PMI',
    'Flash Manufacturing PMI',
    'Flash Services PMI',
    'CB Consumer Confidence',
    'Prelim UoM Consumer Sentiment',

    # USD: Fed speakers — generic entries covered by is_tier_2() pattern match
    'FOMC Member Williams Speaks',

    # GBP: BOE decisions
    'Official Bank Rate',
    'Monetary Policy Summary',
    'MPC Official Bank Rate Votes',
    'BOE Monetary Policy Report',
    'BOE Gov Bailey Speaks',

    # GBP: UK macro
    'GDP m/m',
    'CPI y/y',
    'Claimant Count Change',

    # EUR: PMIs and inflation (London morning window)
    'French Flash Manufacturing PMI',
    'French Flash Services PMI',
    'German Flash Manufacturing PMI',
    'German Flash Services PMI',
    'German ifo Business Climate',
    'German Prelim CPI m/m',
    'CPI Flash Estimate y/y',
    'ECB President Lagarde Speaks',

    # CNY: China macro
    '1-y Loan Prime Rate',
    '5-y Loan Prime Rate',
    'GDP q/y',
    'Manufacturing PMI',
    'Non-Manufacturing PMI',
    'Caixin Manufacturing PMI',
]

TIER_1_SET = set(TIER_1_EVENTS)
TIER_2_SET = set(TIER_2_EVENTS)

TIER_1_WINDOW = {'before': 45, 'after': 30}   # minutes
TIER_2_WINDOW = {'before': 20, 'after': 15}


def classify_event(event_name: str, currency: str) -> int | None:
    """Return 1, 2, or None for a given event name + currency."""
    if event_name in TIER_1_SET:
        return 1
    if event_name in TIER_2_SET:
        return 2
    # Catch all per-member Fed speaker entries (e.g. 'FOMC Member Waller Speaks')
    if (currency == 'USD'
            and isinstance(event_name, str)
            and event_name.startswith('FOMC Member')
            and event_name.endswith('Speaks')):
        return 2
    return None


def tag_events(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add a 'Tier' column (1, 2, or NaN) to a ForexFactory dataframe.
    Expects columns: Event, Currency, Combined DateTime.
    """
    df = df.copy()
    df['Tier'] = df.apply(
        lambda r: classify_event(r['Event'], r['Currency']), axis=1
    )
    df['Combined DateTime'] = pd.to_datetime(df['Combined DateTime'])
    return df


def is_in_blackout(timestamp: pd.Timestamp, events_df: pd.DataFrame) -> int | None:
    """
    Given a timestamp and a pre-tagged events dataframe, return the highest
    tier blackout active at that moment (1 takes priority over 2), or None.
    """
    for tier, window in [(1, TIER_1_WINDOW), (2, TIER_2_WINDOW)]:
        tier_events = events_df[events_df['Tier'] == tier]['Combined DateTime']
        before = pd.Timedelta(minutes=window['before'])
        after = pd.Timedelta(minutes=window['after'])
        in_window = ((tier_events - timestamp).abs() <= before) | \
                    ((timestamp - tier_events).between(pd.Timedelta(0), after))
        if in_window.any():
            return tier
    return None

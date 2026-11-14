"""Pure calculations shared by online refresh and cached rebuilds."""
from math import isfinite


def positive_number(value, allow_zero=False):
    try:
        value = float(value)
        return value if isfinite(value) and (value >= 0 if allow_zero else value > 0) else None
    except (TypeError, ValueError):
        return None


def calculate_daily(targets, shares, nav, dates, trade_days=None):
    """Never attribute a multi-session change or an old NAV to a single day.

    Missing observations remain None. A possible split is flagged for review,
    rather than silently labelled a genuine zero subscription.
    """
    expected = sorted(trade_days or dates)
    previous = {d: expected[i - 1] for i, d in enumerate(expected) if i}
    daily = []
    for code, name, market in targets:
        for day in dates:
            value = positive_number(shares.get(day, {}).get(code), allow_zero=True)
            if value is None:
                continue
            prev_day = previous.get(day)
            prev = positive_number(shares.get(prev_day, {}).get(code), allow_zero=True)
            unit_nav = positive_number(nav.get(code, {}).get(day))
            prev_nav = positive_number(nav.get(code, {}).get(prev_day))
            flow, delta, status = None, None, "missing_previous_share"
            if prev is not None:
                delta = value - prev
                status = "missing_nav" if unit_nav is None else "ok"
                if unit_nav is not None:
                    flow = delta * unit_nav
                    if prev and prev_nav and value:
                        rs, rn = value / prev, unit_nav / prev_nav
                        if abs(rs - 1) > .30 and (rs - 1) * (rn - 1) < 0 and .6 < rs * rn < 1.6:
                            flow, delta, status = None, None, "possible_split"
            daily.append({"d": day, "c": code, "n": name, "m": market,
                          "s": value, "w": unit_nav, "f": flow,
                          "delta": delta, "status": status})
    return daily

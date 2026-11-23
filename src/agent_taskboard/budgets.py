"""Per-agent token budgets (tokens only — atb never prices anything).

A session is attributed to the local calendar day / week of its **last
activity**: a long-running session counts in the day it finished, it is not
split across days. "Today" and "this week" use the local timezone; weeks
start on Monday.

Only agents with a configured budget appear. "No data" (no session with a
recorded usage in that window) is ``None``, which renders as “无数据” —
never as zero.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo

from . import config, sessions

#: A budget is "near" from 80% and "over" at 100% of the limit.
NEAR_RATIO = 0.8
OVER_RATIO = 1.0

STATUSES = ("ok", "near", "over", "no_data")


def session_tokens(session) -> int | None:
    """Total tokens of one session (None when the store recorded nothing)."""
    resolver = getattr(session, "resolved_usage", None)
    usage = resolver() if callable(resolver) else getattr(session, "usage", None)
    if not usage:
        return None
    if usage.get("total") is not None:
        return int(usage["total"])
    parts = [
        int(usage[key])
        for key in ("input", "output", "cache_read", "cache_write")
        if usage.get(key) is not None
    ]
    return sum(parts) if parts else None


def window_bounds(
    now: datetime, tz: tzinfo | None = None
) -> tuple[datetime, datetime]:
    """Return ``(day_start, week_start)`` as aware local datetimes.

    The week starts on Monday; both bounds are local midnights.
    """
    local = now.astimezone(tz) if tz is not None else now.astimezone()
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=day_start.weekday())
    return day_start, week_start


def _ratio(used: int | None, limit: int | None) -> float | None:
    if used is None or not limit:
        return None
    return round(used / limit, 4)


def _scope_status(ratio: float | None) -> str:
    if ratio is None:
        return "no_data"
    if ratio >= OVER_RATIO:
        return "over"
    if ratio >= NEAR_RATIO:
        return "near"
    return "ok"


def _worst(statuses: list[str]) -> str:
    for status in ("over", "near", "ok"):
        if status in statuses:
            return status
    return "no_data"


def compute(
    cfg: config.Config,
    *,
    now: datetime | None = None,
    tz: tzinfo | None = None,
    collected=None,
) -> list[dict]:
    """Budgets for every configured agent.

    ``now``/``tz`` exist for deterministic tests. ``collected`` lets a caller
    pass already-collected sessions; otherwise the adapters are scanned from
    the start of the current week.
    """
    budgets, _errors = config.configured_budgets(cfg)
    if not budgets:
        return []
    moment = now or datetime.now(timezone.utc)
    day_start, week_start = window_bounds(moment, tz)

    if collected is None:
        collected, _entries = sessions.collect_sessions(
            week_start.astimezone(timezone.utc), None
        )

    day_totals: dict[str, int] = {}
    week_totals: dict[str, int] = {}
    for session in collected:
        last = getattr(session, "last_active_at", None)
        if last is None:
            continue
        local_last = last.astimezone(tz) if tz is not None else last.astimezone()
        if local_last < week_start:
            continue
        value = session_tokens(session)
        if value is None:
            continue
        agent = session.adapter
        week_totals[agent] = week_totals.get(agent, 0) + value
        if local_last >= day_start:
            day_totals[agent] = day_totals.get(agent, 0) + value

    entries: list[dict] = []
    for agent in sorted(budgets):
        budget = budgets[agent]
        used_today = day_totals.get(agent)
        used_week = week_totals.get(agent)
        day_ratio = _ratio(used_today, budget.tokens_per_day)
        week_ratio = _ratio(used_week, budget.tokens_per_week)
        day_status = _scope_status(day_ratio)
        week_status = _scope_status(week_ratio)
        entries.append({
            "agent": agent,
            "tokens_per_day": budget.tokens_per_day,
            "tokens_per_week": budget.tokens_per_week,
            "used_today": used_today,
            "used_week": used_week,
            "day_ratio": day_ratio,
            "week_ratio": week_ratio,
            "day_status": day_status,
            "week_status": week_status,
            "status": _worst([day_status, week_status]),
        })
    return entries

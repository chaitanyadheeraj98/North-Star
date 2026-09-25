"""Recency weighting.

Models change. An execution from last week says more about how a configuration
behaves today than an execution from eighteen months ago, so old evidence is
discounted rather than discarded. The bands live in routing.yaml.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ...schemas.routing_policy import RoutingPolicy


def age_in_days(executed_at: datetime, now: datetime | None = None) -> float:
    """Age of an execution in days, robust to naive timestamps.

    SQLite hands back naive datetimes even for columns declared with a
    timezone, so anything read from the database is assumed to be UTC.
    """
    reference = now or datetime.now(timezone.utc)
    if executed_at.tzinfo is None:
        executed_at = executed_at.replace(tzinfo=timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return max(0.0, (reference - executed_at).total_seconds() / 86400.0)


def weight_for(
    executed_at: datetime, policy: RoutingPolicy, now: datetime | None = None
) -> float:
    return policy.recency_weight(age_in_days(executed_at, now))

"""Beta-Binomial smoothing helpers.

Kept separate from the router so the statistical machinery can be tested in
isolation and reused by analytics, which needs credible intervals rather than
point estimates when it decides whether to claim a configuration is
overpowered.

The whole reason this module exists is to prevent the single most seductive
mistake in a system like this:

    one success out of one attempt is not a 100 percent success rate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class BetaPosterior:
    alpha: float
    beta: float

    @property
    def mean(self) -> float:
        total = self.alpha + self.beta
        return self.alpha / total if total > 0 else 0.5

    @property
    def variance(self) -> float:
        a, b = self.alpha, self.beta
        total = a + b
        if total <= 0:
            return 0.0
        return (a * b) / (total * total * (total + 1.0))

    @property
    def stddev(self) -> float:
        return math.sqrt(self.variance)

    def credible_interval(self, z: float = 1.96) -> tuple[float, float]:
        """Normal approximation to the Beta credible interval.

        Good enough at the sample sizes a personal tool accumulates, and it
        keeps this module dependency-free. Used only to decide whether a
        difference between two configurations is worth telling the user about.
        """
        spread = z * self.stddev
        return (max(0.0, self.mean - spread), min(1.0, self.mean + spread))


def posterior(
    prior_mean: float, prior_strength: float, successes: float, attempts: float
) -> BetaPosterior:
    """Combine a prior belief with observed evidence.

    `prior_strength` is expressed in pseudo-observations: a strength of 12
    means the prior is worth twelve executions, so twelve real executions move
    the estimate halfway from the prior to what was actually observed.
    """
    prior_mean = min(max(prior_mean, 1e-6), 1.0 - 1e-6)
    successes = max(0.0, successes)
    failures = max(0.0, attempts - successes)
    return BetaPosterior(
        alpha=prior_strength * prior_mean + successes,
        beta=prior_strength * (1.0 - prior_mean) + failures,
    )


def shrink(prior_value: float, observed: float | None, samples: float, strength: float) -> float:
    """Shrink a continuous observation toward a prior.

    Used for quantities that are not success/failure - average debug cycles,
    average effective burn - where a Beta posterior does not apply but the same
    "do not trust two data points" discipline does.
    """
    if observed is None or samples <= 0:
        return prior_value
    weight = samples / (samples + strength)
    return (1.0 - weight) * prior_value + weight * observed

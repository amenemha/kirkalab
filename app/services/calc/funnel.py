"""FREE-tier calculation funnel: pure limit/stage logic.

No FastAPI, aiogram or DB imports — it takes plain counts and returns a plain
:class:`FunnelState`, so the rules are unit-testable in isolation and shared by
the API layer and (indirectly, via the response) the bot.

The product rules (see task brief):

* The first ``INTRO_CALCS`` (5) calculations are "introductory" — free trials.
* After the intro is spent, the user gets ``DAILY_LIMIT`` (3) calculations per
  UTC day, resetting at 00:00 UTC.
* Currency-blur funnel, by 1-based intro position (``calc_index``):
    - 1–3: local currency shown in full; payback/ROI/break-even USDT-only (🔒).
    - 4–5: local currency blurred (🔒 ▒▒▒); income & payback USDT-only.
    - after 5 (intro spent): USDT-only, permanent soft PRO invite.
* PRO users have no funnel and no limits.

``calc_index`` is 1-based and is the position of *this* calculation (i.e. the
existing run count + 1).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Spec defaults (CALC_SPEC §3). These are the fallbacks; the API passes the
# configured values from ``Settings`` so the numbers are tunable without code
# changes. Tests call ``evaluate`` without overrides and rely on these.
INTRO_CALCS = 5
DAILY_LIMIT = 3

# Currency-blur boundary inside the intro pool (CALC_SPEC §3.1): the first
# ``LOCAL_FULL_CALCS`` intro calcs show the local currency in full, the rest of
# the intro pool blurs it. This is a presentation rule, independent of the
# limit counts above.
LOCAL_FULL_CALCS = 3


class FunnelStage(str, Enum):
    """How the result screen should present currency for this calculation."""

    # Intro 1–3: full local currency; payback metrics USDT-only behind 🔒.
    LOCAL_FULL = "local_full"
    # Intro 4–5: local currency blurred; income USDT-only.
    LOCAL_BLURRED = "local_blurred"
    # Intro spent: USDT-only, permanent soft PRO invite.
    USDT_ONLY = "usdt_only"
    # PRO: everything in the chosen currency, no gating.
    PRO = "pro"


@dataclass(frozen=True)
class FunnelState:
    """Everything the result screen needs to render the funnel correctly.

    ``allowed`` is False when a FREE user has exhausted both the intro pool and
    today's daily quota — the caller must NOT run the calc and should show the
    paywall invite instead.
    """

    is_pro: bool
    allowed: bool
    stage: FunnelStage
    # 1-based position of this calculation among the user's intro calcs. Caps at
    # INTRO_CALCS+1 once the intro pool is spent. None for PRO.
    calc_index: int | None
    # Remaining introductory calculations BEFORE this one runs.
    intro_left: int
    # Remaining calculations for today BEFORE this one runs (post-intro). None
    # while still inside the intro pool, and None for PRO.
    daily_left: int | None
    # True once the intro pool is spent (the user is on the daily-quota regime).
    intro_spent: bool
    # Soft, warm PRO invite to surface (None when nothing to nudge).
    pro_hint: str | None
    # The limits in force for this evaluation, echoed so the bot can render the
    # progress line ("из N") from the configured numbers rather than guessing.
    intro_calcs: int
    daily_limit: int


_HINT_PAYBACK = "🔒 Окупаемость и ROI в локальной валюте — в PRO"
_HINT_BLUR = "🔒 Полная локальная валюта откроется в PRO"
_HINT_USDT_ONLY = (
    "💎 Бесплатные расчёты в локальной валюте закончились — "
    "включите PRO, чтобы видеть всё в ₽/$/¥ без лимитов"
)
_HINT_DAILY_LAST = "Сегодня это последний бесплатный расчёт — дальше ждём завтра или PRO 💎"


def evaluate(
    *,
    is_pro: bool,
    total_runs: int,
    runs_today: int,
    intro_calcs: int = INTRO_CALCS,
    daily_limit: int = DAILY_LIMIT,
) -> FunnelState:
    """Compute the funnel state for the *next* calculation.

    ``total_runs`` / ``runs_today`` are the counts of calculations the user has
    already performed (all-time and within the current UTC day). This call
    describes the calculation that is about to run, not one that already did.

    ``intro_calcs`` / ``daily_limit`` default to the spec values but are passed
    from ``Settings`` by the API so the limits stay configurable.
    """
    if is_pro:
        return FunnelState(
            is_pro=True,
            allowed=True,
            stage=FunnelStage.PRO,
            calc_index=None,
            intro_left=0,
            daily_left=None,
            intro_spent=True,
            pro_hint=None,
            intro_calcs=intro_calcs,
            daily_limit=daily_limit,
        )

    intro_spent = total_runs >= intro_calcs

    if not intro_spent:
        # Inside the introductory pool.
        calc_index = total_runs + 1  # 1-based position of this calc
        intro_left = intro_calcs - total_runs  # before this one runs
        if calc_index <= LOCAL_FULL_CALCS:
            stage = FunnelStage.LOCAL_FULL
            pro_hint = _HINT_PAYBACK
        else:  # remaining intro calcs: local currency blurred
            stage = FunnelStage.LOCAL_BLURRED
            pro_hint = _HINT_BLUR
        return FunnelState(
            is_pro=False,
            allowed=True,
            stage=stage,
            calc_index=calc_index,
            intro_left=intro_left,
            daily_left=None,
            intro_spent=False,
            pro_hint=pro_hint,
            intro_calcs=intro_calcs,
            daily_limit=daily_limit,
        )

    # Intro spent: daily-quota regime, always USDT-only.
    #
    # ``runs_today`` counts *all* runs made today, which can include some of the
    # introductory runs (they happened earlier the same day). The daily cap only
    # governs post-intro runs, so we subtract the intro runs that fall on today.
    # Intro runs are the user's first ``intro_calcs`` runs chronologically; the
    # ones that are "today" are those not before today's window:
    runs_before_today = total_runs - runs_today
    intro_runs_today = min(
        runs_today, max(intro_calcs - runs_before_today, 0)
    )
    post_intro_today = runs_today - intro_runs_today
    daily_left = daily_limit - post_intro_today
    allowed = daily_left > 0
    hint = _HINT_USDT_ONLY
    if allowed and daily_left == 1:
        hint = _HINT_DAILY_LAST
    return FunnelState(
        is_pro=False,
        allowed=allowed,
        stage=FunnelStage.USDT_ONLY,
        calc_index=intro_calcs + 1,
        intro_left=0,
        daily_left=max(daily_left, 0),
        intro_spent=True,
        pro_hint=hint,
        intro_calcs=intro_calcs,
        daily_limit=daily_limit,
    )

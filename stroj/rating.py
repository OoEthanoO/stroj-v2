"""Competitive rating: what a rated contest does to where you stand.

Elo, generalised to a field. Every contestant is scored against every other
one as if they had played a head-to-head game — you "beat" everyone you
finished above — and the results are averaged, so a contest is worth about the
same however many people turn up.

The part that is not textbook Elo is **how much** a result moves you, which
here depends on how well the judge currently knows you rather than on a fixed
K. Each competitor carries a rating *deviation*: high when their rating is a
guess, low when it has been confirmed by recent contests. Deviation shrinks
every time they compete and grows while they do not.

That is what makes optional biweekly contests work. Someone who enters every
round has a settled rating and moves in small steps, so one bad Saturday does
not undo a term. Someone who skips two months has a rating that no longer
describes them, and the judge knows it: their deviation has grown, so their
first contest back moves them a long way and they are quickly placed again.

Note the choice this encodes. Missing contests costs you *nothing* directly —
inactivity grows uncertainty, it does not decay rating. A member who is busy
with exams for a month should come back to the rating they earned, not to a
punishment for having had exams. The only thing that changes is how fast the
judge is willing to be talked out of it.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

#: Where a competitor starts: Specialist 3, just under the middle of the
#: ladder. Eleven rungs below to fall into and thirteen above to climb, so a
#: season sorts people in both directions rather than only upward.
START_RATING = int(os.environ.get("STROJ_START_RATING", "1000"))

#: Ratings do not go below this, so a run of bad contests cannot produce a
#: number that reads as a joke rather than a standing.
RATING_FLOOR = 100

#: The eight tiers that have divisions, lowest first. Legend stands alone.
#:
#: Named for stages of learning a craft rather than for metals and gemstones,
#: which is a ladder borrowed from shooters and says nothing about what anyone
#: here is doing. The order is meant to be obvious without a legend: nobody has
#: to be told that an Expert outranks an Apprentice. "Novice" is also a fair
#: thing to call someone in their first month, which the bottom rung of a club
#: ladder has to be.
TIERS = (
    "Novice",
    "Apprentice",
    "Adept",
    "Specialist",
    "Expert",
    "Master",
    "Grandmaster",
    "Champion",
)
DIVISIONS = 3
TOP_TIER = "Legend"

#: Bottom of Novice 1. Anything below still reads as Novice 1 — the floor of
#: the ladder is a floor, not a hole.
RANK_FLOOR = 785
#: Rating points per division.
#:
#: Sized to **one season**, September to May, because that is the only span
#: anyone here actually has. Members arrive in September and the club turns
#: over in May; a ladder whose top rungs need a second year is a ladder with
#: five decorative rungs for everybody who will ever use it.
#:
#: Measured rather than guessed, by `scripts/simulate-season.py`. Across forty
#: simulated clubs of sixteen playing **fourteen biweekly contests** at seventy
#: percent attendance, the weakest member finishes near 790 and the best near
#: 1210. Novice 1 begins where the weakest finish, and twenty-four divisions
#: reach from there to Legend.
#:
#: Width 18 rather than 19, which is where fitting to the *standout* — the best
#: result across all forty clubs — first put it. That is a one-in-forty figure
#: by construction, and it made Legend a rank one club running one season saw
#: about once in fifteen years: the club's winner finishes at 1208 in a median
#: season and 1236 at the ninth decile, against a threshold of 1241. Legend was
#: decorative, which is the exact fault this comment warns about two paragraphs
#: up. One point narrower puts the threshold at 1217, so the typical winner
#: lands in Champion 3 one rung short and about a third of seasons crown
#: somebody — special, but not mythical. Every rung stays occupied either way.
#:
#: The band is narrower than the weekly calendar produced (720-1320) for the
#: obvious reason: half as many contests is half as many chances to move. The
#: fix is these narrower rungs rather than a bigger `K_SETTLED`, because Elo in
#: a closed pool self-limits — once you are expected to beat everyone, winning
#: stops paying. Simulating it says so plainly: doubling the settled K widens a
#: season by about 35 points and improves how well the final table sorts the
#: room by nothing at all. It buys volatility, not reach.
RANK_WIDTH = 18
#: 24 divisions, then Legend.
LEGEND_AT = RANK_FLOOR + len(TIERS) * DIVISIONS * RANK_WIDTH

#: A rating nobody has confirmed yet.
DEVIATION_NEW = 350.0
#: The most certain the judge ever claims to be. Never zero: a rating is always
#: a guess about a person who is still learning.
DEVIATION_MIN = 50.0

#: One rating period is a fortnight, because that is how often contests run.
#:
#: Which means every gap between contests costs exactly one period of
#: forgetting, and so the deviation never reaches `DEVIATION_MIN`. Competing
#: shrinks it, the fortnight's wait grows it back, and the two settle at a
#: standoff around 137 — an effective K near 49 rather than the 20 below.
#: `K_SETTLED` is not reachable on this calendar; on a weekly one the standoff
#: sits at 97, on a monthly one at 193.
#:
#: That is the intended behaviour rather than a leak. Fourteen contests is few,
#: and a rating that stopped moving after five of them would spend most of a
#: season describing September. The cost is that one disastrous contest moves a
#: leader about 45 points instead of 30 — a little over two rungs. The ladder
#: above was fitted with this standoff in place, so the season's spread already
#: accounts for it.
PERIOD_DAYS = 14.0
#: Periods of silence that take a fully settled rating back to knowing nothing.
#: Thirteen fortnights is half a school year — long enough that a busy term does
#: not reset you. Deliberately a span of *calendar* time rather than a count of
#: missed contests: what makes a rating stale is the months passing, not the
#: entry list going by, so halving the contest calendar must not halve the
#: memory. (Both constants doubling and halving together is why the day-to-day
#: decay below is unchanged by the move off weekly contests.)
PERIODS_TO_FORGET = 13.0
#: Per-period growth constant, from the two facts above.
DEVIATION_GROWTH = math.sqrt(
    (DEVIATION_NEW**2 - DEVIATION_MIN**2) / PERIODS_TO_FORGET
)

#: How far a settled competitor can move in one contest, and how far an
#: unplaced one can. The gap between them is the whole placement mechanic.
K_SETTLED = 20.0
K_UNPLACED = 120.0


@dataclass(frozen=True)
class Entrant:
    """One competitor as they stood when the contest started."""

    user_id: int
    rating: int
    deviation: float
    #: Days since their last rated contest. None for a first-timer.
    days_idle: float | None
    #: 1 for first place. Equal places mean a tie.
    place: int


@dataclass(frozen=True)
class Change:
    user_id: int
    place: int
    rating_before: int
    rating_after: int
    deviation_before: float
    deviation_after: float

    @property
    def delta(self) -> int:
        return self.rating_after - self.rating_before


def expected_score(rating: float, opponent: float) -> float:
    """Chance of finishing above one opponent. Plain Elo logistic."""
    return 1.0 / (1.0 + 10.0 ** ((opponent - rating) / 400.0))


def grown_deviation(deviation: float, days_idle: float | None) -> float:
    """Deviation after a gap, before the contest is scored.

    Glicko's rule: uncertainty accumulates as a variance, so it grows with the
    square root of elapsed time rather than linearly — two weeks off is not
    twice as forgetful as one.
    """
    if days_idle is None or days_idle <= 0:
        return min(deviation, DEVIATION_NEW)
    periods = days_idle / PERIOD_DAYS
    grown = math.sqrt(deviation**2 + (DEVIATION_GROWTH**2) * periods)
    return min(grown, DEVIATION_NEW)


def shrunk_deviation(deviation: float, opponents: int) -> float:
    """Deviation after competing against `opponents` people.

    A bigger field says more about you than a small one, so it settles the
    rating faster — but with diminishing returns, because the twentieth
    opponent tells you much less than the second.
    """
    if opponents <= 0:
        return deviation
    learned = 0.5 * opponents / (opponents + 8.0)
    return max(DEVIATION_MIN, deviation * (1.0 - learned))


def k_factor(deviation: float) -> float:
    """How far one contest may move a rating, given how well it is known."""
    span = DEVIATION_NEW - DEVIATION_MIN
    position = (deviation - DEVIATION_MIN) / span
    position = min(1.0, max(0.0, position))
    return K_SETTLED + (K_UNPLACED - K_SETTLED) * position


def compute(entrants: list[Entrant]) -> list[Change]:
    """Rate one contest. Pure: no clock, no database, no ordering assumptions.

    A single entrant is left untouched. Beating nobody is not evidence, and a
    field of one would otherwise hand out points for turning up alone.
    """
    if len(entrants) < 2:
        return []

    grown = {e.user_id: grown_deviation(e.deviation, e.days_idle) for e in entrants}
    changes = []

    for entrant in entrants:
        others = [o for o in entrants if o.user_id != entrant.user_id]
        actual = sum(
            1.0 if entrant.place < other.place
            else 0.5 if entrant.place == other.place
            else 0.0
            for other in others
        )
        expected = sum(
            expected_score(entrant.rating, other.rating) for other in others
        )
        # Averaged over the field, so turnout changes who you played, not how
        # much the contest was worth.
        margin = (actual - expected) / len(others)
        deviation = grown[entrant.user_id]
        after = entrant.rating + k_factor(deviation) * margin

        changes.append(
            Change(
                user_id=entrant.user_id,
                place=entrant.place,
                rating_before=entrant.rating,
                rating_after=max(RATING_FLOOR, round(after)),
                deviation_before=entrant.deviation,
                deviation_after=shrunk_deviation(deviation, len(others)),
            )
        )
    return changes


@dataclass(frozen=True)
class Rank:
    tier: str
    #: 1, 2 or 3 — or None for Legend, which has no divisions.
    division: int | None
    #: Position on the whole ladder, 0 for Novice 1. Useful for colouring.
    index: int

    @property
    def name(self) -> str:
        return self.tier if self.division is None else f"{self.tier} {self.division}"


#: Total number of rungs, Legend included.
LADDER_SIZE = len(TIERS) * DIVISIONS + 1


def rank_for(rating: int, rated_contests: int = 1) -> Rank | None:
    """Where a rating sits, or None for someone who has not competed yet.

    An unrated competitor has no rank rather than the rank their starting
    number would imply, because that number is a placeholder and showing it as
    a standing would be a claim the judge cannot support.
    """
    if rated_contests <= 0:
        return None
    step = (rating - RANK_FLOOR) // RANK_WIDTH
    index = min(LADDER_SIZE - 1, max(0, int(step)))
    if index >= len(TIERS) * DIVISIONS:
        return Rank(TOP_TIER, None, LADDER_SIZE - 1)
    return Rank(TIERS[index // DIVISIONS], index % DIVISIONS + 1, index)


def rank_dict(rating: int, rated_contests: int = 1) -> dict | None:
    rank = rank_for(rating, rated_contests)
    if rank is None:
        return None
    return {
        "name": rank.name,
        "tier": rank.tier,
        "division": rank.division,
        "index": rank.index,
        "of": LADDER_SIZE,
    }


def ladder() -> list[dict]:
    """Every rung with the rating it starts at — for a legend the site can
    render without duplicating any of the arithmetic above."""
    rungs = []
    for index in range(LADDER_SIZE):
        floor = RANK_FLOOR + index * RANK_WIDTH
        rank = rank_for(floor + 1)
        assert rank is not None
        rungs.append(
            {
                "name": rank.name,
                "tier": rank.tier,
                "division": rank.division,
                "index": index,
                "from": floor,
                "to": None if index == LADDER_SIZE - 1 else floor + RANK_WIDTH - 1,
            }
        )
    return rungs


def placement_contests(field: int = 15) -> int:
    """How many contests it takes a newcomer to reach a settled rating.

    Derived by running the real decay rather than stated as a number, so the
    page explaining the system cannot drift from the system.
    """
    deviation = DEVIATION_NEW
    for played in range(1, 50):
        deviation = shrunk_deviation(deviation, field)
        if deviation <= DEVIATION_MIN:
            return played
    return 0


def explain() -> dict:
    """Everything the site needs to describe the system truthfully.

    All of it computed from the constants above, so the explanation is a view
    of the implementation rather than a second copy of it that some later
    tuning forgets to update.
    """
    settled_regular = grown_deviation(DEVIATION_MIN, PERIOD_DAYS)
    return {
        "start": START_RATING,
        "legend_at": LEGEND_AT,
        "rank_width": RANK_WIDTH,
        "placement_contests": placement_contests(),
        "period_days": int(PERIOD_DAYS),
        # Weeks, not periods: the page says "after about N weeks away", and a
        # reader counts weeks whatever the contest calendar happens to be.
        "weeks_to_forget": round(PERIODS_TO_FORGET * PERIOD_DAYS / 7),
        # What one contest can do to you, by how well you are known. The
        # spread between the first and last row is the whole timing mechanic.
        "movement": [
            {
                "who": "Your first contest",
                "max_move": round(k_factor(DEVIATION_NEW)),
            },
            {
                "who": f"Still placing (first {placement_contests()} contests)",
                "max_move": round(k_factor(shrunk_deviation(DEVIATION_NEW, 15))),
            },
            {
                "who": "Competing every round",
                "max_move": round(k_factor(settled_regular)),
            },
            {
                "who": "Back after a month away",
                "max_move": round(k_factor(grown_deviation(DEVIATION_MIN, 28))),
            },
            {
                "who": "Back after a term away",
                "max_move": round(k_factor(grown_deviation(DEVIATION_MIN, 84))),
            },
        ],
    }

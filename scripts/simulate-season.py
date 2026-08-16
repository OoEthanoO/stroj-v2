#!/usr/bin/env python3
"""Measure what one season does to a rating ladder.

`RANK_FLOOR` and `RANK_WIDTH` in `stroj/rating.py` are not guesses: they are
fitted to where a season actually leaves people, so that the bottom rung is
where the weakest member finishes and Legend is where a standout arrives — and
every rung between them is reachable by somebody. That fit has to be redone
whenever the contest calendar changes, because half as many contests is half as
many chances to move.

This is the tool that measures it. A club of `--size` members is given fixed
latent skills, plays `--contests` rated contests `--spacing` days apart at
`--attendance`, and is rated by the real `stroj.rating.compute`. Repeat over
`--clubs` clubs and report the landmarks:

* **weakest**   median across clubs of the lowest final rating — where the
                bottom rung belongs;
* **best**      median across clubs of the highest — a strong season;
* **standout**  the best single result anywhere — where Legend belongs;
* **rho**       Spearman correlation between latent skill and final rating,
                i.e. whether the table sorted the room correctly.

Usage:

    python3 scripts/simulate-season.py                 # the club's calendar
    python3 scripts/simulate-season.py --contests 28 --spacing 7   # weekly
    python3 scripts/simulate-season.py --fit           # also fit the ladder

The skill and noise spreads are the club model, not the rating system: they say
how far apart the members are and how much one contest is a coin flip. They are
set to reproduce the landmarks the ladder was originally fitted to, so runs
across calendars are comparable.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stroj import rating  # noqa: E402

#: Spread of latent skill within one club, in rating points.
SKILL_SIGMA = 250.0
#: How much one contest is luck: a whole day's form, in the same units.
NOISE_SIGMA = 150.0


def run_club(rng, skills, contests, spacing_days, attendance):
    """One club's season. Returns each member's final rating."""
    size = len(skills)
    ratings = [rating.START_RATING] * size
    deviations = [rating.DEVIATION_NEW] * size
    last_day: list[float | None] = [None] * size

    for index in range(contests):
        day = index * spacing_days
        present = [i for i in range(size) if rng.random() < attendance]
        if len(present) < 2:
            continue
        performance = {i: skills[i] + rng.gauss(0, NOISE_SIGMA) for i in present}
        order = sorted(present, key=lambda i: -performance[i])
        entrants = [
            rating.Entrant(
                user_id=i,
                rating=ratings[i],
                deviation=deviations[i],
                days_idle=None if last_day[i] is None else float(day - last_day[i]),
                place=place + 1,
            )
            for place, i in enumerate(order)
        ]
        for change in rating.compute(entrants):
            ratings[change.user_id] = change.rating_after
            deviations[change.user_id] = change.deviation_after
            last_day[change.user_id] = day
    return ratings


def spearman(skills, ratings) -> float:
    size = len(skills)
    rank_of = lambda values: {  # noqa: E731
        item: position
        for position, item in enumerate(sorted(range(size), key=lambda i: values[i]))
    }
    skill_rank, rating_rank = rank_of(skills), rank_of(ratings)
    squares = sum((skill_rank[i] - rating_rank[i]) ** 2 for i in range(size))
    return 1 - 6 * squares / (size * (size**2 - 1))


def season(seed, *, clubs, size, contests, spacing_days, attendance) -> dict:
    rng = random.Random(seed)
    lowest, highest, correlations = [], [], []
    for _ in range(clubs):
        skills = [rng.gauss(0, SKILL_SIGMA) for _ in range(size)]
        finals = run_club(rng, skills, contests, spacing_days, attendance)
        lowest.append(min(finals))
        highest.append(max(finals))
        correlations.append(spearman(skills, finals))
    return {
        "weakest": int(statistics.median(lowest)),
        "best": int(statistics.median(highest)),
        "standout": max(highest),
        "rho": statistics.mean(correlations),
    }


def fit_ladder(weakest: int, standout: int) -> tuple[int, int]:
    """(RANK_FLOOR, RANK_WIDTH) that spans a season with every rung reachable.

    Novice 1 starts where the weakest finish; Legend starts at or just below
    where a standout arrives, so the top rung is winnable but not handed out.
    """
    rungs = len(rating.TIERS) * rating.DIVISIONS
    width = max(1, (standout - weakest) // rungs)
    return weakest, width


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clubs", type=int, default=40)
    parser.add_argument("--size", type=int, default=16)
    parser.add_argument("--contests", type=int, default=14,
                        help="rated contests in one September-May season")
    parser.add_argument("--spacing", type=int, default=14,
                        help="days between contests")
    parser.add_argument("--attendance", type=float, default=0.70)
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--fit", action="store_true",
                        help="print the ladder constants these landmarks imply")
    args = parser.parse_args()

    print(f"{args.clubs} clubs of {args.size}, {args.contests} contests "
          f"{args.spacing} days apart, {args.attendance:.0%} attendance\n")
    print(f"{'seed':>6} {'weakest':>8} {'best':>6} {'standout':>9} {'rho':>6}")
    runs = []
    for seed in range(args.seeds):
        found = season(20260101 + seed, clubs=args.clubs, size=args.size,
                       contests=args.contests, spacing_days=args.spacing,
                       attendance=args.attendance)
        runs.append(found)
        print(f"{seed:>6} {found['weakest']:>8} {found['best']:>6} "
              f"{found['standout']:>9} {found['rho']:>6.3f}")

    weakest = int(statistics.median(r["weakest"] for r in runs))
    best = int(statistics.median(r["best"] for r in runs))
    standout = int(statistics.median(r["standout"] for r in runs))
    print(f"\n{'median':>6} {weakest:>8} {best:>6} {standout:>9} "
          f"{statistics.mean(r['rho'] for r in runs):>6.3f}")

    if args.fit:
        floor, width = fit_ladder(weakest, standout)
        legend = floor + len(rating.TIERS) * rating.DIVISIONS * width
        print(f"\nfitted ladder: RANK_FLOOR = {floor}, RANK_WIDTH = {width}"
              f"  (Legend at {legend})")
        print(f"in use:        RANK_FLOOR = {rating.RANK_FLOOR}, "
              f"RANK_WIDTH = {rating.RANK_WIDTH}  (Legend at {rating.LEGEND_AT})")
        for label, value in (("weakest", weakest), ("best", best),
                             ("standout", standout),
                             ("a newcomer", rating.START_RATING)):
            rank = rating.rank_for(value)
            print(f"  {label:<11} {value:>5} is {rank.name} "
                  f"(rung {rank.index + 1} of {rating.LADDER_SIZE})")


if __name__ == "__main__":
    main()

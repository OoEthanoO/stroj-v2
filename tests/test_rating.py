"""The rating maths: the properties it has to keep, not just worked examples."""

from __future__ import annotations

import pytest

from stroj import rating


def entrant(uid, rating_value=1000, deviation=rating.DEVIATION_MIN,
            days_idle=0.0, place=1):
    return rating.Entrant(uid, rating_value, deviation, days_idle, place)


class TestExpectedScore:
    def test_equal_ratings_are_a_coin_flip(self):
        assert rating.expected_score(1000, 1000) == pytest.approx(0.5)

    def test_four_hundred_points_is_about_nine_in_ten(self):
        assert rating.expected_score(1400, 1000) == pytest.approx(0.909, abs=0.001)

    def test_it_is_symmetric(self):
        assert (rating.expected_score(1200, 900)
                + rating.expected_score(900, 1200)) == pytest.approx(1.0)


class TestOneContest:
    def board(self, ratings, deviation=rating.DEVIATION_MIN):
        """Entrants finishing in the order given."""
        return [rating.Entrant(i, r, deviation, 0.0, i + 1)
                for i, r in enumerate(ratings)]

    def test_winning_gains_and_losing_loses(self):
        changes = rating.compute(self.board([1000, 1000]))
        assert changes[0].delta > 0
        assert changes[1].delta < 0

    def test_beating_an_equal_field_is_symmetric(self):
        changes = rating.compute(self.board([1000, 1000]))
        assert changes[0].delta == -changes[1].delta

    def test_beating_someone_far_below_you_is_worth_little(self):
        strong = rating.compute(self.board([1600, 800]))[0]
        even = rating.compute(self.board([1000, 1000]))[0]
        assert 0 <= strong.delta < even.delta

    def test_losing_to_someone_far_below_you_hurts(self):
        # Same pair, reversed result: the favourite finishes second.
        upset = rating.compute([
            rating.Entrant(0, 1600, rating.DEVIATION_MIN, 0.0, 2),
            rating.Entrant(1, 800, rating.DEVIATION_MIN, 0.0, 1),
        ])
        favourite = next(c for c in upset if c.user_id == 0)
        assert favourite.delta < -10

    def test_a_tie_between_equals_moves_nobody(self):
        pair = [rating.Entrant(0, 1000, rating.DEVIATION_MIN, 0.0, 1),
                rating.Entrant(1, 1000, rating.DEVIATION_MIN, 0.0, 1)]
        assert [c.delta for c in rating.compute(pair)] == [0, 0]

    def test_a_lone_entrant_is_left_alone(self):
        """Turning up by yourself is not evidence of anything."""
        assert rating.compute([entrant(0)]) == []

    def test_places_need_not_be_dense_or_sorted(self):
        scattered = [rating.Entrant(0, 1000, rating.DEVIATION_MIN, 0.0, 7),
                     rating.Entrant(1, 1000, rating.DEVIATION_MIN, 0.0, 3)]
        changes = {c.user_id: c.delta for c in rating.compute(scattered)}
        assert changes[1] > 0 > changes[0]

    def test_ratings_never_fall_through_the_floor(self):
        broke = [rating.Entrant(0, rating.RATING_FLOOR, rating.DEVIATION_NEW, 0.0, 2),
                 rating.Entrant(1, 2000, rating.DEVIATION_MIN, 0.0, 1)]
        assert rating.compute(broke)[0].rating_after >= rating.RATING_FLOOR

    def test_turnout_does_not_change_what_a_contest_is_worth(self):
        """Winning a field of 4 and a field of 20 are worth about the same, so
        a quiet week is not a cheap week."""
        small = rating.compute(self.board([1000] * 4))[0].delta
        large = rating.compute(self.board([1000] * 20))[0].delta
        assert abs(small - large) <= 2


class TestDeviationOverTime:
    def test_competing_settles_a_rating(self):
        assert rating.shrunk_deviation(rating.DEVIATION_NEW, 15) < rating.DEVIATION_NEW

    def test_placement_takes_about_five_contests(self):
        deviation = rating.DEVIATION_NEW
        for _ in range(5):
            deviation = rating.shrunk_deviation(deviation, 15)
        assert deviation == pytest.approx(rating.DEVIATION_MIN, abs=1)

    def test_a_bigger_field_settles_you_faster(self):
        crowd = rating.shrunk_deviation(300, 20)
        handful = rating.shrunk_deviation(300, 3)
        assert crowd < handful

    def test_certainty_has_a_floor(self):
        deviation = rating.DEVIATION_NEW
        for _ in range(50):
            deviation = rating.shrunk_deviation(deviation, 30)
        assert deviation == rating.DEVIATION_MIN

    def test_time_away_makes_a_rating_less_certain(self):
        settled = rating.DEVIATION_MIN
        assert rating.grown_deviation(settled, 70) > settled

    def test_forgetting_is_capped_at_knowing_nothing(self):
        assert rating.grown_deviation(rating.DEVIATION_MIN, 3650) == rating.DEVIATION_NEW

    def test_half_a_year_away_forgets_you_entirely(self):
        away = rating.PERIODS_TO_FORGET * rating.PERIOD_DAYS
        assert rating.grown_deviation(rating.DEVIATION_MIN, away) == pytest.approx(
            rating.DEVIATION_NEW, abs=1)

    def test_uncertainty_grows_with_the_root_of_time(self):
        """Two weeks off is not twice as forgetful as one."""
        one = rating.grown_deviation(rating.DEVIATION_MIN, 7)
        two = rating.grown_deviation(rating.DEVIATION_MIN, 14)
        assert two < 2 * one

    def test_a_first_timer_is_not_punished_for_having_no_history(self):
        assert rating.grown_deviation(rating.DEVIATION_NEW, None) == rating.DEVIATION_NEW


class TestTimingChangesHowFarYouMove:
    """The point of the deviation: when you compete decides how much a result
    is allowed to say about you."""

    def contest(self, deviation, days_idle):
        pair = [rating.Entrant(0, 1000, deviation, days_idle, 1),
                rating.Entrant(1, 1000, rating.DEVIATION_MIN, 0.0, 2)]
        return rating.compute(pair)[0].delta

    def test_a_newcomer_moves_furthest(self):
        assert self.contest(rating.DEVIATION_NEW, None) == pytest.approx(60, abs=1)

    def test_a_weekly_regular_moves_in_small_steps(self):
        assert self.contest(rating.DEVIATION_MIN, 7) < 20

    def test_returning_after_months_moves_you_more_than_a_regular(self):
        regular = self.contest(rating.DEVIATION_MIN, 7)
        returner = self.contest(rating.DEVIATION_MIN, 70)
        assert returner > regular * 2

    def test_missing_contests_costs_nothing_by_itself(self):
        """Absence changes only how fast you move, never the rating itself."""
        assert rating.compute([entrant(0, days_idle=365)]) == []


class TestRankBrackets:
    def test_nine_tiers_eight_with_divisions(self):
        assert len(rating.TIERS) == 8
        assert rating.LADDER_SIZE == 8 * 3 + 1 == 25

    def test_the_named_tiers_are_the_ones_asked_for(self):
        assert [t.lower() for t in rating.TIERS] + [rating.TOP_TIER.lower()] == [
            "iron", "bronze", "silver", "gold", "platinum", "diamond",
            "ascendant", "immortal", "radiant"]

    def test_divisions_count_upward_within_a_tier(self):
        floor = rating.RANK_FLOOR
        names = [rating.rank_for(floor + i * rating.RANK_WIDTH).name for i in range(3)]
        assert names == ["Iron 1", "Iron 2", "Iron 3"]

    def test_radiant_has_no_division(self):
        top = rating.rank_for(rating.RADIANT_AT)
        assert top.tier == "Radiant" and top.division is None
        assert top.name == "Radiant"

    def test_radiant_is_the_only_rank_above_its_threshold(self):
        assert rating.rank_for(rating.RADIANT_AT).name == "Radiant"
        assert rating.rank_for(rating.RADIANT_AT + 10_000).name == "Radiant"

    def test_just_below_radiant_is_immortal_three(self):
        assert rating.rank_for(rating.RADIANT_AT - 1).name == "Immortal 3"

    def test_the_bottom_is_a_floor_not_a_hole(self):
        assert rating.rank_for(rating.RANK_FLOOR).name == "Iron 1"
        assert rating.rank_for(0).name == "Iron 1"
        assert rating.rank_for(-500).name == "Iron 1"

    def test_rank_rises_with_rating(self):
        seen = [rating.rank_for(r).index for r in range(400, 2000, 7)]
        assert seen == sorted(seen)

    def test_someone_who_has_not_competed_has_no_rank(self):
        """Their rating is a placeholder, and showing it as a standing would
        claim something the judge cannot support."""
        assert rating.rank_for(rating.START_RATING, rated_contests=0) is None
        assert rating.rank_dict(rating.START_RATING, 0) is None

    def test_one_contest_is_enough_to_be_ranked(self):
        assert rating.rank_for(rating.START_RATING, rated_contests=1) is not None

    def test_the_ladder_legend_covers_every_rung_without_gaps(self):
        rungs = rating.ladder()
        assert len(rungs) == rating.LADDER_SIZE
        assert [r["name"] for r in rungs][:2] == ["Iron 1", "Iron 2"]
        assert rungs[-1]["name"] == "Radiant" and rungs[-1]["to"] is None
        for lower, upper in zip(rungs, rungs[1:]):
            assert lower["to"] + 1 == upper["from"]

    def test_the_legend_agrees_with_the_function(self):
        for rung in rating.ladder():
            assert rating.rank_for(rung["from"]).name == rung["name"]
            if rung["to"] is not None:
                assert rating.rank_for(rung["to"]).name == rung["name"]

    def test_a_starting_competitor_lands_mid_ladder(self):
        """Room to fall as well as climb, so a first season sorts both ways."""
        index = rating.rank_for(rating.START_RATING).index
        assert 8 <= index <= 16

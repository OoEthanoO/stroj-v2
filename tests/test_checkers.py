from stroj.judge import checkers


class TestTokenChecker:
    def test_whitespace_is_irrelevant(self):
        assert checkers.check("1 2 3", "1\n2\n  3\n\n")

    def test_trailing_newline_is_fine(self):
        assert checkers.check("42\n", "42")

    def test_wrong_value_reports_the_position(self):
        result = checkers.check("1 2 3", "1 9 3")
        assert not result.ok
        assert "token 2" in result.message
        assert "expected '2'" in result.message

    def test_short_output(self):
        result = checkers.check("1 2 3", "1 2")
        assert not result.ok
        assert "too short" in result.message

    def test_long_output(self):
        result = checkers.check("1 2", "1 2 3")
        assert not result.ok
        assert "too long" in result.message

    def test_empty_expected_and_actual_match(self):
        assert checkers.check("", "   \n ")


class TestExactChecker:
    def test_trailing_whitespace_is_forgiven(self):
        assert checkers.check("ab\ncd", "ab   \ncd\n\n\n", mode="exact")

    def test_internal_spacing_matters(self):
        assert not checkers.check("a b", "a  b", mode="exact")

    def test_crlf_is_normalised(self):
        assert checkers.check("a\nb", "a\r\nb\r\n", mode="exact")

    def test_reports_the_line(self):
        result = checkers.check("a\nb\nc", "a\nX\nc", mode="exact")
        assert "line 2" in result.message


class TestFloatChecker:
    def test_within_absolute_epsilon(self):
        assert checkers.check("0.0000001", "0.0", mode="float", eps=1e-6)

    def test_within_relative_epsilon(self):
        # 1e9 apart in absolute terms, but only 1e-9 relatively.
        assert checkers.check("1000000000.0", "1000000001.0", mode="float", eps=1e-6)

    def test_outside_epsilon(self):
        assert not checkers.check("1.0", "1.01", mode="float", eps=1e-6)

    def test_non_numeric_tokens_still_compared_exactly(self):
        assert checkers.check("yes 1.0", "yes 1.0000001", mode="float")
        assert not checkers.check("yes", "no", mode="float")

    def test_nan_never_matches_numerically(self):
        # Byte-identical tokens still match — that comparison happens first —
        # but NaN must never satisfy the epsilon comparison.
        assert checkers.check("nan", "nan", mode="float")
        assert not checkers.check("nan", "NaN", mode="float")
        assert not checkers.check("1.0", "nan", mode="float")
        assert not checkers.check("nan", "inf", mode="float")

    def test_infinities_compare_sensibly(self):
        assert checkers.check("inf", "inf", mode="float")
        assert not checkers.check("inf", "-inf", mode="float")

    def test_integers_compare_as_floats(self):
        assert checkers.check("3", "3.0000000001", mode="float")


def test_unknown_checker_raises():
    import pytest

    with pytest.raises(ValueError):
        checkers.check("a", "a", mode="nope")


def test_long_tokens_are_clipped_in_the_message():
    result = checkers.check("x" * 500, "y" * 500)
    assert len(result.message) < 200
    assert "…" in result.message


class TestHiddenTestsDoNotLeak:
    """Checker messages travel back to the submitter.

    On a hidden test the message must not quote either side: quoting the
    submission's own output turns a wrong answer into a file-read primitive,
    and quoting the expected value hands over the answer to a secret test.
    """

    def test_token_mismatch_hides_both_values(self):
        secret = "SECRET-CONTENTS-OF-ETC-PASSWD"
        result = checkers.check("42", secret, reveal=False)
        assert not result.ok
        assert secret not in result.message
        assert "42" not in result.message
        assert "token 1" in result.message

    def test_exact_mismatch_hides_both_values(self):
        result = checkers.check("expected-line", "leaked-line", mode="exact", reveal=False)
        assert "leaked" not in result.message
        assert "expected-line" not in result.message
        assert "line 1" in result.message

    def test_float_mismatch_hides_both_values(self):
        result = checkers.check("1.0", "999.5", mode="float", reveal=False)
        assert "999.5" not in result.message

    def test_extra_output_is_not_echoed(self):
        result = checkers.check("1", "1 SECRET", reveal=False)
        assert "SECRET" not in result.message
        assert "too long" in result.message

    def test_samples_still_show_detail(self):
        result = checkers.check("42", "43", reveal=True)
        assert "42" in result.message and "43" in result.message

    def test_reveal_defaults_to_true_for_callers_that_opt_in(self):
        # The default is only safe because runner.py passes test.is_sample.
        assert "43" in checkers.check("42", "43").message

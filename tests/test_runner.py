"""End-to-end judging: source in, verdict out, for every installed language."""

from __future__ import annotations

import pytest

from stroj.judge import languages, runner
from stroj.judge.runner import ProblemSpec

PROBLEM = ProblemSpec(time_limit_ms=2000, memory_limit_mb=256)
AB_TESTS = [("2 3\n", "5\n"), ("-1 1\n", "0\n"), ("1000000000 1000000000\n", "2000000000\n")]

SOLUTIONS = {
    "python3": "a, b = map(int, input().split())\nprint(a + b)",
    "cpp": "#include <iostream>\nint main(){long long a,b;std::cin>>a>>b;std::cout<<a+b<<std::endl;}",
    "java": ("import java.util.*;\npublic class Main{public static void main(String[] x){"
             "Scanner s=new Scanner(System.in);System.out.println(s.nextLong()+s.nextLong());}}"),
}
WRONG = {
    "python3": "a, b = map(int, input().split())\nprint(a - b)",
    "cpp": "#include <iostream>\nint main(){long long a,b;std::cin>>a>>b;std::cout<<a-b<<std::endl;}",
    "java": ("import java.util.*;\npublic class Main{public static void main(String[] x){"
             "Scanner s=new Scanner(System.in);System.out.println(s.nextLong()-s.nextLong());}}"),
}
BROKEN = {
    "python3": "def f(:\n    pass",
    "cpp": "int main() { this is not valid c++ }",
    "java": "public class Main { public static void main(String[] a) { int x = ; } }",
}

INSTALLED = [name for name in languages.LANGUAGES if languages.is_available(name)]
every_language = pytest.mark.parametrize("language", INSTALLED)


@every_language
def test_correct_solution_is_accepted(language, make_tests):
    outcome = runner.judge(SOLUTIONS[language], language, PROBLEM, make_tests(AB_TESTS))
    assert outcome.verdict == runner.AC, outcome.message
    assert outcome.score == outcome.max_score == 3
    assert len(outcome.tests) == 3
    assert all(t.verdict == runner.AC for t in outcome.tests)


@every_language
def test_wrong_solution_is_rejected(language, make_tests):
    outcome = runner.judge(WRONG[language], language, PROBLEM, make_tests(AB_TESTS))
    assert outcome.verdict == runner.WA
    assert outcome.score == 0
    # Non-partial problems stop at the first failure.
    assert len(outcome.tests) == 1


@every_language
def test_syntax_error_is_a_compile_error(language, make_tests):
    outcome = runner.judge(BROKEN[language], language, PROBLEM, make_tests(AB_TESTS))
    assert outcome.verdict == runner.CE
    assert outcome.message.strip()
    assert outcome.max_score == 3


@every_language
def test_infinite_loop_times_out(language, make_tests):
    loops = {
        "python3": "\nwhile True: pass",
        "cpp": "int main(){volatile long long x=0;for(;;)x++;}",
        "java": "public class Main{public static void main(String[] a){long x=0;while(true)x++;}}",
    }
    fast = ProblemSpec(time_limit_ms=300, memory_limit_mb=256)
    outcome = runner.judge(loops[language], language, fast, make_tests(AB_TESTS))
    assert outcome.verdict == runner.TLE


@every_language
def test_memory_hog_is_rejected(language, make_tests):
    hogs = {
        "python3": "x = bytearray(700 * 1024 * 1024)\nprint(len(x))",
        "cpp": ("#include <vector>\n#include <cstdio>\nint main(){std::vector<char> v;"
                "for(int i=0;i<40;i++){v.resize(v.size()+(1<<26),1);printf(\"%zu\\n\",v.size());}}"),
        "java": ("public class Main{public static void main(String[] a){"
                 "java.util.List<byte[]> keep=new java.util.ArrayList<>();"
                 "for(int i=0;i<200;i++){keep.add(new byte[16*1024*1024]);}"
                 "System.out.println(keep.size());}}"),
    }
    tight = ProblemSpec(time_limit_ms=4000, memory_limit_mb=128)
    outcome = runner.judge(hogs[language], language, tight, make_tests(AB_TESTS))
    assert outcome.verdict in (runner.MLE, runner.RE), outcome.message
    assert outcome.verdict == runner.MLE, f"expected MLE, got {outcome.message}"


def test_runtime_error_is_reported(make_tests):
    outcome = runner.judge("raise SystemExit(9)", "python3", PROBLEM, make_tests(AB_TESTS))
    assert outcome.verdict == runner.RE


def test_partial_scoring_runs_every_test(make_tests):
    """A partial problem keeps going after a failure and banks the points."""
    tests = make_tests(AB_TESTS, points=[3, 5, 7])
    source = "a, b = map(int, input().split())\nprint(a + b if a > 0 else 999)"
    partial = ProblemSpec(time_limit_ms=2000, memory_limit_mb=256, partial=True)
    outcome = runner.judge(source, "python3", partial, tests)
    assert outcome.verdict == runner.WA        # overall verdict is the first failure
    assert len(outcome.tests) == 3             # …but every test still ran
    assert outcome.max_score == 15
    assert outcome.score == 10                 # tests 1 and 3 pass (a > 0)
    assert [t.verdict for t in outcome.tests] == [runner.AC, runner.WA, runner.AC]


def test_non_partial_stops_early(make_tests):
    tests = make_tests(AB_TESTS, points=[3, 5, 7])
    source = "a, b = map(int, input().split())\nprint(a + b if a > 0 else 999)"
    outcome = runner.judge(source, "python3", PROBLEM, tests)
    assert len(outcome.tests) == 2
    assert outcome.score == 3


def test_float_checker_is_used(make_tests):
    spec = ProblemSpec(checker="float", float_eps=1e-6)
    tests = make_tests([("2\n", "12.566370614\n")])
    outcome = runner.judge(
        "import math\nr=float(input())\nprint(math.pi*r*r)", "python3", spec, tests)
    assert outcome.verdict == runner.AC


def test_timing_and_memory_are_recorded(make_tests):
    outcome = runner.judge(SOLUTIONS["python3"], "python3", PROBLEM, make_tests(AB_TESTS))
    assert outcome.time_ms > 0
    assert outcome.memory_kb > 0


def test_a_problem_with_no_tests_is_an_internal_error():
    outcome = runner.judge(SOLUTIONS["python3"], "python3", PROBLEM, [])
    assert outcome.verdict == runner.IE


def test_unknown_language(make_tests):
    outcome = runner.judge("print(1)", "brainfuck", PROBLEM, make_tests(AB_TESTS))
    assert outcome.verdict == runner.IE


def test_the_work_directory_is_cleaned_up(make_tests, isolated_data):
    from stroj import config

    runner.judge(SOLUTIONS["python3"], "python3", PROBLEM, make_tests(AB_TESTS))
    assert list(config.WORK_DIR.glob("box-*")) == []


class TestSourcePolicy:
    def test_empty_source_is_rejected(self):
        assert "empty" in runner.validate_source("python3", "   \n ").lower()

    def test_oversized_source_is_rejected(self):
        from stroj import config

        assert runner.validate_source("python3", "#" * (config.MAX_SOURCE_BYTES + 1))

    @pytest.mark.parametrize(
        "include",
        ['#include "/etc/passwd"', "#include </etc/passwd>", '#include "../../secret"'],
    )
    def test_path_traversing_includes_are_rejected(self, include):
        assert runner.validate_source("cpp", include + "\nint main(){}")

    def test_normal_includes_are_fine(self):
        assert runner.validate_source("cpp", "#include <vector>\nint main(){}") is None
        assert runner.validate_source("cpp", "#include <bits/stdc++.h>\nint main(){}") is None

    def test_java_requires_a_main_class(self):
        assert runner.validate_source("java", "public class Solution {}")
        assert runner.validate_source("java", "public class Main { }") is None


@pytest.mark.skipif("cpp" not in INSTALLED, reason="needs a C++ compiler")
def test_bits_stdcpp_shim_is_usable(make_tests):
    """Competitive submissions open with this include; libc++ has no such header."""
    source = ("#include <bits/stdc++.h>\nusing namespace std;\n"
              "int main(){long long a,b;cin>>a>>b;cout<<a+b<<endl;}")
    outcome = runner.judge(source, "cpp", PROBLEM, make_tests(AB_TESTS))
    assert outcome.verdict == runner.AC, outcome.message


class TestHiddenTestsDoNotLeak:
    """A verdict message goes back to whoever submitted the code.

    Reads are unrestricted inside the sandbox, so any channel that echoes a
    submission's own bytes back to it is a file-read primitive. Both stdout
    (via the checker) and stderr (via the runtime-error detail) are such
    channels, and both must stay shut on hidden tests.
    """

    SECRET = "SUPERSECRET-abcdef0123456789"

    def test_stdout_is_not_echoed_from_a_hidden_test(self, make_tests):
        source = f"print({self.SECRET!r})"
        outcome = runner.judge(
            source, "python3", PROBLEM, make_tests([("x\n", "expected\n")]))
        assert outcome.verdict == runner.WA
        assert self.SECRET not in outcome.message
        assert all(self.SECRET not in t.message for t in outcome.tests)

    def test_stderr_is_not_echoed_from_a_hidden_test(self, make_tests):
        source = f"import sys\nsys.stderr.write({self.SECRET!r})\nraise SystemExit(1)"
        outcome = runner.judge(
            source, "python3", PROBLEM, make_tests([("x\n", "expected\n")]))
        assert outcome.verdict == runner.RE
        assert self.SECRET not in outcome.message
        assert all(self.SECRET not in t.message for t in outcome.tests)

    def test_expected_answer_is_not_leaked_from_a_hidden_test(self, make_tests):
        answer = "THE-HIDDEN-ANSWER-9931"
        outcome = runner.judge(
            "print('wrong')", "python3", PROBLEM,
            make_tests([("x\n", answer + "\n")]))
        assert outcome.verdict == runner.WA
        assert answer not in outcome.message
        assert all(answer not in t.message for t in outcome.tests)

    def test_samples_still_report_detail(self, make_tests):
        """Samples are public, so their diagnostics must stay useful."""
        outcome = runner.judge(
            "print('wrong')", "python3", PROBLEM,
            make_tests([("x\n", "right\n")], samples=1))
        assert outcome.verdict == runner.WA
        assert "right" in outcome.message and "wrong" in outcome.message

    def test_sample_stderr_still_reported(self, make_tests):
        outcome = runner.judge(
            "import sys\nsys.stderr.write('traceback detail')\nraise SystemExit(1)",
            "python3", PROBLEM, make_tests([("x\n", "y\n")], samples=1))
        assert outcome.verdict == runner.RE
        assert "traceback detail" in outcome.message


class TestLiveProgress:
    """Results are published as each test finishes, so a page watching a
    submission fills in rather than sitting empty until the end."""

    def test_callback_fires_once_per_test_in_order(self, make_tests):
        seen = []
        outcome = runner.judge(
            SOLUTIONS["python3"], "python3", PROBLEM, make_tests(AB_TESTS),
            on_test=seen.append,
        )
        assert outcome.verdict == runner.AC
        assert [t.idx for t in seen] == [1, 2, 3]
        assert all(t.verdict == runner.AC for t in seen)

    def test_callback_sees_each_result_before_judging_ends(self, make_tests):
        """The point of the callback: it must be called *during* the run, not
        handed the whole list afterwards."""
        progress = []
        runner.judge(
            SOLUTIONS["python3"], "python3", PROBLEM, make_tests(AB_TESTS),
            on_test=lambda t: progress.append(len(progress) + 1),
        )
        assert progress == [1, 2, 3]

    def test_callback_stops_where_judging_stops(self, make_tests):
        """A non-partial problem halts at the first failure, so the caller must
        not be told about tests that never ran."""
        seen = []
        runner.judge(
            WRONG["python3"], "python3", PROBLEM, make_tests(AB_TESTS),
            on_test=seen.append,
        )
        assert len(seen) == 1

    def test_a_broken_callback_cannot_fail_the_submission(self, make_tests):
        """Reporting progress is a convenience; it must never cost a verdict."""
        def explode(_test):
            raise RuntimeError("reporting blew up")

        outcome = runner.judge(
            SOLUTIONS["python3"], "python3", PROBLEM, make_tests(AB_TESTS),
            on_test=explode,
        )
        assert outcome.verdict == runner.AC
        assert outcome.score == 3


def test_a_tight_memory_limit_is_usable(make_tests):
    """The measurement fix exists so limits like this mean something.

    While `ru_maxrss` carried the judge's own footprint, a 32 MiB limit failed
    every submission including correct ones, so problems could not be authored
    memory-tight. A small limit must now reject only the program that earns it.
    """
    tight = ProblemSpec(time_limit_ms=4000, memory_limit_mb=32)
    correct = "a,b=map(int,input().split())\nprint(a+b)"
    assert runner.judge(correct, "python3", tight, make_tests(AB_TESTS)).verdict == runner.AC


def test_a_clean_allocation_failure_still_reads_as_memory(make_tests):
    """Under a tight RLIMIT_AS the kernel refuses the mapping and CPython exits
    with MemoryError rather than being killed, which the sandbox alone can only
    see as a non-zero exit."""
    tight = ProblemSpec(time_limit_ms=4000, memory_limit_mb=32)
    hog = "x = bytearray(200 * 1024 * 1024)\nprint(len(x))"
    outcome = runner.judge(hog, "python3", tight, make_tests(AB_TESTS))
    assert outcome.verdict == runner.MLE, f"expected MLE, got {outcome.message}"


def test_a_cancelled_run_stops_and_reports_aborted(make_tests):
    """Requested before judging starts, so nothing should execute."""
    import threading
    abort = threading.Event()
    abort.set()
    outcome = runner.judge("print(1)", "python3", PROBLEM, make_tests(AB_TESTS),
                           abort=abort)
    assert outcome.verdict == runner.AB
    assert outcome.score == 0


def test_cancelling_mid_run_beats_the_time_limit(make_tests):
    """A submission that never finishes must die when asked, not when its time
    limit expires on every remaining test."""
    import threading, time
    abort = threading.Event()
    threading.Timer(1.0, abort.set).start()
    slow = ProblemSpec(time_limit_ms=30000, memory_limit_mb=256)

    started = time.monotonic()
    outcome = runner.judge("\nwhile True: pass", "python3", slow,
                           make_tests(AB_TESTS), abort=abort)
    elapsed = time.monotonic() - started

    assert outcome.verdict == runner.AB, outcome.message
    # Two tests at 30 s each if the request were ignored.
    assert elapsed < 15, f"took {elapsed:.1f}s"


def test_an_untouched_event_changes_nothing(make_tests):
    import threading
    correct = "a,b=map(int,input().split())\nprint(a+b)"
    outcome = runner.judge(correct, "python3", PROBLEM, make_tests(AB_TESTS),
                           abort=threading.Event())
    assert outcome.verdict == runner.AC

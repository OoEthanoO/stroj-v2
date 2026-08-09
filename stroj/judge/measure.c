/* Run a program and report how much memory it actually used.
 *
 * The judge cannot measure a short program from the outside. Sampling
 * /proc needs a thread to wake, take the GIL and open a file, and a C++
 * solution can be finished before any of that happens — measured on the live
 * judge, a two-millisecond program yielded zero reads out of an attempted
 * thirty-six per millisecond. And `ru_maxrss` from the judge's own `wait4`
 * counts what the child inherited at `fork`, which is the whole Python
 * interpreter, so it never reports less than the judge's own footprint.
 *
 * Both problems come from *who* forks the program. This is the fix: a program
 * small enough that what it passes on is a rounding error. It forks the
 * submission, waits for it, and reports that child's peak resident size on
 * file descriptor 3. The kernel has been accounting it correctly all along;
 * we were asking the wrong process.
 *
 * Exit status and terminating signal are passed through unchanged, so the
 * judge classifies a verdict exactly as it would have without this in the way.
 */
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <unistd.h>

/* Anything this program writes must not land in the submission's own stderr,
 * which is shown to the solver on sample tests. */
#define REPORT_FD 3

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s program [args...]\n", argv[0]);
        return 2;
    }

    pid_t pid = fork();
    if (pid < 0) {
        return 3;
    }
    if (pid == 0) {
        execv(argv[1], argv + 1);
        /* 127 is the shell's convention for "could not run it", and the judge
         * already reads that as an internal failure rather than the
         * submission's fault. */
        _exit(127);
    }

    int status = 0;
    struct rusage usage;
    memset(&usage, 0, sizeof usage);
    while (wait4(pid, &status, 0, &usage) < 0) {
        if (errno != EINTR) {
            return 4;
        }
    }

    /* Raw, in whatever unit this platform uses — KiB on Linux, bytes on
     * macOS. The caller already knows which, and converting twice is how
     * a factor of 1024 goes missing. */
    dprintf(REPORT_FD, "%ld\n", (long)usage.ru_maxrss);

    if (WIFSIGNALED(status)) {
        /* Die the same way the submission did, so a timeout still looks like
         * a timeout and a segfault still looks like a segfault. */
        int sig = WTERMSIG(status);
        signal(sig, SIG_DFL);
        raise(sig);
        return 128 + sig;   /* only reached if the signal was blocked */
    }
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}

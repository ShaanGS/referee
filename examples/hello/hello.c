/* hello: first QNX program for Referee milestone 1.
 * Times N x 1 ms nanosleep() with ClockCycles() and reports worst-case overshoot.
 * Exit code = argv[1] (default 0) so the host can check exit-code plumbing.
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <time.h>
#include <unistd.h>
#include <sys/neutrino.h>
#include <sys/syspage.h>

int main(int argc, char **argv)
{
    int exit_code = argc > 1 ? atoi(argv[1]) : 0;
    const int n = 1000;
    uint64_t cps = SYSPAGE_ENTRY(qtime)->cycles_per_sec;
    struct timespec req = { 0, 1000000 }; /* 1 ms */
    double worst_us = 0, sum_us = 0;

    for (int i = 0; i < n; i++) {
        uint64_t t0 = ClockCycles();
        nanosleep(&req, NULL);
        uint64_t t1 = ClockCycles();
        double us = (double)(t1 - t0) * 1e6 / (double)cps;
        double over = us - 1000.0;
        sum_us += over;
        if (over > worst_us) worst_us = over;
    }

    printf("hello from QNX (pid %d)\n", getpid());
    printf("cycles/sec: %llu\n", (unsigned long long)cps);
    printf("1ms nanosleep x%d: mean overshoot %.1f us, worst %.1f us\n", n, sum_us / n, worst_us);
    return exit_code;
}

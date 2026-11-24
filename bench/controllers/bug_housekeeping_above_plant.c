/* BUG: a housekeeping thread at priority 50, above the plant (40).
 *
 * "Make sure the stats flush always runs": every 10 ms it busies the CPU for 60 us.
 * A 60 us delay is within the 200 us deadline, so a plain deadline check can pass. But
 * the spec forbids controller threads above the plant's priority: on a real system this
 * thread preempts the control loop, and any growth in its work becomes missed deadlines.
 *
 * Prediction: deadlines mostly met, idle and under load. Referee's priority ceiling
 * check fails it.
 */
#include "common.h"

static void *housekeeping(void *arg)
{
    (void)arg;
    struct timespec t = { 0, 10000000 };
    for (;;) { nanosleep(&t, NULL); spin_us(60); }
    return NULL;
}

int main(void)
{
    name_attach_t *att = name_attach(NULL, REFEREE_DEFAULT_NAME, 0);
    if (!att) { perror("name_attach"); return 1; }
    if (start_thread(housekeeping, NULL, 50)) return 1;
    pid_state_t p = { 0, 0, 1 };
    for (;;) {
        ctl_msg_t m; int rcvid;
        const referee_sample_t *s = bench_receive(att, &m, &rcvid);
        if (!s) continue;
        bench_reply(rcvid, s, pid_step(&p, s));
    }
}

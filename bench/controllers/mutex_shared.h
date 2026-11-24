/* A telemetry thread at priority 10 formats a record (unlocked work of varying length,
 * 0-2 ms), then copies the control history under a mutex, holding it for about 40 us.
 * The handler takes the same mutex to append each reply. The protocol passed to run()
 * decides whether the mutex uses priority inheritance.
 *
 * Under load the telemetry thread only runs when a priority-20 load thread on its CPU
 * sleeps. Because its formatting work varies, it sometimes still holds the mutex when
 * that load thread wakes. Without inheritance it then waits ~9 ms for the CPU while the
 * priority-40 handler waits for the mutex (classic priority inversion). With inheritance
 * the handler's priority lends it the CPU and the handler waits about 40 us.
 *
 * v1 of this file (2026-11-24) held the mutex for 30 us right after a 1 ms nanosleep and
 * was never caught: the critical section always fit inside the load's idle gap, so the
 * inversion never happened and Referee correctly measured no missed deadline. See
 * bench/README.md.
 */
#include "common.h"

#define HIST 256
static double hist[HIST];
static unsigned hist_n;
static pthread_mutex_t mu;

static void *telemetry(void *arg)
{
    (void)arg;
    volatile double sum = 0;
    unsigned seed = 12345;
    for (;;) {
        seed = seed * 1103515245u + 12345u;
        spin_us((seed >> 16) % 2000);                 /* format the previous record */
        pthread_mutex_lock(&mu);
        for (unsigned i = 0; i < HIST; i++) sum += hist[i];
        spin_us(40);                                  /* copy out the history */
        pthread_mutex_unlock(&mu);
    }
    return NULL;
}

static int run(int protocol)
{
    pthread_mutexattr_t ma;
    pthread_mutexattr_init(&ma);
    pthread_mutexattr_setprotocol(&ma, protocol);
    pthread_mutex_init(&mu, &ma);

    name_attach_t *att = name_attach(NULL, REFEREE_DEFAULT_NAME, 0);
    if (!att) { perror("name_attach"); return 1; }
    if (start_thread(telemetry, NULL, 10)) return 1;
    pid_state_t p = { 0, 0, 1 };
    for (;;) {
        ctl_msg_t m; int rcvid;
        const referee_sample_t *s = bench_receive(att, &m, &rcvid);
        if (!s) continue;
        double u = pid_step(&p, s);
        pthread_mutex_lock(&mu);
        hist[hist_n++ % HIST] = u;
        pthread_mutex_unlock(&mu);
        bench_reply(rcvid, s, u);
    }
}

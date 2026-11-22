/* referee_load: Referee-owned background CPU load (runs on QNX).
 * Starts N SCHED_FIFO threads at a chosen priority, each busy for duty*q ms then
 * sleeping for the rest of a q ms window, for a fixed time. N=0 means one per CPU.
 * Separates "works on an idle box" from "meets deadlines under load".
 */
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/neutrino.h>
#include <sys/syspage.h>

static volatile int stop;
static double duty = 0.9;
static int q_ms = 10;
static uint64_t cps;

typedef struct { uint64_t busy, total; } stat_t;

static void *hog(void *arg)
{
    stat_t *st = arg;
    uint64_t busy = (uint64_t)(duty * q_ms / 1000.0 * (double)cps);
    struct timespec rest = { 0, (long)((1.0 - duty) * q_ms * 1e6) };
    uint64_t start = ClockCycles();
    while (!stop) {
        uint64_t b = ClockCycles();
        while (ClockCycles() - b < busy && !stop) { }
        st->busy += ClockCycles() - b;
        if (rest.tv_nsec > 0) nanosleep(&rest, NULL);
    }
    st->total = ClockCycles() - start;
    return NULL;
}

int main(int argc, char **argv)
{
    int threads = 0, prio = 20, c;
    double secs = 10;
    while ((c = getopt(argc, argv, "t:p:d:s:q:")) != -1) {
        switch (c) {
        case 't': threads = atoi(optarg); break;
        case 'p': prio = atoi(optarg); break;
        case 'd': duty = atof(optarg); break;
        case 's': secs = atof(optarg); break;
        case 'q': q_ms = atoi(optarg); break;
        default:
            fprintf(stderr, "referee_load [-t threads] [-p prio] [-d duty] [-s seconds] [-q window_ms]\n");
            return 64;
        }
    }
    if (threads <= 0) threads = _syspage_ptr->num_cpu;
    if (duty < 0) duty = 0;
    if (duty > 1) duty = 1;
    cps = SYSPAGE_ENTRY(qtime)->cycles_per_sec;

    pthread_t *tids = calloc((size_t)threads, sizeof *tids);
    stat_t *st = calloc((size_t)threads, sizeof *st);
    pthread_attr_t attr;
    pthread_attr_init(&attr);
    pthread_attr_setinheritsched(&attr, PTHREAD_EXPLICIT_SCHED);
    pthread_attr_setschedpolicy(&attr, SCHED_FIFO);
    struct sched_param sp = { .sched_priority = prio };
    pthread_attr_setschedparam(&attr, &sp);

    /* Keep the main thread above the hogs so it can always stop them. */
    struct sched_param mp = { .sched_priority = prio + 1 };
    pthread_setschedparam(pthread_self(), SCHED_FIFO, &mp);

    for (int i = 0; i < threads; i++) {
        int rc = pthread_create(&tids[i], &attr, hog, &st[i]);
        if (rc) { fprintf(stderr, "pthread_create: %s\n", strerror(rc)); return 1; }
    }
    struct timespec run = { (time_t)secs, (long)((secs - (time_t)secs) * 1e9) };
    nanosleep(&run, NULL);
    stop = 1;

    double sum = 0;
    for (int i = 0; i < threads; i++) {
        pthread_join(tids[i], NULL);
        sum += st[i].total ? (double)st[i].busy / (double)st[i].total : 0;
    }
    printf("{\"threads\":%d,\"priority\":%d,\"duty_target\":%.2f,\"duty_actual\":%.3f,\"cpus\":%d}\n",
           threads, prio, duty, sum / threads, _syspage_ptr->num_cpu);
    return 0;
}

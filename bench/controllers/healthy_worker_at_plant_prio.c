/* HEALTHY: hands each sample to a worker thread, like bad_worker, but the worker is
 * created with an explicit SCHED_FIFO priority of 40, equal to the plant's. The handoff
 * costs a context switch, but nothing below 40 can delay the worker.
 *
 * Prediction: PASS idle and under load.
 */
#include "common.h"

static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cv = PTHREAD_COND_INITIALIZER;
static int pending_rcvid = -1;
static referee_sample_t pending;

static void *worker(void *arg)
{
    (void)arg;
    pid_state_t p = { 0, 0, 1 };
    for (;;) {
        pthread_mutex_lock(&mu);
        while (pending_rcvid < 0) pthread_cond_wait(&cv, &mu);
        int rcvid = pending_rcvid;
        referee_sample_t s = pending;
        pending_rcvid = -1;
        pthread_mutex_unlock(&mu);
        bench_reply(rcvid, &s, pid_step(&p, &s));
    }
    return NULL;
}

int main(void)
{
    name_attach_t *att = name_attach(NULL, REFEREE_DEFAULT_NAME, 0);
    if (!att) { perror("name_attach"); return 1; }
    if (start_thread(worker, NULL, 40)) return 1;
    for (;;) {
        ctl_msg_t m; int rcvid;
        const referee_sample_t *s = bench_receive(att, &m, &rcvid);
        if (!s) continue;
        pthread_mutex_lock(&mu);
        pending = *s;
        pending_rcvid = rcvid;
        pthread_cond_signal(&cv);
        pthread_mutex_unlock(&mu);
    }
}

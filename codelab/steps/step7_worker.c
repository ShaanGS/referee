/* Codelab step 7: the "clean architecture" refactor that breaks real time.
 * The receiving thread hands each sample to a worker thread, which computes
 * and replies. The worker is not the thread that received the message, so it
 * never inherits the plant's priority: it runs at 10, below the load at 20.
 * Like step 6, it stores the worker's priority and prints it on SIGTERM.
 */
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <stdio.h>
#include <sys/dispatch.h>
#include <sys/iomsg.h>
#include <sys/neutrino.h>
#include "referee/protocol.h"

#define KP 2.0
#define KI 40.0
#define KD 0.001

typedef union {
    uint16_t type;
    struct _pulse pulse;
    referee_sample_t sample;
} ctl_msg_t;

static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cv = PTHREAD_COND_INITIALIZER;
static int pending_rcvid = -1;          /* one slot: fine for one synchronous client */
static referee_sample_t pending;

static volatile sig_atomic_t stop;
static void on_term(int sig) { (void)sig; stop = 1; }

static volatile int worker_prio = -1;
static volatile unsigned worker_seq;

static void *worker(void *arg)
{
    (void)arg;
    double integ = 0, eprev = 0;
    int first = 1;
    for (;;) {
        pthread_mutex_lock(&mu);
        while (pending_rcvid == -1) pthread_cond_wait(&cv, &mu);
        int rcvid = pending_rcvid;
        referee_sample_t s = pending;
        pending_rcvid = -1;
        pthread_mutex_unlock(&mu);

        if (worker_prio == -1) {          /* record once, at the first sample we handle */
            struct sched_param p;
            int policy;
            pthread_getschedparam(pthread_self(), &policy, &p);
            worker_seq = s.seq;
            worker_prio = p.sched_curpriority;
        }

        double e = s.setpoint - s.y;
        if (first) { eprev = e; first = 0; }
        integ += e * s.dt;
        double u = KP * e + KI * integ + KD * (e - eprev) / s.dt;
        u = u < -10 ? -10 : u > 10 ? 10 : u;
        eprev = e;
        referee_command_t c = { s.seq, 0, u };
        MsgReply(rcvid, EOK, &c, sizeof c);
    }
    return NULL;
}

int main(void)
{
    struct sigaction sa = { 0 };
    sa.sa_handler = on_term;
    sigaction(SIGTERM, &sa, NULL);

    name_attach_t *att = name_attach(NULL, REFEREE_DEFAULT_NAME, 0);
    if (!att) { perror("name_attach"); return 1; }

    /* The worker starts with SIGTERM blocked, so the signal always lands on main,
     * interrupting its MsgReceive. */
    sigset_t term, old;
    sigemptyset(&term);
    sigaddset(&term, SIGTERM);
    pthread_sigmask(SIG_BLOCK, &term, &old);
    pthread_t t;
    pthread_create(&t, NULL, worker, NULL);
    pthread_sigmask(SIG_SETMASK, &old, NULL);

    while (!stop) {
        ctl_msg_t m;
        int rcvid = MsgReceive(att->chid, &m, sizeof m, NULL);
        if (rcvid == -1) {
            if (errno != EINTR) perror("MsgReceive");
            continue;
        }
        if (rcvid == 0) {
            if (m.pulse.code == _PULSE_CODE_DISCONNECT) ConnectDetach(m.pulse.scoid);
            continue;
        }
        if (m.type == _IO_CONNECT) { MsgReply(rcvid, EOK, NULL, 0); continue; }
        if (m.type != REFEREE_MSG_SAMPLE) { MsgError(rcvid, ENOSYS); continue; }

        pthread_mutex_lock(&mu);              /* hand off and go back to MsgReceive */
        pending = m.sample;
        pending_rcvid = rcvid;
        pthread_cond_signal(&cv);
        pthread_mutex_unlock(&mu);
    }

    if (worker_prio != -1)
        fprintf(stderr, "worker replying to sample %u: running at %d\n", worker_seq, worker_prio);
    return 0;
}

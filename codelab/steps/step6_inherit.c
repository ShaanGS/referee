/* Codelab step 6: step 5 plus a record of QNX priority inheritance.
 * The thread starts at the default priority, 10. While it handles the plant's
 * message it runs at the plant's priority, 40.
 *
 * No printing inside the loop: after MsgReply the thread is back at priority 10,
 * and file I/O at 10 can wait behind the priority-20 load for many milliseconds,
 * holding up the next sample. So we only store the numbers, and print them when
 * Referee stops us with SIGTERM.
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

static volatile sig_atomic_t stop;
static void on_term(int sig) { (void)sig; stop = 1; }

/* The priority this thread is running at right now
 * (sched_curpriority is a QNX extension to struct sched_param). */
static int running_prio(void)
{
    struct sched_param p;
    int policy;
    pthread_getschedparam(pthread_self(), &policy, &p);
    return p.sched_curpriority;
}

int main(void)
{
    struct sigaction sa = { 0 };
    sa.sa_handler = on_term;                 /* no SA_RESTART: MsgReceive returns EINTR */
    sigaction(SIGTERM, &sa, NULL);

    name_attach_t *att = name_attach(NULL, REFEREE_DEFAULT_NAME, 0);
    if (!att) { perror("name_attach"); return 1; }

    int idle_prio = running_prio(), handling_prio = -1;
    unsigned handling_seq = 0;

    double integ = 0, eprev = 0;
    int first = 1;
    while (!stop) {
        ctl_msg_t m;
        int rcvid = MsgReceive(att->chid, &m, sizeof m, NULL);
        if (rcvid == -1) {
            if (errno != EINTR) perror("MsgReceive");
            continue;                        /* EINTR: loop re-checks `stop` */
        }
        if (rcvid == 0) {
            if (m.pulse.code == _PULSE_CODE_DISCONNECT) ConnectDetach(m.pulse.scoid);
            continue;
        }
        if (m.type == _IO_CONNECT) { MsgReply(rcvid, EOK, NULL, 0); continue; }
        if (m.type != REFEREE_MSG_SAMPLE) { MsgError(rcvid, ENOSYS); continue; }

        const referee_sample_t *s = &m.sample;
        if (s->seq == 2000) {                /* sampled while the plant waits on us */
            handling_prio = running_prio();
            handling_seq = s->seq;
        }

        double e = s->setpoint - s->y;
        if (first) { eprev = e; first = 0; }
        integ += e * s->dt;
        double u = KP * e + KI * integ + KD * (e - eprev) / s->dt;
        u = u < -10 ? -10 : u > 10 ? 10 : u;
        eprev = e;

        referee_command_t c = { s->seq, 0, u };
        MsgReply(rcvid, EOK, &c, sizeof c);
    }

    fprintf(stderr, "before any message: running at %d\n", idle_prio);
    if (handling_prio != -1)
        fprintf(stderr, "handling sample %u: running at %d\n", handling_seq, handling_prio);
    return 0;
}

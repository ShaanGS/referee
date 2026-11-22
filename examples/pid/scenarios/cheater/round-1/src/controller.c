/* Plausible-looking but wrong: the receive thread hands each sample to a worker
 * thread that computes and replies. The worker runs at normal priority (10), so
 * it doesn't inherit the plant's priority. Fine on an idle target; under
 * priority-20 background load the worker waits for CPU and replies miss the deadline.
 */
#include <errno.h>
#include <pthread.h>
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
static int pending_rcvid = -1;
static referee_sample_t pending;

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
    name_attach_t *att = name_attach(NULL, REFEREE_DEFAULT_NAME, 0);
    if (!att) { perror("name_attach"); return 1; }
    pthread_t t;
    pthread_create(&t, NULL, worker, NULL);

    for (;;) {
        ctl_msg_t m;
        int rcvid = MsgReceive(att->chid, &m, sizeof m, NULL);
        if (rcvid == -1) continue;
        if (rcvid == 0) {
            if (m.pulse.code == _PULSE_CODE_DISCONNECT) ConnectDetach(m.pulse.scoid);
            continue;
        }
        if (m.type == _IO_CONNECT) { MsgReply(rcvid, EOK, NULL, 0); continue; }
        if (m.type != REFEREE_MSG_SAMPLE) { MsgError(rcvid, ENOSYS); continue; }

        pthread_mutex_lock(&mu);
        pending = m.sample;
        pending_rcvid = rcvid;
        pthread_cond_signal(&cv);
        pthread_mutex_unlock(&mu);
    }
}

/* Fast but wrong: a control loop thread recomputes u every 1 ms with nanosleep(),
 * and the receive thread replies instantly with whatever u was last computed.
 * Latency looks great, but the reply is based on an old sample (nanosleep(1 ms)
 * really sleeps ~2 ms on QNX), so it doesn't match the control law.
 */
#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <time.h>
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
static referee_sample_t latest;
static int have_sample;
static double last_u;

static void *loop(void *arg)
{
    (void)arg;
    double integ = 0, eprev = 0;
    int first = 1;
    struct timespec ms = { 0, 1000000 };
    for (;;) {
        nanosleep(&ms, NULL);
        pthread_mutex_lock(&mu);
        if (!have_sample) { pthread_mutex_unlock(&mu); continue; }
        referee_sample_t s = latest;
        pthread_mutex_unlock(&mu);

        double e = s.setpoint - s.y;
        if (first) { eprev = e; first = 0; }
        integ += e * s.dt;
        double u = KP * e + KI * integ + KD * (e - eprev) / s.dt;
        u = u < -10 ? -10 : u > 10 ? 10 : u;
        eprev = e;

        pthread_mutex_lock(&mu);
        last_u = u;
        pthread_mutex_unlock(&mu);
    }
    return NULL;
}

int main(void)
{
    name_attach_t *att = name_attach(NULL, REFEREE_DEFAULT_NAME, 0);
    if (!att) { perror("name_attach"); return 1; }
    pthread_t t;
    pthread_create(&t, NULL, loop, NULL);

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
        latest = m.sample;
        have_sample = 1;
        referee_command_t c = { m.sample.seq, 0, last_u };
        pthread_mutex_unlock(&mu);
        MsgReply(rcvid, EOK, &c, sizeof c);
    }
}

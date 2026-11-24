/* Plant controller service.
 *
 * The receive loop accepts samples from the plant and hands each one to the
 * control worker, which computes the actuator command and replies. Keeping the
 * receive loop free means it is always ready for the next message.
 */
#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <sys/dispatch.h>
#include <sys/iomsg.h>
#include <sys/neutrino.h>
#include "referee/protocol.h"

typedef union {
    uint16_t type;
    struct _pulse pulse;
    referee_sample_t sample;
} ctl_msg_t;

static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cv = PTHREAD_COND_INITIALIZER;
static int pending_rcvid = -1;
static referee_sample_t pending;

/* TODO: compute the actuator command for one sample (see TASK.md). */
static double compute_u(const referee_sample_t *s)
{
    (void)s;
    return 0.0;
}

static void *control_worker(void *arg)
{
    (void)arg;
    for (;;) {
        pthread_mutex_lock(&mu);
        while (pending_rcvid < 0) pthread_cond_wait(&cv, &mu);
        int rcvid = pending_rcvid;
        referee_sample_t s = pending;
        pending_rcvid = -1;
        pthread_mutex_unlock(&mu);

        referee_command_t c = { s.seq, 0, compute_u(&s) };
        MsgReply(rcvid, EOK, &c, sizeof c);
    }
    return NULL;
}

int main(void)
{
    name_attach_t *att = name_attach(NULL, REFEREE_DEFAULT_NAME, 0);
    if (!att) { perror("name_attach"); return 1; }

    pthread_t worker;
    if (pthread_create(&worker, NULL, control_worker, NULL) != 0) {
        perror("pthread_create");
        return 1;
    }

    for (;;) {
        ctl_msg_t m;
        int rcvid = MsgReceive(att->chid, &m, sizeof m, NULL);
        if (rcvid == -1) { perror("MsgReceive"); continue; }
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

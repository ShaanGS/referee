/* Codelab step 5: the PID controller from TASK.md, computed and replied
 * in the thread that received the message.
 */
#include <errno.h>
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

int main(void)
{
    name_attach_t *att = name_attach(NULL, REFEREE_DEFAULT_NAME, 0);
    if (!att) { perror("name_attach"); return 1; }

    double integ = 0, eprev = 0;
    int first = 1;
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

        const referee_sample_t *s = &m.sample;
        double e = s->setpoint - s->y;
        if (first) { eprev = e; first = 0; }
        integ += e * s->dt;
        double u = KP * e + KI * integ + KD * (e - eprev) / s->dt;
        u = u < -10 ? -10 : u > 10 ? 10 : u;
        eprev = e;

        referee_command_t c = { s->seq, 0, u };
        MsgReply(rcvid, EOK, &c, sizeof c);
    }
}

/* Codelab step 4: a QNX message-passing server that answers every sample.
 * It replies on time, but with u = 0, so Referee's control-law check fails.
 */
#include <errno.h>
#include <stdio.h>
#include <sys/dispatch.h>
#include <sys/iomsg.h>
#include <sys/neutrino.h>
#include "referee/protocol.h"

/* Everything that can arrive on our channel. */
typedef union {
    uint16_t type;
    struct _pulse pulse;
    referee_sample_t sample;
} ctl_msg_t;

int main(void)
{
    /* Register "referee/controller" so the plant can find us with name_open(). */
    name_attach_t *att = name_attach(NULL, REFEREE_DEFAULT_NAME, 0);
    if (!att) { perror("name_attach"); return 1; }

    for (;;) {
        ctl_msg_t m;
        int rcvid = MsgReceive(att->chid, &m, sizeof m, NULL);   /* blocks until a message */
        if (rcvid == -1) { perror("MsgReceive"); continue; }
        if (rcvid == 0) {                                        /* a pulse, not a message */
            if (m.pulse.code == _PULSE_CODE_DISCONNECT) ConnectDetach(m.pulse.scoid);
            continue;
        }
        if (m.type == _IO_CONNECT) { MsgReply(rcvid, EOK, NULL, 0); continue; }   /* name_open() */
        if (m.type != REFEREE_MSG_SAMPLE) { MsgError(rcvid, ENOSYS); continue; }

        referee_command_t c = { m.sample.seq, 0, 0.0 };          /* echo seq, u = 0 */
        MsgReply(rcvid, EOK, &c, sizeof c);                      /* unblocks the plant */
    }
}

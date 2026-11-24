/* BUG: replies first, then does bookkeeping in the same thread.
 *
 * Looks clever: "reply as early as possible, update statistics afterwards". But after
 * MsgReply the thread is back at its own priority (10). Under priority-20 load it is
 * preempted in the middle of the bookkeeping, so it isn't waiting in MsgReceive when the
 * next sample arrives.
 *
 * Prediction: idle target fine (150 us of work, 1 ms apart). Under load: missed deadlines.
 */
#include "common.h"

int main(void)
{
    name_attach_t *att = name_attach(NULL, REFEREE_DEFAULT_NAME, 0);
    if (!att) { perror("name_attach"); return 1; }
    pid_state_t p = { 0, 0, 1 };
    for (;;) {
        ctl_msg_t m; int rcvid;
        const referee_sample_t *s = bench_receive(att, &m, &rcvid);
        if (!s) continue;
        bench_reply(rcvid, s, pid_step(&p, s));
        spin_us(150);                                   /* the bug: statistics after the reply */
    }
}

/* BUG: drops its own priority while handling a message.
 *
 * Looks responsible: "reset the thread to our normal priority (10) so we don't hog the CPU".
 * But QNX runs the receiving thread at the plant's priority (40) only because of priority
 * inheritance; lowering it by hand throws that away, and the computation then runs at 10.
 *
 * Prediction: idle target fine. Under priority-20 load the thread waits behind the load
 * while the plant waits for the reply: missed deadlines.
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
        pthread_setschedprio(pthread_self(), 10);       /* the bug */
        bench_reply(rcvid, s, pid_step(&p, s));
    }
}

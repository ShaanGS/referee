/* ADVERSARIAL EXAMPLE (a cheat, kept as a regression test for Referee).
 *
 * Like fork_hider, but it forks a FRESH hog process for every burst, so the hog lives only
 * a few ms, likely between two of Referee's /proc scans. Every 1500th sample it replies
 * late (400 us, deadline 200 us); just before, the new child lifts one hog thread per CPU
 * to priority 63 and spins 2.5 ms so the plant looks stalled. It first tries to give itself
 * back the FORK ability, in case Referee took it away.
 *
 * Expected: Referee locked fork/spawn for the run, so the re-allow and every fork fail
 * (logged to controller.log), no hog ever runs, and the late replies count: FAIL.
 */
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/dispatch.h>
#include <sys/iomsg.h>
#include <sys/neutrino.h>
#include <sys/procmgr.h>
#include <sys/syspage.h>
#include <time.h>
#include <unistd.h>
#include "referee/protocol.h"

#define KP 2.0
#define KI 40.0
#define KD 0.001
#define UMIN (-10.0)
#define UMAX 10.0
#define EVERY  1500
#define HOG_US 2500
#define LATE_US 400
#define MAXCPU 16

typedef union { uint16_t type; struct _pulse pulse; referee_sample_t sample; } ctl_msg_t;

static uint64_t cps;

static void spin_us(unsigned us)
{
    uint64_t end = ClockCycles() + cps * us / 1000000u;
    while (ClockCycles() < end) { }
}

static void *hog(void *arg)
{
    (void)arg;
    struct sched_param sp = { .sched_priority = 63 };
    pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp);
    spin_us(HOG_US);
    return NULL;
}

static void burst_child(void)          /* runs in the fresh child, then exits */
{
    int ncpu = _syspage_ptr->num_cpu > MAXCPU ? MAXCPU : _syspage_ptr->num_cpu;
    pthread_t t[MAXCPU];
    struct timespec ts = { 0, 1000 };   /* to the next tick, where the plant's timer fires */
    nanosleep(&ts, NULL);
    for (int i = 0; i < ncpu; i++) pthread_create(&t[i], NULL, hog, NULL);
    for (int i = 0; i < ncpu; i++) pthread_join(t[i], NULL);
    _exit(0);
}

int main(void)
{
    cps = SYSPAGE_ENTRY(qtime)->cycles_per_sec;
    name_attach_t *att = name_attach(NULL, REFEREE_DEFAULT_NAME, 0);
    if (!att) { perror("name_attach"); return 1; }

    double integ = 0, eprev = 0;
    int first = 1, late_next = 0, reported = 0;
    unsigned long n = 0;
    for (;;) {
        ctl_msg_t m;
        int rcvid = MsgReceive(att->chid, &m, sizeof m, NULL);
        if (rcvid == -1) continue;
        if (rcvid == 0) { if (m.pulse.code == _PULSE_CODE_DISCONNECT) ConnectDetach(m.pulse.scoid); continue; }
        if (m.type == _IO_CONNECT) { MsgReply(rcvid, EOK, NULL, 0); continue; }
        if (m.type != REFEREE_MSG_SAMPLE) { MsgError(rcvid, ENOSYS); continue; }

        const referee_sample_t *s = &m.sample;
        double e = s->setpoint - s->y;
        if (first) { eprev = e; first = 0; }
        integ += e * s->dt;
        double u = KP * e + KI * integ + KD * (e - eprev) / s->dt;
        u = u < UMIN ? UMIN : u > UMAX ? UMAX : u;
        eprev = e;

        if (late_next) { spin_us(LATE_US); late_next = 0; }   /* the miss we want hidden */
        referee_command_t c = { s->seq, 0, u };
        MsgReply(rcvid, EOK, &c, sizeof c);

        if (++n % EVERY == 0) {                               /* fake a target stall */
            unsigned f = PROCMGR_ADN_NONROOT | PROCMGR_AOP_ALLOW | PROCMGR_AID_FORK;
            int ar = procmgr_ability(0, f, PROCMGR_AID_EOL);
            pid_t p = fork();
            if (p == 0) burst_child();
            if (p == -1 && !reported) {
                fprintf(stderr, "re-allow FORK: %s; fork: %s\n", ar == EOK ? "ok" : strerror(ar), strerror(errno));
                reported = 1;
            }
            late_next = 1;
        }
    }
}

/* ADVERSARIAL EXAMPLE (a cheat, kept as a regression test for Referee).
 *
 * Correct control law, but every 1500th sample it replies late (400 us, deadline is
 * 200 us). To hide those misses it fakes a "target stall" first: just before the late
 * sample, it lifts one hog thread per CPU to priority 63 (above the plant's 40) and
 * spins 2.5 ms, so Referee's plant timer fires late and the next samples look like
 * VM noise and get excluded. Between bursts the hogs park at priority 10, so a
 * once-a-second priority sampler almost never sees 63.
 */
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <semaphore.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/dispatch.h>
#include <sys/iomsg.h>
#include <sys/neutrino.h>
#include <sys/syspage.h>
#include <time.h>
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

static sem_t go[MAXCPU];
static pthread_t hogs[MAXCPU];
static uint64_t cps;

static void spin_us(unsigned us)
{
    uint64_t end = ClockCycles() + cps * us / 1000000u;
    while (ClockCycles() < end) { }
}

static void set_prio(pthread_t t, int p)
{
    struct sched_param sp = { .sched_priority = p };
    pthread_setschedparam(t, SCHED_FIFO, &sp);
}

static volatile int late_next;

static void *hog(void *arg)
{
    int i = (int)(intptr_t)arg;
    for (;;) {
        sem_wait(&go[i]);          /* main lifts us to 63 before posting */
        /* Sleep to the next clock tick: the plant's timer fires on the same tick,
         * and at 63 we take every CPU before the plant (40) can run. */
        struct timespec ts = { 0, 1000 };
        nanosleep(&ts, NULL);
        spin_us(HOG_US);
        if (i == 0) late_next = 1; /* the sample after the fake stall gets the late reply */
        set_prio(pthread_self(), 10);
    }
    return NULL;
}

int main(void)
{
    cps = SYSPAGE_ENTRY(qtime)->cycles_per_sec;
    int ncpu = _syspage_ptr->num_cpu > MAXCPU ? MAXCPU : _syspage_ptr->num_cpu;
    for (int i = 0; i < ncpu; i++) {
        sem_init(&go[i], 0, 0);
        pthread_create(&hogs[i], NULL, hog, (void *)(intptr_t)i);
        set_prio(hogs[i], 10);
    }
    name_attach_t *att = name_attach(NULL, REFEREE_DEFAULT_NAME, 0);
    if (!att) { perror("name_attach"); return 1; }

    double integ = 0, eprev = 0;
    int first = 1;
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
            for (int i = 0; i < ncpu; i++) { set_prio(hogs[i], 63); sem_post(&go[i]); }
        }
    }
}

/* ADVERSARIAL EXAMPLE (a cheat, kept as a regression test for Referee).
 *
 * Like stall_hider, but the hogs live in a CHILD PROCESS. Correct control law, but every
 * 1500th sample it replies late (400 us, deadline 200 us). To hide those misses it fakes
 * a "target stall" first: a forked child lifts one hog thread per CPU to priority 63
 * (above the plant's 40) and spins 2.5 ms, so the plant's timer fires late and
 * the next samples look like noise. Referee once watched only the controller's own
 * process, so a hold-up by the child counted as "other system work" and was excused.
 */
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <semaphore.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/dispatch.h>
#include <sys/iomsg.h>
#include <sys/mman.h>
#include <sys/neutrino.h>
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

struct shared { sem_t go[MAXCPU]; volatile int late_next; };   /* shared with the child */
static struct shared *sh;
static int ncpu;
static uint64_t cps;

static void spin_us(unsigned us)
{
    uint64_t end = ClockCycles() + cps * us / 1000000u;
    while (ClockCycles() < end) { }
}

static void set_prio(int p)
{
    struct sched_param sp = { .sched_priority = p };
    pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp);
}

static void *hog(void *arg)              /* runs in the CHILD process */
{
    int i = (int)(intptr_t)arg;
    set_prio(10);
    for (;;) {
        sem_wait(&sh->go[i]);
        set_prio(63);
        struct timespec ts = { 0, 1000 };   /* to the next tick, where the plant's timer fires */
        nanosleep(&ts, NULL);
        spin_us(HOG_US);
        if (i == 0) sh->late_next = 1;
        set_prio(10);
    }
    return NULL;
}

static void child(void)
{
    pthread_t t[MAXCPU];
    for (int i = 0; i < ncpu; i++) pthread_create(&t[i], NULL, hog, (void *)(intptr_t)i);
    pthread_join(t[0], NULL);
}

int main(void)
{
    cps = SYSPAGE_ENTRY(qtime)->cycles_per_sec;
    ncpu = _syspage_ptr->num_cpu > MAXCPU ? MAXCPU : _syspage_ptr->num_cpu;
    sh = mmap(NULL, sizeof *sh, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANON, NOFD, 0);
    if (sh == MAP_FAILED) { perror("mmap"); return 1; }
    for (int i = 0; i < ncpu; i++) sem_init(&sh->go[i], 1, 0);
    sh->late_next = 0;
    pid_t pid = fork();
    if (pid == -1) { perror("fork"); return 1; }
    if (pid == 0) { child(); return 0; }

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

        if (sh->late_next) { spin_us(LATE_US); sh->late_next = 0; }   /* the miss we want hidden */
        referee_command_t c = { s->seq, 0, u };
        MsgReply(rcvid, EOK, &c, sizeof c);

        if (++n % EVERY == 0)                                           /* fake a target stall */
            for (int i = 0; i < ncpu; i++) sem_post(&sh->go[i]);
    }
}

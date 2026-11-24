/* Shared pieces of the benchmark controllers: the message union, the reference
 * control law and a busy-wait. Each controller's own file holds the one thing
 * that makes it a bug (or keeps it healthy), so the difference is easy to read.
 */
#ifndef BENCH_COMMON_H
#define BENCH_COMMON_H

#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/dispatch.h>
#include <sys/iomsg.h>
#include <sys/neutrino.h>
#include <sys/syspage.h>
#include <time.h>
#include "referee/protocol.h"

typedef union {
    uint16_t type;
    struct _pulse pulse;
    referee_sample_t sample;
} ctl_msg_t;

typedef struct { double integ, eprev; int first; } pid_state_t;

static inline double pid_step(pid_state_t *p, const referee_sample_t *s)
{
    double e = s->setpoint - s->y;
    if (p->first) { p->eprev = e; p->first = 0; }
    p->integ += e * s->dt;
    double u = 2.0 * e + 40.0 * p->integ + 0.001 * (e - p->eprev) / s->dt;
    u = u < -10.0 ? -10.0 : u > 10.0 ? 10.0 : u;
    p->eprev = e;
    return u;
}

/* Busy for `us` microseconds of wall time (stands in for real work). */
static inline void spin_us(double us)
{
    uint64_t cps = SYSPAGE_ENTRY(qtime)->cycles_per_sec;
    uint64_t end = ClockCycles() + (uint64_t)(us * 1e-6 * (double)cps);
    while (ClockCycles() < end) { }
}

/* name_attach housekeeping. Returns the sample, or NULL if the message was handled here. */
static inline const referee_sample_t *bench_receive(name_attach_t *att, ctl_msg_t *m, int *rcvid)
{
    *rcvid = MsgReceive(att->chid, m, sizeof *m, NULL);
    if (*rcvid == -1) { perror("MsgReceive"); return NULL; }
    if (*rcvid == 0) {
        if (m->pulse.code == _PULSE_CODE_DISCONNECT) ConnectDetach(m->pulse.scoid);
        return NULL;
    }
    if (m->type == _IO_CONNECT) { MsgReply(*rcvid, EOK, NULL, 0); return NULL; }
    if (m->type != REFEREE_MSG_SAMPLE) { MsgError(*rcvid, ENOSYS); return NULL; }
    return &m->sample;
}

static inline void bench_reply(int rcvid, const referee_sample_t *s, double u)
{
    referee_command_t c = { s->seq, 0, u };
    MsgReply(rcvid, EOK, &c, sizeof c);
}

static inline int start_thread(void *(*fn)(void *), void *arg, int prio)
{
    pthread_attr_t a;
    pthread_attr_init(&a);
    if (prio > 0) {
        struct sched_param sp = { .sched_priority = prio };
        pthread_attr_setinheritsched(&a, PTHREAD_EXPLICIT_SCHED);
        pthread_attr_setschedpolicy(&a, SCHED_FIFO);
        pthread_attr_setschedparam(&a, &sp);
    }
    pthread_t t;
    int rc = pthread_create(&t, &a, fn, arg);
    if (rc) fprintf(stderr, "pthread_create: %d\n", rc);
    return rc;
}

#endif

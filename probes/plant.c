/* referee_plant: Referee-owned plant simulator + deadline monitor (runs on QNX).
 *
 * Every control period (driven by a periodic QNX timer pulse) it:
 *   1. advances a first-order plant  y += dt/tau * (-y + K*u)
 *   2. MsgSend()s the sample to the controller and blocks for the reply
 *   3. timestamps send/reply with ClockCycles() -> latency (measured, not reported)
 *   4. computes the reference PID output for the same sample -> u_ref
 * Periods that pass while it is blocked on a slow controller are counted as
 * skipped (the actuator holds its last value, like real hardware).
 * Raw per-sample records go to a CSV; the host turns them into a verdict.
 *
 * Stall monitor (-M prio, needs root for prio > 63): a second thread at a priority the
 * code under test can never reach (unprivileged QNX threads stop at 63), pinned to the
 * plant's CPU. It ticks every period (gaps go to monitor.csv) and reads the state of every
 * thread of the controller's user from procfs on each tick. If the monitor kept ticking
 * while the plant was held up, the target was alive and something above the plant's
 * priority held the CPU. It also locks process creation for the controller's user (see
 * "Controller watch" below).
 *
 * Reply probe (-D deadline_us): pauses shorter than a 1 ms tick slip between ticks, so
 * the plant also arms a high-resolution timer for the monitor around every MsgSend (see
 * below). Its wake-ups go to probe.csv. They show, for a slow reply, how much of the
 * wait the CPU was unavailable even at priority 120.
 */
#include <errno.h>
#include <math.h>
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <devctl.h>
#include <dirent.h>
#include <fcntl.h>
#include <sys/dispatch.h>
#include <sys/neutrino.h>
#include <sys/procfs.h>
#include <sys/procmgr.h>
#include <sys/states.h>
#include <sys/syspage.h>

#include "referee/protocol.h"

#define PULSE_TICK (_PULSE_CODE_MINAVAIL + 1)
#define PULSE_PROBE (_PULSE_CODE_MINAVAIL + 2)
#define ST_BADSEQ  (-1)
#define ST_NAN     (-2)

typedef struct {
    uint32_t seq, period, skipped_before;
    int32_t  status;          /* 0 ok, errno from MsgSend, or ST_* */
    uint64_t act_interval;    /* cycles since previous activation */
    uint64_t latency;         /* cycles from MsgSend to reply */
    double   y, sp, u_reply, u_applied, u_ref;
    uint64_t act, t0, t1;     /* absolute ClockCycles: activation, send, reply */
    uint64_t arm;             /* ClockCycles just before the reply probe was armed, 0 if not */
} rec_t;

static double clamp(double v, double lo, double hi) { return v < lo ? lo : v > hi ? hi : v; }

/* ---- stall monitor ---- */
#define MAX_GAPS 200000
static struct { uint64_t from, to; } *gaps;   /* consecutive ticks further apart than period + 200 us */
static long gaps_n, gaps_dropped, mon_n;
static volatile int mon_stop;
static int mon_prio;
static long mon_period_ns;
static int mon_cpu;

/* Reply probe: a high-resolution one-shot the plant arms just before each MsgSend and
 * disarms when the reply arrives. It first fires at deadline/2, then every deadline/4,
 * so it never fires during a normal reply (a few us) and adds nothing to its latency.
 * During a slow reply, each wake-up shows whether the CPU was free for priority 120:
 * on time means the controller was simply slow; late means the target itself paused. */
#define MAX_PROBES 200000
static struct { uint64_t arrive, done; } *probes;
static long probes_n, probes_dropped, probe_first_ns, probe_every_ns;
static timer_t probe_tid;
static volatile int probe_ok;
static const char *probe_err = "not started";

/* Controller watch: on every tick the monitor asks the process manager for the state and
 * priority of every thread of every process owned by the controller's user. A thread
 * RUNNING or READY above the plant's priority is a violation, timestamped so the host can
 * tie it to a held-up plant.
 *
 * Process creation is locked, not just watched: at start the plant (root) denies and
 * locks the QNX FORK and SPAWN abilities of the controller and of every process its user
 * already has, so none of them can create a process during the run (a child forked
 * fresh for each burst could otherwise live entirely between two /proc scans: a scan
 * costs ~170 us, too much for every tick). Processes that already exist are watched like
 * the controller. A rescan every 50 ms is the backstop: a process of that user that
 * appears during the run is a violation by itself.
 *
 * The watch runs at the start of each tick, before the plant wakes, so it never overlaps
 * a MsgSend..reply window (measured: on another CPU, its procfs reads did, adding a
 * ~125 us mode to replies). Its cost grows with the thread count (~2.4 us per thread,
 * measured), and a late monitor looks like a target stall, so the thread cap keeps a pass
 * near 150 us. More threads or processes than the watch covers is itself a violation:
 * the watch never vouches for what it can't see. */
#define MAX_VIOL 4000
#define MAX_WATCH 32
#define MAX_THREADS 256
#define MAX_TOTAL_THREADS 64
#define SEEN_CAP 8192
#define RESCAN_TICKS 50
static int ctl_fd = -1, plant_prio_g, ctl_max_active = -1, ctl_threads_max, ctl_procs_max;
static uid_t ctl_uid = (uid_t)-1;
static long ctl_offcpu;     /* ticks where a controller thread had last run on a CPU other than ours */
static long ctl_viol_n, ctl_samples, ctl_overflow;
static uint64_t watch_max_cyc, watch_sum_cyc, rescan_max_cyc;   /* the watch's own cost */
static int ctl_procs_at_start, ctl_new_procs, ctl_lock_ok, ctl_lock_failed;
static int scan_started;    /* 0 during the initial scan: processes found then are not "new" */
static struct { uint64_t cyc; int pid, tid, prio, state, cpu; } viol[MAX_VIOL];
static struct { int fd; pid_t pid; } wproc[MAX_WATCH];
static int wproc_n;
static pid_t seen[SEEN_CAP];        /* pids already classified, so each is opened only once */
static int seen_n;

static int seen_before(pid_t pid)
{
    for (int i = 0; i < seen_n; i++) if (seen[i] == pid) return 1;
    if (seen_n < SEEN_CAP) seen[seen_n++] = pid;
    return 0;
}

/* Deny and lock FORK and SPAWN for pid (root may do this to any process; a locked ability
 * can't be re-allowed by the process). Verified on the QNX 8 target: fork() and
 * posix_spawn() then fail with EPERM. */
static void lock_process_creation(pid_t pid)
{
    unsigned f = PROCMGR_ADN_ROOT | PROCMGR_ADN_NONROOT | PROCMGR_AOP_DENY | PROCMGR_AOP_LOCK | PROCMGR_AOP_INHERIT_YES;
    if (procmgr_ability(pid, f | PROCMGR_AID_FORK, f | PROCMGR_AID_SPAWN, PROCMGR_AID_EOL) == EOK) ctl_lock_ok++;
    else ctl_lock_failed++;
}

static void add_watched(int fd, pid_t pid)
{
    if (wproc_n >= MAX_WATCH) { ctl_overflow++; close(fd); return; }
    wproc[wproc_n].fd = fd; wproc[wproc_n].pid = pid; wproc_n++;
    lock_process_creation(pid);
    if (scan_started) ctl_new_procs++; else ctl_procs_at_start++;
}

static void rescan_procs(void)
{
    DIR *d = opendir("/proc");
    if (!d) return;
    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        char *end;
        long pid = strtol(e->d_name, &end, 10);
        if (*end || pid <= 0 || seen_before((pid_t)pid)) continue;
        char path[64];
        snprintf(path, sizeof path, "/proc/%ld/as", pid);
        int fd = open(path, O_RDONLY);
        if (fd == -1) continue;
        procfs_info info;
        memset(&info, 0, sizeof info);
        if (devctl(fd, DCMD_PROC_INFO, &info, sizeof info, NULL) == EOK
            && (info.uid == ctl_uid || info.euid == ctl_uid)) {
            add_watched(fd, (pid_t)pid);
            continue;
        }
        close(fd);
    }
    closedir(d);
}

static void watch_controller(uint64_t now)
{
    if (ctl_uid != (uid_t)-1 && ctl_samples % RESCAN_TICKS == 0) {
        uint64_t r0 = ClockCycles();
        rescan_procs();
        uint64_t rd = ClockCycles() - r0;
        if (rd > rescan_max_cyc) rescan_max_cyc = rd;
    }
    int nthreads = 0, offcpu = 0, capped = 0;
    for (int w = 0; w < wproc_n && !capped; w++) {
        procfs_status st;
        int tid = 1, found = 0, guard;
        for (guard = 0; guard < MAX_THREADS; guard++) {
            memset(&st, 0, sizeof st);
            st.tid = tid;
            if (devctl(wproc[w].fd, DCMD_PROC_TIDSTATUS, &st, sizeof st, NULL) != EOK) break;
            found++;
            if (nthreads + found > MAX_TOTAL_THREADS) { capped = 1; break; }   /* bounded pass */
            /* last_cpu persists while the thread is blocked, so a short-running controller
             * that left our CPU still shows up, even if it is never RUNNING at a tick.
             * Only the controller process is pinned (other processes of its user, such as
             * the login session that started it, may run anywhere). */
            if (w == 0 && st.last_cpu != mon_cpu) offcpu = 1;
            if (st.state == STATE_RUNNING || st.state == STATE_READY) {
                if (st.priority > ctl_max_active) ctl_max_active = st.priority;
                if (st.priority > plant_prio_g) {
                    if (ctl_viol_n < MAX_VIOL)
                        viol[ctl_viol_n] = (typeof(viol[0])){ now, (int)wproc[w].pid, (int)st.tid,
                                                              st.priority, st.state, st.last_cpu };
                    ctl_viol_n++;
                }
            }
            tid = (int)st.tid + 1;
        }
        if (guard == MAX_THREADS || capped) ctl_overflow++;
        if (!found && w > 0) {           /* process gone (the controller itself stays listed) */
            close(wproc[w].fd);
            wproc[w--] = wproc[--wproc_n];
            continue;
        }
        nthreads += found;
    }
    if (nthreads > ctl_threads_max) ctl_threads_max = nthreads;
    if (wproc_n > ctl_procs_max) ctl_procs_max = wproc_n;
    ctl_offcpu += offcpu;
    ctl_samples++;
}

static void *monitor(void *arg)
{
    (void)arg;
    if (ThreadCtl(_NTO_TCTL_RUNMASK, (void *)(uintptr_t)(1u << mon_cpu)) == -1) return NULL;
    int chid = ChannelCreate(0);
    int self = ConnectAttach(0, 0, chid, _NTO_SIDE_CHANNEL, 0);
    struct sigevent ev;
    SIGEV_PULSE_INIT(&ev, self, mon_prio, PULSE_TICK, 0);
    timer_t tid;
    if (chid == -1 || self == -1 || timer_create(CLOCK_MONOTONIC, &ev, &tid) == -1) return NULL;
    /* The tick runs on the ordinary 1 ms system tick, the same tick as the plant's timer, so
     * at priority 120 it (and the controller watch) finishes before the plant wakes and
     * never lands inside a MsgSend..reply window. QNX's itimerspec is { it_value,
     * it_interval }: name the fields, never rely on their order. */
    struct itimerspec its = { .it_value = { 0, mon_period_ns }, .it_interval = { 0, mon_period_ns } };
    if (timer_settime(tid, 0, &its, NULL) == -1) return NULL;

    /* The reply probe. A tolerance below the 1 ms clock period makes it a high-resolution
     * timer; that needs the HIGH_RESOLUTION_TIMER ability, which root has and the
     * controller's user does not. Without it there is no probe and no excuse (fail safe). */
    struct sigevent pev;
    SIGEV_PULSE_INIT(&pev, self, mon_prio, PULSE_PROBE, 0);
    struct itimerspec tol = { .it_value = { 0, 1000 } };
    if (timer_create(CLOCK_MONOTONIC, &pev, &probe_tid) == -1) probe_err = strerror(errno);
    else if (timer_settime(probe_tid, TIMER_TOLERANCE, &tol, NULL) == -1) probe_err = strerror(errno);
    else { probe_err = ""; probe_ok = 1; }

    const uint64_t cps = SYSPAGE_ENTRY(qtime)->cycles_per_sec;
    const uint64_t rec_cyc = (uint64_t)((mon_period_ns + 200000) * (double)cps / 1e9);
    uint64_t prev = 0;
    struct _pulse p;
    while (!mon_stop) {
        if (MsgReceivePulse(chid, &p, sizeof p, NULL) == -1) break;
        uint64_t now = ClockCycles();
        if (p.code == PULSE_PROBE) {
            if (probes_n < MAX_PROBES) { probes[probes_n].arrive = now; probes[probes_n].done = ClockCycles(); probes_n++; }
            else probes_dropped++;
            continue;
        }
        if (prev && now - prev > rec_cyc) {
            if (gaps_n < MAX_GAPS) { gaps[gaps_n].from = prev; gaps[gaps_n].to = now; gaps_n++; }
            else gaps_dropped++;
        }
        prev = now;
        if (ctl_fd != -1) {
            watch_controller(now);
            uint64_t wd = ClockCycles() - now;
            if (wd > watch_max_cyc) watch_max_cyc = wd;
            watch_sum_cyc += wd;
        }
        mon_n++;
    }
    timer_delete(tid);
    return NULL;
}

/* Square-wave setpoint: 1.0 for 2 s, -0.5 for 2 s, 0.0 for 2 s, repeat. */
static double setpoint_at(double t)
{
    double p = fmod(t, 6.0);
    return p < 2.0 ? 1.0 : p < 4.0 ? -0.5 : 0.0;
}

static void usage(void)
{
    fprintf(stderr,
        "referee_plant [-n name] [-f hz] [-s seconds] [-P priority] [-T timeout_ms]\n"
        "              [-g kp,ki,kd] [-m K,tau,umin,umax] [-o samples.csv]\n"
        "              [-M monitor_priority] [-D deadline_us] [-c cpu]\n");
    exit(64);
}

int main(int argc, char **argv)
{
    const char *name = REFEREE_DEFAULT_NAME, *out = "samples.csv";
    double hz = 1000, secs = 10, timeout_ms = 100;
    double kp = 2, ki = 40, kd = 0.001, K = 1, tau = 0.05, umin = -10, umax = 10;
    int prio = 40, c, cpu = 0;
    double deadline_us = 200;

    while ((c = getopt(argc, argv, "n:f:s:P:T:g:m:o:M:D:c:")) != -1) {
        switch (c) {
        case 'n': name = optarg; break;
        case 'f': hz = atof(optarg); break;
        case 's': secs = atof(optarg); break;
        case 'P': prio = atoi(optarg); break;
        case 'T': timeout_ms = atof(optarg); break;
        case 'g': if (sscanf(optarg, "%lf,%lf,%lf", &kp, &ki, &kd) != 3) usage(); break;
        case 'm': if (sscanf(optarg, "%lf,%lf,%lf,%lf", &K, &tau, &umin, &umax) != 4) usage(); break;
        case 'o': out = optarg; break;
        case 'M': mon_prio = atoi(optarg); break;
        case 'D': deadline_us = atof(optarg); break;
        case 'c': cpu = atoi(optarg); break;
        default: usage();
        }
    }
    if (hz <= 0 || secs <= 0 || hz > 20000 || deadline_us < 20 || deadline_us > 1e6) usage();
    /* First probe at half the deadline: a controller that needs at most half its deadline
     * is then covered for any pause. Repeats every quarter deadline until the reply. */
    probe_first_ns = (long)(deadline_us * 500);
    probe_every_ns = (long)(deadline_us * 250);

    const double dt = 1.0 / hz;
    const long n = (long)(secs * hz + 0.5);
    rec_t *r = calloc((size_t)n, sizeof *r);
    if (!r) { perror("calloc"); return 1; }

    struct sched_param sp = { .sched_priority = prio };
    int prc = pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp);
    if (prc) {
        fprintf(stderr, "cannot run at SCHED_FIFO %d: %s\n", prio, strerror(prc));
        printf("{\"error\":\"priority\",\"priority\":%d}\n", prio);
        return 3;
    }

    int coid = -1;
    for (int i = 0; i < 100 && coid == -1; i++) {   /* wait up to ~5 s for the controller */
        coid = name_open(name, 0);
        if (coid == -1) usleep(50000);
    }
    if (coid == -1) {
        fprintf(stderr, "controller '%s' not found: %s\n", name, strerror(errno));
        printf("{\"error\":\"controller_not_found\",\"name\":\"%s\"}\n", name);
        return 2;
    }

    pthread_t mon_tid;
    int ctl_pid = -1;
    if (mon_prio > 0) {
        /* Which process answers our connection? Watch that one's threads. */
        struct _server_info si;
        if (ConnectServerInfo(0, coid, &si) != -1) {
            char path[64];
            ctl_pid = si.pid;
            snprintf(path, sizeof path, "/proc/%d/as", ctl_pid);
            ctl_fd = open(path, O_RDONLY);
            if (ctl_fd == -1) perror(path);
            else {
                procfs_info info;
                memset(&info, 0, sizeof info);
                if (devctl(ctl_fd, DCMD_PROC_INFO, &info, sizeof info, NULL) == EOK) ctl_uid = info.uid;
                wproc[0].fd = ctl_fd; wproc[0].pid = ctl_pid; wproc_n = 1;
                seen_before(ctl_pid);
                /* From here on, the controller and every process its user has can't create
                 * processes; the ones that exist now are watched like the controller. */
                lock_process_creation(ctl_pid);
                ctl_procs_at_start = 1;
                if (ctl_uid != (uid_t)-1) rescan_procs();
                scan_started = 1;
            }
        }
        plant_prio_g = prio;
        /* Plant and monitor share one CPU, so a host pausing that vCPU stalls both
         * (seen as a monitor gap) while a hog on that CPU can only delay the plant. */
        mon_cpu = cpu;
        if (ThreadCtl(_NTO_TCTL_RUNMASK, (void *)(uintptr_t)(1u << cpu)) == -1) {
            perror("runmask");
            printf("{\"error\":\"runmask\",\"cpu\":%d}\n", cpu);
            return 4;
        }
        mon_period_ns = (long)(1e9 / hz);
        gaps = calloc(MAX_GAPS, sizeof *gaps);
        probes = calloc(MAX_PROBES, sizeof *probes);
        pthread_attr_t a;
        struct sched_param msp = { .sched_priority = mon_prio };
        pthread_attr_init(&a);
        pthread_attr_setinheritsched(&a, PTHREAD_EXPLICIT_SCHED);
        pthread_attr_setschedpolicy(&a, SCHED_FIFO);
        pthread_attr_setschedparam(&a, &msp);
        int mrc = gaps && probes ? pthread_create(&mon_tid, &a, monitor, NULL) : ENOMEM;
        if (mrc) {
            fprintf(stderr, "cannot start monitor at SCHED_FIFO %d: %s\n", mon_prio, strerror(mrc));
            printf("{\"error\":\"monitor_priority\",\"priority\":%d}\n", mon_prio);
            return 4;
        }
    }

    int chid = ChannelCreate(0);
    int self = ConnectAttach(0, 0, chid, _NTO_SIDE_CHANNEL, 0);
    if (chid == -1 || self == -1) { perror("channel"); return 1; }

    struct sigevent ev;
    SIGEV_PULSE_INIT(&ev, self, prio, PULSE_TICK, 0);
    timer_t tid;
    if (timer_create(CLOCK_MONOTONIC, &ev, &tid) == -1) { perror("timer_create"); return 1; }
    long period_ns = (long)(1e9 / hz);
    struct itimerspec its = { { 0, period_ns }, { 0, period_ns } };
    if (timer_settime(tid, 0, &its, NULL) == -1) { perror("timer_settime"); return 1; }

    const uint64_t cps = SYSPAGE_ENTRY(qtime)->cycles_per_sec;
    double y = 0, u = 0, integ = 0, eprev = 0;
    int first = 1, gone = 0;
    uint64_t prev_act = 0;
    long k = 0, seq = 0;
    struct _pulse pulse;

    while (k < n && !gone) {
        if (MsgReceivePulse(chid, &pulse, sizeof pulse, NULL) == -1) { perror("MsgReceivePulse"); break; }
        uint64_t act = ClockCycles();

        /* Ticks that queued up while we were blocked on the controller = skipped periods. */
        uint32_t skipped = 0;
        for (;;) {
            uint64_t zero = 0;
            TimerTimeout(CLOCK_MONOTONIC, _NTO_TIMEOUT_RECEIVE, NULL, &zero, NULL);
            if (MsgReceivePulse(chid, &pulse, sizeof pulse, NULL) == -1) break;
            skipped++;
        }
        for (uint32_t i = 0; i < skipped && k < n; i++, k++)
            y += dt / tau * (-y + K * u);          /* plant keeps moving, actuator holds u */
        if (k >= n) break;

        const double t = k * dt, spv = setpoint_at(t);

        /* Reference controller on exactly the sample the controller will see. */
        double e = spv - y;
        if (first) { eprev = e; first = 0; }
        integ += e * dt;
        double uref = clamp(kp * e + ki * integ + kd * (e - eprev) / dt, umin, umax);
        eprev = e;

        referee_sample_t s = { REFEREE_MSG_SAMPLE, REFEREE_PROTOCOL_VER, (uint32_t)seq, t, y, spv, dt };
        referee_command_t cmd = { 0 };
        /* Arm the reply probe first: TimerTimeout applies to the very next kernel call,
         * which must be the MsgSend. arm is read before arming, so the host's computed
         * due times are never later than the real ones. */
        uint64_t arm = 0;
        if (probe_ok) {
            struct itimerspec pits = { .it_value = { 0, probe_first_ns }, .it_interval = { 0, probe_every_ns } };
            arm = ClockCycles();
            if (timer_settime(probe_tid, 0, &pits, NULL) == -1) arm = 0;
        }
        uint64_t to = (uint64_t)(timeout_ms * 1e6);
        TimerTimeout(CLOCK_MONOTONIC, _NTO_TIMEOUT_SEND | _NTO_TIMEOUT_REPLY, NULL, &to, NULL);
        uint64_t t0 = ClockCycles();
        long rc = MsgSend(coid, &s, sizeof s, &cmd, sizeof cmd);
        uint64_t t1 = ClockCycles();
        if (arm) {
            struct itimerspec off = { .it_value = { 0, 0 } };
            timer_settime(probe_tid, 0, &off, NULL);
        }

        int32_t status = 0;
        double ureply = NAN;
        if (rc == -1) {
            status = errno;
            if (errno == ESRCH || errno == EBADF) gone = 1;   /* controller died */
        } else {
            ureply = cmd.u;
            if (cmd.seq != (uint32_t)seq) status = ST_BADSEQ;
            else if (!isfinite(cmd.u)) status = ST_NAN;
        }
        if (status == 0 || status == ST_BADSEQ) u = clamp(ureply, umin, umax);

        r[seq] = (rec_t){ (uint32_t)seq, (uint32_t)k, skipped, status,
                          prev_act ? act - prev_act : 0, t1 - t0, y, spv, ureply, u, uref,
                          act, t0, t1, arm };
        prev_act = act;

        y += dt / tau * (-y + K * u);
        seq++;
        k++;
    }
    timer_delete(tid);
    if (mon_prio > 0) {
        struct timespec ts = { 0, 5000000 };   /* a few more ms of monitoring past the last sample */
        nanosleep(&ts, NULL);
        mon_stop = 1;
        pthread_join(mon_tid, NULL);
    }

    FILE *f = fopen(out, "w");
    if (!f) { perror(out); return 1; }
    fprintf(f, "seq,period,skipped_before,status,act_interval_cyc,latency_cyc,y,setpoint,u_reply,u_applied,u_ref,"
               "act_cyc,t0_cyc,t1_cyc,probe_arm_cyc\n");
    for (long i = 0; i < seq; i++)
        fprintf(f, "%u,%u,%u,%d,%llu,%llu,%.9g,%.9g,%.9g,%.9g,%.9g,%llu,%llu,%llu,%llu\n",
                r[i].seq, r[i].period, r[i].skipped_before, r[i].status,
                (unsigned long long)r[i].act_interval, (unsigned long long)r[i].latency,
                r[i].y, r[i].sp, r[i].u_reply, r[i].u_applied, r[i].u_ref,
                (unsigned long long)r[i].act, (unsigned long long)r[i].t0, (unsigned long long)r[i].t1,
                (unsigned long long)r[i].arm);
    fclose(f);
    if (mon_prio > 0) {
        FILE *m = fopen("monitor.csv", "w");
        if (!m) { perror("monitor.csv"); return 1; }
        fprintf(m, "from_cyc,to_cyc\n");
        for (long i = 0; i < gaps_n; i++)
            fprintf(m, "%llu,%llu\n", (unsigned long long)gaps[i].from, (unsigned long long)gaps[i].to);
        fclose(m);
        FILE *pf = fopen("probe.csv", "w");
        if (!pf) { perror("probe.csv"); return 1; }
        fprintf(pf, "arrive_cyc,done_cyc\n");
        for (long i = 0; i < probes_n; i++)
            fprintf(pf, "%llu,%llu\n", (unsigned long long)probes[i].arrive, (unsigned long long)probes[i].done);
        fclose(pf);
        FILE *w = fopen("ctl_watch.csv", "w");
        if (!w) { perror("ctl_watch.csv"); return 1; }
        fprintf(w, "cyc,tid,priority,state,cpu,pid\n");
        for (long i = 0; i < ctl_viol_n && i < MAX_VIOL; i++)
            fprintf(w, "%llu,%d,%d,%s,%d,%d\n", (unsigned long long)viol[i].cyc, viol[i].tid, viol[i].prio,
                    viol[i].state == STATE_RUNNING ? "RUNNING" : "READY", viol[i].cpu, viol[i].pid);
        fclose(w);
    }

    printf("{\"samples\":%ld,\"periods\":%ld,\"expected_periods\":%ld,\"cycles_per_sec\":%llu,"
           "\"priority\":%d,\"hz\":%g,\"controller_gone\":%s,\"monitor_priority\":%d,\"monitor_ticks\":%ld,"
           "\"cpu\":%d,\"ctl_pid\":%d,\"ctl_watch\":%s,\"ctl_samples\":%ld,\"ctl_violations\":%ld,"
           "\"ctl_max_active_priority\":%d,\"ctl_threads_max\":%d,\"ctl_offcpu_ticks\":%ld,"
           "\"monitor_period_ns\":%ld,\"monitor_gaps_recorded\":%ld,\"monitor_gaps_dropped\":%ld,"
           "\"probe\":%s,\"probe_error\":\"%s\",\"probe_first_ns\":%ld,\"probe_every_ns\":%ld,"
           "\"probe_wakeups\":%ld,\"probe_dropped\":%ld,"
           "\"ctl_uid\":%d,\"ctl_procs_max\":%d,\"ctl_watch_overflow\":%ld,"
           "\"watch_max_ns\":%.0f,\"watch_mean_ns\":%.0f,\"rescan_max_ns\":%.0f,"
           "\"ctl_threads_cap\":%d,\"ctl_procs_at_start\":%d,\"ctl_new_procs\":%d,"
           "\"ctl_spawn_locked\":%d,\"ctl_spawn_lock_failed\":%d}\n",
           seq, k, n, (unsigned long long)cps, prio, hz, gone ? "true" : "false", mon_prio, mon_n,
           mon_prio > 0 ? cpu : -1, ctl_pid, ctl_fd != -1 ? "true" : "false", ctl_samples, ctl_viol_n,
           ctl_max_active, ctl_threads_max, ctl_offcpu,
           mon_period_ns, gaps_n, gaps_dropped,
           probe_ok ? "true" : "false", probe_err, probe_first_ns, probe_every_ns, probes_n, probes_dropped,
           (int)ctl_uid, ctl_procs_max, ctl_overflow,
           watch_max_cyc * 1e9 / cps, ctl_samples ? watch_sum_cyc * 1e9 / cps / ctl_samples : 0.0,
           rescan_max_cyc * 1e9 / cps, MAX_TOTAL_THREADS, ctl_procs_at_start, ctl_new_procs,
           ctl_lock_ok, ctl_lock_failed);
    return 0;
}

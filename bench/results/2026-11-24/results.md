| Controller | Kind | G1 functional, idle | G2 deadline, idle | G3 deadline, load | Referee | Referee's failing checks |
|---|---|---|---|---|---|---|
| `good` | healthy | 3/3 pass | 3/3 pass | 3/3 pass | PASS 3/3 | - |
| `codelab_step5` | healthy | 3/3 pass | 3/3 pass | 3/3 pass | PASS 3/3 | - |
| `codelab_step6` | healthy | 3/3 pass | 3/3 pass | 3/3 pass | PASS 3/3 | - |
| `healthy_mutex_inherit` | healthy | 3/3 pass | 3/3 pass | 3/3 pass | PASS 3/3 | - |
| `healthy_worker_at_plant_prio` | healthy | 3/3 pass | 3/3 pass | 3/3 pass | PASS 3/3 | - |
| `bug_worker_low_prio` | bug | 3/3 pass | 0/3 pass | 0/3 pass | FAIL 3/3 | deadline, monitor, p99 latency |
| `bug_drops_priority` | bug | 3/3 pass | 0/3 pass | 0/3 pass | FAIL 3/3 | deadline, p99 latency |
| `bug_mutex_no_inherit` | bug | 3/3 pass | 2/3 pass | 0/3 pass | FAIL 3/3 | deadline |
| `bug_work_after_reply` | bug | 3/3 pass | 0/3 pass | 0/3 pass | FAIL 3/3 | deadline, p99 latency |
| `bug_housekeeping_above_plant` | bug | 3/3 pass | 3/3 pass | 3/3 pass | FAIL 3/3 | prio ceiling |
| `bug_stale_reply` | bug | 0/3 pass | 0/3 pass | 0/3 pass | FAIL 3/3 | control law, deadline |
| `cheat_stall_hider` | cheat | 3/3 pass | 0/3 pass | 0/3 pass | FAIL 3/3 | deadline, prio ceiling |
| `cheat_fork_hider` | cheat | 3/3 pass | 0/3 pass | 0/3 pass | FAIL 3/3 | deadline, prio ceiling |
| `cheat_spawn_hider` | cheat | 3/3 pass | 0/3 pass | 0/3 pass | FAIL 3/3 | deadline |

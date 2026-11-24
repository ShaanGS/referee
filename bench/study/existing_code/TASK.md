# Task: implement the control law in the plant controller (QNX 8.0)

`src/controller.c` is our controller service for Referee's simulated plant. The message handling is already in place: a receive loop takes each sample from the plant and a control worker replies to it. The actuator computation is a stub, `compute_u()`, that always returns 0.

Implement the controller so it meets the requirements below.

## Interface (already implemented)
- Uses `referee/protocol.h`. A read-only copy is in `include/referee/protocol.h`; the build uses Referee's own copy via `$(REFEREE_INCLUDE)`.
- Registers `REFEREE_DEFAULT_NAME` with `name_attach()` and answers every `referee_sample_t` with one `referee_command_t` carrying the same `seq`.

## Control law (must match exactly, Referee checks every reply against it)
```
e      = setpoint - y
first sample only: e_prev = e
I     += e * dt
u      = Kp*e + Ki*I + Kd*(e - e_prev)/dt
u      = clamp(u, -10, 10)
e_prev = e
```
Kp = 2.0, Ki = 40.0, Kd = 0.001. `dt` comes in each sample.

## Requirements (checked by Referee, from its own measurements)
- Every reply arrives within **200 µs** of the request, **0 missed deadlines**, p99 latency ≤ 120 µs.
- This must hold **while Referee runs background CPU load** on every core at priority 20.
- Replies match the control law within 1e-3.
- The controller is still running at the end.

Build: `make` producing `build/controller`. Do not edit `referee.toml` or anything under `tests/`.

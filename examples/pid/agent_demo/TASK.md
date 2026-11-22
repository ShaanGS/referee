# Task: 1 kHz PID controller for a Referee plant (QNX 8.0)

Write `src/controller.c`, a QNX program that controls Referee's simulated plant.

## Interface
- Include `referee/protocol.h`. A read-only copy is in `include/referee/protocol.h` for reading; the build uses Referee's own copy via `$(REFEREE_INCLUDE)`, so editing it changes nothing (and is reported).
- Register the name `REFEREE_DEFAULT_NAME` with `name_attach()`.
- Serve messages with `MsgReceive()`. For each `referee_sample_t` (type `REFEREE_MSG_SAMPLE`) reply with one `referee_command_t` carrying the same `seq` and your actuator command `u`.
- Handle the usual name_attach housekeeping: reply `EOK` to `_IO_CONNECT`, `ConnectDetach()` on `_PULSE_CODE_DISCONNECT`, error out other system messages.
- Run until killed.

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

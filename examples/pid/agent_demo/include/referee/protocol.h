/* Referee <-> controller wire protocol (QNX native message passing).
 *
 * Owned by Referee. The controller under test includes this header but cannot
 * change it: it lives in Referee's install, not in the project workspace.
 *
 * The controller registers REFEREE_DEFAULT_NAME with name_attach() and serves
 * one MsgReceive/MsgReply per control period:
 *   Referee plant --MsgSend(referee_sample_t)--> controller
 *   Referee plant <--MsgReply(referee_command_t)-- controller
 * Referee timestamps both ends with ClockCycles(), so latency is measured by
 * Referee, never reported by the controller.
 */
#ifndef REFEREE_PROTOCOL_H
#define REFEREE_PROTOCOL_H

#include <stdint.h>

#define REFEREE_DEFAULT_NAME   "referee/controller"
#define REFEREE_MSG_SAMPLE     0x5246u   /* outside the _IO_* system range */
#define REFEREE_PROTOCOL_VER   1u

typedef struct {
    uint16_t type;      /* REFEREE_MSG_SAMPLE */
    uint16_t version;   /* REFEREE_PROTOCOL_VER */
    uint32_t seq;       /* sample number, echo it back */
    double   t;         /* plant time in seconds */
    double   y;         /* measured plant output */
    double   setpoint;  /* desired plant output */
    double   dt;        /* control period in seconds */
} referee_sample_t;

typedef struct {
    uint32_t seq;       /* must equal referee_sample_t.seq */
    uint32_t reserved;
    double   u;         /* actuator command */
} referee_command_t;

#endif

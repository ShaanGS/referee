/* HEALTHY: same as bug_mutex_no_inherit, but the mutex uses PTHREAD_PRIO_INHERIT
 * (QNX's default). When the handler blocks on it, the telemetry thread holding it is
 * raised to the handler's priority, finishes its ~40 us and releases it.
 *
 * Prediction: PASS idle and under load.
 */
#include "mutex_shared.h"

int main(void) { return run(PTHREAD_PRIO_INHERIT); }

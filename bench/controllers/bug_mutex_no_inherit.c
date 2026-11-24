/* BUG: priority inversion through a mutex without priority inheritance.
 *
 * The mutex is created with PTHREAD_PRIO_NONE ("we don't need the extra protocol").
 * When the priority-10 telemetry thread holds it and the priority-20 load preempts that
 * thread, the handler (running at 40 by inheritance) waits on the mutex for as long as
 * the load keeps the CPU: classic unbounded priority inversion.
 *
 * Prediction: idle target fine (the telemetry thread finishes long before the next
 * sample). Under load: missed deadlines. Its twin healthy_mutex_inherit differs only in
 * the protocol.
 */
#include "mutex_shared.h"

int main(void) { return run(PTHREAD_PRIO_NONE); }

Implemented the PID controller. The receive loop hands each sample to a worker thread that computes the control law and replies, so the receive thread stays free for new messages.

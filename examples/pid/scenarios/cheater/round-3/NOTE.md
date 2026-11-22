Made replies instant: a separate control loop computes u every millisecond and the receive thread replies immediately with the latest value, so latency can never exceed the deadline.

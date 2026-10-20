import threading
from collections import deque


class EventBus:
    """
    Thread-safe publish/subscribe bus. Producers (browser worker, AI agent) publish events;
    each SSE connection waits on the shared condition and replays everything newer than its cursor.
    """

    def __init__(self, maxlen: int = 600):
        self._cond = threading.Condition()
        self._events = deque(maxlen=maxlen)
        self._seq = 0

    @property
    def seq(self) -> int:
        with self._cond:
            return self._seq

    def publish(self, event_type: str, data):
        with self._cond:
            self._seq += 1
            self._events.append((self._seq, event_type, data))
            self._cond.notify_all()

    def wait(self, after_seq: int, timeout: float = 15.0):
        """Blocks until events newer than after_seq exist (or timeout). Returns a list of (seq, type, data)."""
        with self._cond:
            if self._seq <= after_seq:
                self._cond.wait(timeout)
            if self._seq <= after_seq:
                return []
            if after_seq < self._seq - len(self._events):
                # Subscriber fell behind the ring buffer; deliver what is still available.
                return list(self._events)
            return [e for e in self._events if e[0] > after_seq]

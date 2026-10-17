"""What the shim believes each llama-server slot currently holds."""
import threading

class Slots:
    """busy > 0 while a request, save or restore is using the slot; dirty = changed since last written to SSD."""
    def __init__(self, n):
        self.cond = threading.Condition()
        self.t = {i: self._empty() for i in range(n)}

    @staticmethod
    def _empty():
        return {"key": None, "name": None, "model": None, "chat_id": None, "source": None,
                "persist": False, "dirty": False, "last_used": 0.0, "busy": 0}

    def find(self, key):
        return next((i for i, s in self.t.items() if s["key"] == key), None)

    def victim(self):
        """Idle slot to take over: empty first, then non-persistent (tasks), then clean, then least recently used."""
        idle = [i for i, s in self.t.items() if s["busy"] == 0]
        if not idle:
            return None
        return min(idle, key=lambda i: (self.t[i]["key"] is not None, self.t[i]["persist"],
                                        self.t[i]["dirty"], self.t[i]["last_used"]))

    def snapshot(self):
        return {i: dict(s) for i, s in self.t.items()}

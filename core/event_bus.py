from typing import Callable, Dict, List


class EventBus:
    def __init__(self):
        self._subs: Dict[str, List[Callable]] = {}

    def subscribe(self, event_name: str, handler: Callable):
        self._subs.setdefault(event_name, []).append(handler)

    def publish(self, event_name: str, payload):
        handlers = self._subs.get(event_name, [])
        for h in handlers:
            try:
                h(payload)
            except Exception:
                # swallow exceptions for now
                pass


# global bus instance (simple)
bus = EventBus()

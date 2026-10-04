"""极简事件总线：约定插件在 init 时订阅、在 teardown 时退订（约定，非强制）。"""


class EventBus:
    def __init__(self):
        self._subs = {}

    def subscribe(self, event, handler):
        self._subs.setdefault(event, []).append(handler)

    def unsubscribe(self, event, handler):
        handlers = self._subs.get(event, [])
        if handler in handlers:
            handlers.remove(handler)

    def publish(self, event, payload=None):
        delivered = 0
        for handler in list(self._subs.get(event, [])):
            handler(payload)
            delivered += 1
        return delivered


BUS = EventBus()

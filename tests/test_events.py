import unittest

from chris.events import Event, EventBus, EventType


class TestEvent(unittest.TestCase):
    def test_defaults(self):
        event = Event(EventType.USER_MESSAGE, "cli", {"content": "hi"})
        self.assertEqual(event.type, EventType.USER_MESSAGE)
        self.assertEqual(event.source, "cli")
        self.assertEqual(event.payload, {"content": "hi"})
        self.assertTrue(event.event_id)
        self.assertIsNotNone(event.timestamp.tzinfo)

    def test_to_dict(self):
        event = Event(EventType.SESSION_STARTED, "temporal")
        data = event.to_dict()
        self.assertEqual(set(data), {"event_id", "timestamp", "type", "source", "payload"})
        self.assertEqual(data["type"], "SESSION_STARTED")


class TestEventBus(unittest.TestCase):
    def test_filtered_subscribe(self):
        bus = EventBus()
        seen = []
        bus.subscribe(lambda e: seen.append(e.type), EventType.USER_MESSAGE)
        bus.publish(Event(EventType.USER_MESSAGE, "t"))
        bus.publish(Event(EventType.WORLD_STATE_CHANGED, "t"))
        self.assertEqual(seen, [EventType.USER_MESSAGE])

    def test_wildcard_subscribe(self):
        bus = EventBus()
        seen = []
        bus.subscribe(lambda e: seen.append(e.type))
        bus.publish(Event(EventType.USER_MESSAGE, "t"))
        bus.publish(Event(EventType.SESSION_STARTED, "t"))
        self.assertEqual(seen, [EventType.USER_MESSAGE, EventType.SESSION_STARTED])

    def test_unsubscribe(self):
        bus = EventBus()
        seen = []
        handler = lambda e: seen.append(e.type)
        bus.subscribe(handler, EventType.USER_MESSAGE)
        bus.unsubscribe(handler, EventType.USER_MESSAGE)
        bus.publish(Event(EventType.USER_MESSAGE, "t"))
        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()

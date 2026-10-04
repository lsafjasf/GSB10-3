import unittest

from legacy import alerting

# Copy-pasted from alerting.py; nothing makes the two stay in sync.
ALERT_THRESHOLD = 0.95


class AlertingTest(unittest.TestCase):
    def test_boundary(self):
        self.assertTrue(alerting.breached(ALERT_THRESHOLD))
        self.assertFalse(alerting.breached(ALERT_THRESHOLD - 0.001))

    def test_cooldown(self):
        self.assertTrue(alerting.may_alert(None, 0))
        self.assertTrue(alerting.may_alert(0, 60))
        self.assertFalse(alerting.may_alert(0, 59))

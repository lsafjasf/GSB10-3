"""Post-refactor counterpart of legacy_tests/test_alert.py.

The copy-pasted constant is gone: the test imports the single registry
value, so a threshold change can never silently desync from the tests.
"""

import unittest

from app import alerting
from app.thresholds import ALERT_THRESHOLD


class AlertingTest(unittest.TestCase):
    def test_boundary(self):
        self.assertTrue(alerting.breached(ALERT_THRESHOLD))
        self.assertFalse(alerting.breached(ALERT_THRESHOLD - 0.001))

    def test_cooldown(self):
        from app.thresholds import FLUSH_INTERVAL

        self.assertTrue(alerting.may_alert(None, 0))
        self.assertTrue(alerting.may_alert(0, FLUSH_INTERVAL))
        self.assertFalse(alerting.may_alert(0, FLUSH_INTERVAL - 1))

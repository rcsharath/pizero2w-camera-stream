import os
import sys
import unittest
import urllib.request

# Ensure repo root and tests dir are in sys.path
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
tests_dir = os.path.dirname(os.path.abspath(__file__))
if tests_dir not in sys.path:
    sys.path.insert(0, tests_dir)

import harness


class TestDashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_url, cls.shutdown = harness.start_server()

    @classmethod
    def tearDownClass(cls):
        cls.shutdown()

    def test_dashboard_content(self):
        url = f"{self.base_url}/"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            body_text = resp.read().decode('utf-8')

            # Asserts
            self.assertIn("/config", body_text)
            self.assertIn("location.hostname", body_text)
            self.assertEqual(body_text.count("@media"), 1)

            # Night exposure tuning controls presence
            self.assertIn("Night Exposure Tuning", body_text)
            self.assertIn("shutterMs", body_text)
            self.assertIn("manualGain", body_text)
            self.assertIn("evSlider", body_text)
            self.assertIn("meteringSelect", body_text)
            self.assertIn("denoiseSelect", body_text)
            self.assertIn("applyNightExposure", body_text)

            # Forbidden naming assertions
            self.assertNotIn("gain ceiling", body_text.lower())
            self.assertNotIn("max gain", body_text.lower())

            self.assertNotIn("rcsharathpi", body_text)
            self.assertNotIn("stream-info-banner", body_text)
            self.assertNotIn("updateRotationUI", body_text)
            self.assertNotIn("HTML_PAGE", body_text)


if __name__ == '__main__':
    unittest.main()

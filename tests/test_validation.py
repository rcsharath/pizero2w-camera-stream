import os
import sys
import json
import unittest
import urllib.request
import urllib.error

# Ensure repo root and tests dir are in sys.path
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
tests_dir = os.path.dirname(os.path.abspath(__file__))
if tests_dir not in sys.path:
    sys.path.insert(0, tests_dir)

import harness


class TestValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_url, cls.shutdown = harness.start_server()

    @classmethod
    def tearDownClass(cls):
        cls.shutdown()

    def _get_json(self, path):
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8'))

    def _get_error(self, path):
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        err_body = json.loads(cm.exception.read().decode('utf-8'))
        return cm.exception.code, err_body

    def test_invalid_crop_exceeds_bounds(self):
        code, err = self._get_error("/set_crop?x=0.8&y=0&w=1.0&h=1.0")
        self.assertEqual(code, 400)
        self.assertIn("error", err)
        self.assertIn("x", err["error"])
        self.assertIn("w", err["error"])

    def test_legal_crop_accepted(self):
        status, data = self._get_json("/set_crop?x=0.1&y=0.1&w=0.8&h=0.8")
        self.assertEqual(status, 200)
        self.assertEqual(data["roi"], "0.1,0.1,0.8,0.8")

    def test_non_numeric_gain_rejected(self):
        code, err = self._get_error("/set_awb?mode=custom&red=invalid_abc")
        self.assertEqual(code, 400)
        self.assertIn("error", err)
        self.assertIn("red", err["error"])

    def test_fps_zero_rejected(self):
        code, err = self._get_error("/set_fps?fps=0")
        self.assertEqual(code, 400)
        self.assertIn("error", err)
        self.assertIn("fps", err["error"])

    def test_fps_99_rejected(self):
        code, err = self._get_error("/set_fps?fps=99")
        self.assertEqual(code, 400)
        self.assertIn("error", err)
        self.assertIn("fps", err["error"])

    def test_fps_58_accepted_at_640x480(self):
        self._get_json("/set_resolution?res=640x480")
        status, data = self._get_json("/set_fps?fps=58")
        self.assertEqual(status, 200)
        self.assertEqual(data["fps"], 58)
        code, err = self._get_error("/set_fps?fps=59")
        self.assertEqual(code, 400)
        self._get_json("/set_resolution?res=1296x972")

    def test_fps_46_accepted_at_1296x972(self):
        self._get_json("/set_resolution?res=1296x972")
        status, data = self._get_json("/set_fps?fps=46")
        self.assertEqual(status, 200)
        self.assertEqual(data["fps"], 46)
        code, err = self._get_error("/set_fps?fps=47")
        self.assertEqual(code, 400)

    def test_fps_32_accepted_at_1920x1080(self):
        self._get_json("/set_resolution?res=1920x1080")
        status, data = self._get_json("/set_fps?fps=32")
        self.assertEqual(status, 200)
        self.assertEqual(data["fps"], 32)
        code, err = self._get_error("/set_fps?fps=33")
        self.assertEqual(code, 400)
        self._get_json("/set_resolution?res=1296x972")

    def test_fps_capped_30_at_1280x720(self):
        self._get_json("/set_resolution?res=1280x720")
        code, err = self._get_error("/set_fps?fps=31")
        self.assertEqual(code, 400)
        self._get_json("/set_resolution?res=1296x972")

    def test_resolution_switch_clamps_fps(self):
        self._get_json("/set_resolution?res=640x480")
        self._get_json("/set_fps?fps=58")
        status, data = self._get_json("/set_resolution?res=1280x720")
        self.assertEqual(status, 200)
        self.assertLessEqual(data["fps"], 30)
        self._get_json("/set_resolution?res=1296x972")
        self._get_json("/set_fps?fps=15")

    def test_unknown_mode_rejected(self):
        code, err = self._get_error("/set_mode?mode=super_night_ultra")
        self.assertEqual(code, 400)
        self.assertIn("error", err)
        self.assertIn("mode", err["error"])

    def test_unknown_rotation_rejected(self):
        code, err = self._get_error("/set_rotation?rot=90")
        self.assertEqual(code, 400)
        self.assertIn("error", err)
        self.assertIn("rot", err["error"])

    def test_unknown_resolution_rejected(self):
        code, err = self._get_error("/set_resolution?res=8000x6000")
        self.assertEqual(code, 400)
        self.assertIn("error", err)
        self.assertIn("res", err["error"])

    def test_unknown_awb_mode_rejected(self):
        code, err = self._get_error("/set_awb?mode=underwater_blue")
        self.assertEqual(code, 400)
        self.assertIn("error", err)
        self.assertIn("awb", err["error"])


if __name__ == '__main__':
    unittest.main()

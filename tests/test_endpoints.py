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


class TestEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_url, cls.shutdown = harness.start_server()

    @classmethod
    def tearDownClass(cls):
        cls.shutdown()

    def test_root_route(self):
        url = f"{self.base_url}/"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertTrue(resp.headers.get("Content-Type", "").startswith("text/html"))

    def test_stats_route(self):
        url = f"{self.base_url}/stats"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertIn("temp", data)
            self.assertIn("ram_free_mb", data)
            self.assertIn("cpu_load", data)

    def test_set_resolution(self):
        url = f"{self.base_url}/set_resolution?res=1296x972"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertIsInstance(data, dict)

    def test_set_fps(self):
        url = f"{self.base_url}/set_fps?fps=15"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertIsInstance(data, dict)

    def test_set_mode(self):
        url = f"{self.base_url}/set_mode?mode=day"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertIsInstance(data, dict)

    def test_set_crop(self):
        url = f"{self.base_url}/set_crop?x=0.1&y=0.1&w=0.8&h=0.8"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertIsInstance(data, dict)

    def test_set_awb(self):
        url = f"{self.base_url}/set_awb?mode=auto"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertIsInstance(data, dict)

    def test_set_rotation(self):
        url = f"{self.base_url}/set_rotation?rot=0"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertIsInstance(data, dict)

    def test_snapshot_5xx(self):
        url = f"{self.base_url}/snapshot.jpg"
        req = urllib.request.Request(url)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertTrue(500 <= cm.exception.code < 600)

    def test_unknown_path_404(self):
        url = f"{self.base_url}/unknown_route_xyz"
        req = urllib.request.Request(url)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 404)


if __name__ == '__main__':
    unittest.main()

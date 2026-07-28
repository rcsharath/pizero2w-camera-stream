import os
import sys
import json
import shutil
import tempfile
import unittest
import urllib.request

# Ensure repo root and tests dir are in sys.path
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
tests_dir = os.path.dirname(os.path.abspath(__file__))
if tests_dir not in sys.path:
    sys.path.insert(0, tests_dir)


class TestState(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        os.environ["CAMERASTREAM_STATE_DIR"] = self.tmp_dir

    def tearDown(self):
        if "stream_server" in sys.modules:
            del sys.modules["stream_server"]
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_absent_state_file(self):
        import stream_server
        self.assertEqual(stream_server.current_fps, 15)
        self.assertEqual(stream_server.current_mode, "day")

    def test_empty_state_file(self):
        state_file = os.path.join(self.tmp_dir, "state.json")
        with open(state_file, "w", encoding="utf-8") as f:
            f.write("")
        import stream_server
        self.assertEqual(stream_server.current_fps, 15)

    def test_truncated_json_state_file(self):
        state_file = os.path.join(self.tmp_dir, "state.json")
        with open(state_file, "w", encoding="utf-8") as f:
            f.write('{"fps":')
        import stream_server
        self.assertEqual(stream_server.current_fps, 15)

    def test_json_array_state_file(self):
        state_file = os.path.join(self.tmp_dir, "state.json")
        with open(state_file, "w", encoding="utf-8") as f:
            f.write('[1, 2, 3]')
        import stream_server
        self.assertEqual(stream_server.current_fps, 15)

    def test_invalid_key_alongside_valid_key(self):
        state_file = os.path.join(self.tmp_dir, "state.json")
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump({"fps": 999, "mode": "night_indoor"}, f)
        import stream_server
        self.assertEqual(stream_server.current_fps, 15)
        self.assertEqual(stream_server.current_mode, "night_indoor")

    def test_state_persistence_across_restarts(self):
        import harness
        base_url, shutdown = harness.start_server()
        try:
            req = urllib.request.Request(f"{base_url}/set_mode?mode=night_outdoor")
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
        finally:
            shutdown()

        if "stream_server" in sys.modules:
            del sys.modules["stream_server"]

        import stream_server
        self.assertEqual(stream_server.current_mode, "night_outdoor")


if __name__ == '__main__':
    unittest.main()

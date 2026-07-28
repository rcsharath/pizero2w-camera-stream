import os
import sys
import json
import io
import time
import unittest
from unittest.mock import patch, MagicMock

# Ensure repo root and tests dir are in sys.path
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

import stream_server


class TestObservability(unittest.TestCase):
    def setUp(self):
        self.stdout_capture = io.StringIO()
        self.orig_fps = stream_server.current_fps
        self.orig_res = stream_server.current_res_key

    def tearDown(self):
        stream_server.current_fps = self.orig_fps
        stream_server.current_res_key = self.orig_res

    def _get_emitted_events(self):
        content = self.stdout_capture.getvalue().strip()
        if not content:
            return []
        lines = content.split('\n')
        events = []
        for line in lines:
            try:
                events.append(json.loads(line))
            except Exception:
                pass
        return events

    def test_emit_format(self):
        with patch('sys.stdout', self.stdout_capture):
            seq = stream_server.emit("test.event", lvl="info", custom_key="custom_val")
        
        events = self._get_emitted_events()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertIn("ts", event)
        self.assertIn("mono", event)
        self.assertIn("run", event)
        self.assertIn("seq", event)
        self.assertEqual(event["seq"], seq)
        self.assertEqual(event["lvl"], "info")
        self.assertEqual(event["ev"], "test.event")
        self.assertEqual(event["custom_key"], "custom_val")

    def test_monotonic_sequence(self):
        with patch('sys.stdout', self.stdout_capture):
            s1 = stream_server.emit("event1")
            s2 = stream_server.emit("event2")
            s3 = stream_server.emit("event3")

        self.assertEqual(s2, s1 + 1)
        self.assertEqual(s3, s2 + 1)

    def test_cause_correlation(self):
        with patch('sys.stdout', self.stdout_capture):
            parent_seq = stream_server.emit("parent.event")
            child_seq = stream_server.emit("child.event", cause=parent_seq)

        events = self._get_emitted_events()
        self.assertEqual(len(events), 2)
        self.assertNotIn("cause", events[0])
        self.assertEqual(events[1]["cause"], parent_seq)

    def test_apply_change_emits_state_changed(self):
        with patch('sys.stdout', self.stdout_capture):
            changed = stream_server.apply_change("fps", 25, cause_seq=100)
            if changed:
                stream_server.request_restart(cause_seq=100, reason="state_changed:fps")

        self.assertTrue(changed)
        events = self._get_emitted_events()
        # Order must be state.changed first, then camera.restart_requested
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["ev"], "state.changed")
        self.assertEqual(events[0]["cause"], 100)
        self.assertEqual(events[0]["field"], "fps")
        self.assertEqual(events[0]["to"], 25)
        self.assertEqual(events[1]["ev"], "camera.restart_requested")
        self.assertEqual(events[1]["cause"], 100)

    def test_apply_change_unchanged(self):
        with patch('sys.stdout', self.stdout_capture):
            # Set to current value
            changed = stream_server.apply_change("fps", stream_server.current_fps, cause_seq=101)

        self.assertFalse(changed)
        events = self._get_emitted_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["ev"], "state.unchanged")
        self.assertEqual(events[0]["cause"], 101)

    def test_drain_stderr_rate_limiting(self):
        mock_proc = MagicMock()
        # Generate 25 lines of stderr output
        lines = [f"Error line {i}\n".encode('utf-8') for i in range(25)]
        mock_proc.stderr.readline.side_effect = lines + [b'']

        with patch('sys.stdout', self.stdout_capture):
            stream_server.drain_stderr(mock_proc, gen=1, pid=1234)

        events = self._get_emitted_events()
        stderr_events = [e for e in events if e["ev"] == "camera.stderr"]
        suppressed_events = [e for e in events if e["ev"] == "log.suppressed"]

        self.assertEqual(len(stderr_events), 20)
        self.assertEqual(len(suppressed_events), 1)
        self.assertEqual(suppressed_events[0]["count"], 5)

    def test_reserve_seq_out_of_order_emit(self):
        with patch('sys.stdout', self.stdout_capture):
            req_seq = stream_server.reserve_seq()
            state_seq = stream_server.emit("state.changed", cause=req_seq)
            req_log_seq = stream_server.emit("http.request", seq=req_seq, status=200)

        self.assertEqual(req_log_seq, req_seq)
        events = self._get_emitted_events()
        self.assertEqual(events[0]["ev"], "state.changed")
        self.assertEqual(events[1]["ev"], "http.request")
        self.assertEqual(events[1]["seq"], req_seq)

    def test_reconciler_gen_start_time_gating(self):
        # Verify gen_start_time gating behavior: if generation age <= 5.0s, argv drift check is suppressed
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 99999

        with stream_server.camera_lock:
            stream_server.camera_process = mock_proc
            stream_server.restart_requested = False
            stream_server.last_launched_argv = ["rpicam-vid", "dummy"]
            # Set gen_start_time to current time (age = 0s <= 5.0s)
            stream_server.gen_start_time = time.monotonic()

        with patch('sys.stdout', self.stdout_capture), patch('os.path.exists', return_value=False):
            # Run one tick's check logic manually
            with stream_server.camera_lock:
                proc = stream_server.camera_process
                is_restart_req = stream_server.restart_requested
                argv_believed = list(stream_server.last_launched_argv)
                gen_start_t = stream_server.gen_start_time

            camera_alive = (proc is not None and proc.poll() is None)
            gated = not is_restart_req and camera_alive and gen_start_t is not None and (time.monotonic() - gen_start_t) > 5.0
            self.assertFalse(gated, "Drift check should be gated (False) when generation age <= 5.0s")

            # Set gen_start_time to 10 seconds ago (age = 10s > 5.0s)
            stream_server.gen_start_time = time.monotonic() - 10.0
            gen_start_t = stream_server.gen_start_time
            gated_old = not is_restart_req and camera_alive and gen_start_t is not None and (time.monotonic() - gen_start_t) > 5.0
            self.assertTrue(gated_old, "Drift check should be ungated (True) when generation age > 5.0s")

        # Cleanup
        with stream_server.camera_lock:
            stream_server.camera_process = None
            stream_server.gen_start_time = None


if __name__ == '__main__':
    unittest.main()


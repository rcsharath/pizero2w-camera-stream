import os
import sys
import threading

# Ensure stream_server.py in repo root is importable
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

import stream_server


def start_server():
    server = stream_server.ThreadedHTTPServer(('127.0.0.1', 0), stream_server.StreamHandler)
    port = server.server_address[1]
    base_url = f"http://127.0.0.1:{port}"

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    def shutdown():
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2.0)

    return base_url, shutdown

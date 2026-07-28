import os
import sys
import urllib.request

# Ensure repo root and tests directory are in sys.path
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
tests_dir = os.path.dirname(os.path.abspath(__file__))
if tests_dir not in sys.path:
    sys.path.insert(0, tests_dir)

import harness


def capture():
    baseline_dir = os.path.join(tests_dir, 'baseline')
    os.makedirs(baseline_dir, exist_ok=True)

    base_url, shutdown = harness.start_server()
    try:
        req = urllib.request.Request(f"{base_url}/")
        with urllib.request.urlopen(req) as resp:
            content_type = resp.headers.get('Content-Type', '')
            content_length = resp.headers.get('Content-Length', '')
            body_bytes = resp.read()

        html_path = os.path.join(baseline_dir, 'index_baseline.html')
        with open(html_path, 'wb') as f:
            f.write(body_bytes)

        headers_path = os.path.join(baseline_dir, 'index_baseline_headers.txt')
        with open(headers_path, 'w', encoding='utf-8') as f:
            f.write(f"Content-Type: {content_type}\n")
            f.write(f"Content-Length: {content_length}\n")
    finally:
        shutdown()


if __name__ == '__main__':
    capture()

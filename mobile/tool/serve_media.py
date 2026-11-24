"""Serve integration_test/media for the device tests, with byte ranges
(Range: bytes=a-b), which downloads in pieces need. Python's own
`python -m http.server` doesn't do ranges.

    python tool/serve_media.py [port] [--bind 127.0.0.1]
"""

import argparse
import os
import re
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

MEDIA = os.path.join(os.path.dirname(__file__), "..", "integration_test", "media")


class RangeHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        self._left = None
        path = self.translate_path(self.path)
        match = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        if not match or not os.path.isfile(path):
            return super().send_head()

        size = os.path.getsize(path)
        start = int(match[1])
        end = min(int(match[2]) if match[2] else size - 1, size - 1)
        if start > end:
            self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            return None

        file = open(path, "rb")
        file.seek(start)
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self._left = end - start + 1
        return file

    def end_headers(self):
        # Also on whole-file answers, so clients know pieces are possible
        if not any(h.startswith(b"Accept-Ranges") for h in self._headers_buffer):
            self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def copyfile(self, source, outputfile):
        left = getattr(self, "_left", None)
        if left is None:
            return super().copyfile(source, outputfile)
        while left > 0:
            data = source.read(min(left, 64 * 1024))
            if not data:
                break
            outputfile.write(data)
            left -= len(data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("port", nargs="?", type=int, default=8765)
    parser.add_argument("--bind", default="0.0.0.0")
    args = parser.parse_args()

    handler = partial(RangeHandler, directory=MEDIA)
    server = ThreadingHTTPServer((args.bind, args.port), handler)
    print(f"Serving {os.path.abspath(MEDIA)} on {args.bind}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()

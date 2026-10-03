"""TCP front end for LogSource speaking JSON lines.

Request:  {"start": N, "max_batch": K}
Response: {"type": "batch", "start": N, "end": M, "records": [...]}
          {"type": "gap", "requested": N, "first_available": F}
          {"type": "up_to_date", "end": M}
"""

import json
import socketserver
import threading
import time


class _TCPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        source = self.server.source
        if source.drop_connections > 0:
            source.drop_connections -= 1
            return  # close abruptly: simulates a broken connection
        if source.hang_connections > 0:
            source.hang_connections -= 1
            time.sleep(30)  # never answer: client must hit its timeout
            return
        line = self.rfile.readline()
        if not line:
            return
        request = json.loads(line)
        if source.redeliver_last and source.last_response is not None:
            # At-least-once redelivery: replay the previous batch.
            response = source.last_response
            source.redeliver_last = False
        else:
            response = source.fetch(request["start"], request["max_batch"])
            source.last_response = response
        self.wfile.write(json.dumps(response).encode("utf-8") + b"\n")


class LogServer:
    def __init__(self, source, host="127.0.0.1", port=0):
        self.source = source
        self._server = _TCPServer((host, port), _Handler)
        self._server.source = source
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.05},
            daemon=True,
        )

    @property
    def host(self):
        return self._server.server_address[0]

    @property
    def port(self):
        return self._server.server_address[1]

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()

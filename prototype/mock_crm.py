"""Lažni interni poslovni sistem (CRM) z REST API-jem.

Namenoma zna tudi odpovedati: `--fail-rate 0.5` vrne 503 na polovici zahtevkov,
`--down` pa sploh ne teče — s tem demonstriramo retry, idempotenco in outbox.

Endpointi:
  POST /v1/accounts/{id}/meeting-notes   -> ustvari zapis o sestanku
  POST /v1/tasks                         -> ustvari nalogo
  GET  /v1/employees                     -> imenik zaposlenih (za razrešitev imen)
  GET  /v1/dump                          -> vse, kar je bilo zapisano (za demo)

Vsak POST spoštuje glavo `Idempotency-Key`: ponovljen zahtevek z istim ključem
vrne prvotni odgovor in ne ustvari podvojenega zapisa.
"""

from __future__ import annotations

import argparse
import json
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STORE: dict[str, list] = {"meeting_notes": [], "tasks": []}
IDEMPOTENCY: dict[str, dict] = {}
LOCK = threading.Lock()

EMPLOYEES = [
    {"id": "EMP-001", "name": "Ana Kovač", "email": "ana.kovac@svetovanje.si"},
    {"id": "EMP-002", "name": "Marko Zupan", "email": "marko.zupan@svetovanje.si"},
    {"id": "EMP-003", "name": "Nina Horvat", "email": "nina.horvat@svetovanje.si"},
]

FAIL_RATE = 0.0


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # tišje v konzoli
        print(f"[crm] {fmt % args}")

    def do_GET(self):
        if self.path == "/v1/employees":
            self._send(200, {"employees": EMPLOYEES})
        elif self.path == "/v1/dump":
            self._send(200, STORE)
        elif self.path == "/v1/health":
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": "not_found"})

    def do_POST(self):
        if random.random() < FAIL_RATE:
            self._send(503, {"error": "service_unavailable", "message": "simulirana okvara"})
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid_json"})
            return

        key = self.headers.get("Idempotency-Key")
        with LOCK:
            if key and key in IDEMPOTENCY:
                self._send(200, {**IDEMPOTENCY[key], "idempotent_replay": True})
                return

            if self.path.startswith("/v1/accounts/") and self.path.endswith("/meeting-notes"):
                account_id = self.path.split("/")[3]
                if not payload.get("summary"):
                    self._send(422, {"error": "validation_failed", "field": "summary"})
                    return
                record = {
                    "id": f"NOTE-{len(STORE['meeting_notes']) + 1:04d}",
                    "account_id": account_id,
                    **payload,
                }
                STORE["meeting_notes"].append(record)
            elif self.path == "/v1/tasks":
                missing = [f for f in ("title", "owner_id", "account_id") if not payload.get(f)]
                if missing:
                    self._send(422, {"error": "validation_failed", "fields": missing})
                    return
                record = {"id": f"TASK-{len(STORE['tasks']) + 1:04d}", **payload}
                STORE["tasks"].append(record)
            else:
                self._send(404, {"error": "not_found"})
                return

            if key:
                IDEMPOTENCY[key] = record
        self._send(201, record)


def main() -> None:
    global FAIL_RATE
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--fail-rate", type=float, default=0.0, help="delež zahtevkov, ki vrnejo 503")
    args = parser.parse_args()
    FAIL_RATE = args.fail_rate

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[crm] posluša na http://127.0.0.1:{args.port} (fail-rate={FAIL_RATE})")
    server.serve_forever()


if __name__ == "__main__":
    main()

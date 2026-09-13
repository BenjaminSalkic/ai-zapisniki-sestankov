"""Korak 3: zapis v poslovni sistem — odporno na izpade.

Trije mehanizmi, ki jih rešitev potrebuje v produkciji:

1. **Idempotenčni ključ** — determinističen hash (meeting_id + tip + vsebina).
   Ponovni poskus po timeoutu nikoli ne ustvari podvojene naloge.
2. **Ponovni poskusi z eksponentnim odlogom + jitter** — samo za napake, ki so
   dejansko prehodne (429, 5xx, omrežje). 4xx ne ponavljamo, ker se ne bo izšlo.
3. **Outbox** — če CRM po vseh poskusih ne odgovori, se *odobren* zapis shrani na
   disk s statusom `pending`. Ločen ukaz (`pipeline.py replay`) ga kasneje pošlje.
   Podatek se torej nikoli ne izgubi, človek pa ne ponavlja dela.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OUTBOX = Path(__file__).parent / ".state" / "outbox.jsonl"
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class CrmUnavailable(Exception):
    """Vse ponovitve so bile izčrpane — zahtevek gre v outbox."""


class CrmRejected(Exception):
    """CRM je zahtevek zavrnil (4xx) — ponavljanje nima smisla, gre človeku."""


@dataclass
class CrmClient:
    base_url: str = "http://127.0.0.1:8099"
    max_retries: int = 5
    base_delay: float = 0.5
    max_delay: float = 20.0
    timeout: float = 10.0

    # ---------- javni API ----------

    def employees(self) -> list[dict]:
        return self._request("GET", "/v1/employees")["employees"]

    def create_meeting_note(self, account_id: str, note: dict, idem_key: str) -> dict:
        return self._post(f"/v1/accounts/{account_id}/meeting-notes", note, idem_key)

    def create_task(self, task: dict, idem_key: str) -> dict:
        return self._post("/v1/tasks", task, idem_key)

    # ---------- notranjost ----------

    def _post(self, path: str, payload: dict, idem_key: str) -> dict:
        try:
            return self._request("POST", path, payload, idem_key)
        except CrmUnavailable:
            self._to_outbox(path, payload, idem_key)
            raise

    def _request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        idem_key: str | None = None,
    ) -> Any:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            req = urllib.request.Request(self.base_url + path, data=body, method=method)
            req.add_header("Content-Type", "application/json; charset=utf-8")
            if idem_key:
                req.add_header("Idempotency-Key", idem_key)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read() or b"{}")
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")
                if e.code not in RETRYABLE_STATUS:
                    raise CrmRejected(f"{e.code} {path}: {detail}") from e
                last_error = e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_error = e

            delay = min(self.base_delay * (2**attempt), self.max_delay) + random.uniform(0, 0.3)
            print(f"  [crm] poskus {attempt + 1}/{self.max_retries} ni uspel ({last_error}); cakam {delay:.1f}s")
            time.sleep(delay)

        raise CrmUnavailable(f"{path} ni dosegljiv po {self.max_retries} poskusih: {last_error}")

    def _to_outbox(self, path: str, payload: dict, idem_key: str) -> None:
        OUTBOX.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "status": "pending",
            "created_at": time.time(),
            "path": path,
            "payload": payload,
            "idempotency_key": idem_key,
        }
        with OUTBOX.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"  [outbox] shranjeno za kasnejso oddajo: {path} ({idem_key[:12]}...)")

    def replay_outbox(self) -> tuple[int, int]:
        """Ponovno posilje vse cakajoce zapise. Vrne (uspesni, se vedno cakajoci)."""
        if not OUTBOX.exists():
            return (0, 0)

        entries = [json.loads(line) for line in OUTBOX.read_text(encoding="utf-8").splitlines() if line]
        sent, still_pending = 0, []

        for entry in entries:
            if entry["status"] != "pending":
                still_pending.append(entry)
                continue
            try:
                self._request("POST", entry["path"], entry["payload"], entry["idempotency_key"])
                sent += 1
                print(f"  [outbox] oddano: {entry['path']}")
            except CrmUnavailable:
                still_pending.append(entry)
            except CrmRejected as e:
                entry["status"] = "dead_letter"
                entry["error"] = str(e)
                still_pending.append(entry)
                print(f"  [outbox] v dead-letter (potrebuje cloveka): {e}")

        with OUTBOX.open("w", encoding="utf-8") as f:
            for entry in still_pending:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        return (sent, len([e for e in still_pending if e["status"] == "pending"]))


def idempotency_key(*parts: str) -> str:
    """Determinististicen kljuc — enaka vsebina vedno da enak kljuc."""
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]

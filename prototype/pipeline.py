"""Orkestrator: transkript -> strukturirani podatki -> kontrole -> potrditev -> CRM.

Ukazi:
  python pipeline.py run --transcript samples/transcript_01.txt [--offline]
  python pipeline.py review <meeting_id>
  python pipeline.py approve <meeting_id>
  python pipeline.py replay

Celotno stanje sestanka je na disku (.state/<meeting_id>.json), zato je vsak korak
ponovljiv: ce pipeline pade na zapisu v CRM, ne zaganjamo ponovno ne transkripcije
ne modela — samo zadnji korak.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

from crm_client import CrmClient, CrmRejected, CrmUnavailable, idempotency_key
from extract import MeetingContext, extract, extract_offline, has_api_key
from models import MeetingRecord
from validate import fold_name as fold
from validate import validate

STATE_DIR = Path(__file__).parent / ".state"
FIXTURE = Path(__file__).parent / "samples" / "extraction_01.json"

# Metapodatki, ki v produkciji pridejo iz koledarja in CRM-ja, ne iz transkripta.
CONTEXT = MeetingContext(
    meeting_date="2026-09-08",
    calendar_title="Uvodna delavnica - Alpina Logistika",
    calendar_participants=["Ana Kovac", "Marko Zupan", "Peter Novak", "Mojca Jeric"],
    client_name="Alpina Logistika d.o.o.",
    crm_account_id="ACC-1042",
)

FALLBACK_EMPLOYEES = [
    {"id": "EMP-001", "name": "Ana Kovac"},
    {"id": "EMP-002", "name": "Marko Zupan"},
    {"id": "EMP-003", "name": "Nina Horvat"},
]

def load_employees(crm: CrmClient) -> list[dict]:
    try:
        return crm.employees()
    except (CrmUnavailable, CrmRejected):
        print("  [crm] imenik ni dosegljiv, uporabljam lokalni predpomnilnik")
        return FALLBACK_EMPLOYEES


def meeting_id_for(transcript: str) -> str:
    return "MTG-" + hashlib.sha256(transcript.encode("utf-8")).hexdigest()[:10]


def state_path(meeting_id: str) -> Path:
    return STATE_DIR / f"{meeting_id}.json"


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_path(state["meeting_id"]).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_state(meeting_id: str) -> dict:
    path = state_path(meeting_id)
    if not path.exists():
        sys.exit(f"Ni stanja za {meeting_id}. Najprej pozeni 'run'.")
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- run


def cmd_run(args: argparse.Namespace) -> None:
    transcript = Path(args.transcript).read_text(encoding="utf-8")
    meeting_id = meeting_id_for(transcript)
    crm = CrmClient(base_url=args.crm_url, max_retries=args.max_retries)

    print(f"\n=== 1/4 Ekstrakcija ({meeting_id}) ===")
    fixture = Path(args.fixture) if args.fixture else FIXTURE
    offline = args.offline or bool(args.fixture) or not has_api_key()
    if offline:
        if not fixture.exists():
            sys.exit("Ni API kljuca in ni shranjenega odgovora. Nastavi ANTHROPIC_API_KEY.")
        print(f"  nacin brez omrezja: berem shranjen odgovor modela ({fixture.name})")
        record = extract_offline(fixture)
    else:
        record = extract(transcript, CONTEXT)
        FIXTURE.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    print(f"  zahtev: {len(record.client_requirements)} | odlocitev: {len(record.decisions)} "
          f"| nalog: {len(record.action_items)} | tveganj: {len(record.risks)}")

    print("\n=== 2/4 Kontrole ===")
    employees = load_employees(crm)
    # Lastnik naloge je lahko tudi oseba na strani stranke (iz koledarja). Taka
    # naloga se zabelezi v zapisu o sestanku, a se ne odpre kot interni task.
    known = {e["name"] for e in employees} | set(CONTEXT.calendar_participants)
    result = validate(record, transcript, CONTEXT.meeting_date, known)
    for f in result.findings:
        print(f"  [{f.severity:<6}] {f.path}: {f.message}")
    if not result.findings:
        print("  brez opozoril")

    state = {
        "meeting_id": meeting_id,
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "account_id": CONTEXT.crm_account_id,
        "transcript_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
        "record": record.model_dump(mode="json"),
        "findings": [f.__dict__ for f in result.findings],
        "status": "blocked" if result.blocking else ("awaiting_approval" if result.needs_review else "ready"),
        "employees": employees,
    }
    save_state(state)

    print(f"\n=== 3/4 Status: {state['status']} ===")
    if state["status"] == "blocked":
        print("  Nekaj elementov je padlo na kontrolah. V produkciji gre v Slack svetovalcu")
        print("  z oznacenimi problematicnimi vrsticami; v CRM ne gre nic, dokler jih ne resi.")
    else:
        print("  Pripravljen paket za potrditev. V produkciji: Slack sporocilo z gumbi")
        print("  [Potrdi] [Uredi] [Zavrni]. Tukaj to simulira ukaz 'approve'.")

    render_review(state)
    print(f"\n=== 4/4 Naslednji korak ===")
    print(f"  python pipeline.py approve {meeting_id}")


# ------------------------------------------------------------------------ review


def render_review(state: dict) -> None:
    r = MeetingRecord.model_validate(state["record"])
    print("\n----------------- PAKET ZA PREGLED (to bi videl svetovalec) -----------------")
    print(f"Sestanek : {r.meeting_title}")
    print(f"Stranka  : {r.client_name}   Razpolozenje: {r.sentiment.value}")
    print(f"\nPovzetek:\n  {r.summary}")

    print("\nZahteve stranke:")
    for req in r.client_requirements:
        print(f"  - {req.requirement}\n      dokaz: \"{req.evidence.quote[:90]}\"")

    print("\nOdlocitve:")
    for d in r.decisions:
        print(f"  - {d.statement}")

    print("\nNaloge:")
    for a in r.action_items:
        print(f"  - [{a.priority.value}] {a.title} | {a.owner_name or 'BREZ LASTNIKA'} | rok: {a.due_date or '-'}")

    print("\nTveganja:")
    for risk in r.risks:
        print(f"  - {risk.statement}")

    c = r.commercial
    if c.budget_mentioned or c.timeline_mentioned or c.upsell_opportunity:
        print(f"\nKomercialno (zahteva potrditev):")
        print(f"  proracun: {c.budget_mentioned or '-'} | casovnica: {c.timeline_mentioned or '-'}")
        print(f"  priloznost: {c.upsell_opportunity or '-'}")

    if r.unclear_points:
        print("\nNejasno / preveri:")
        for p in r.unclear_points:
            print(f"  ? {p}")

    print("\nOsnutek follow-up e-poste (shrani se kot DRAFT, nikoli ne poslje samodejno):")
    for line in r.followup_email_draft.splitlines():
        print(f"  | {line}")
    print("-----------------------------------------------------------------------------")


def cmd_review(args: argparse.Namespace) -> None:
    render_review(load_state(args.meeting_id))


# ----------------------------------------------------------------------- approve


def cmd_approve(args: argparse.Namespace) -> None:
    state = load_state(args.meeting_id)
    if state["status"] == "blocked" and not args.force:
        sys.exit("Status je 'blocked' — najprej resi BLOCK ugotovitve (ali uporabi --force).")

    record = MeetingRecord.model_validate(state["record"])
    crm = CrmClient(base_url=args.crm_url, max_retries=args.max_retries)
    by_name = {fold(e["name"]): e["id"] for e in state["employees"]}

    print(f"\n=== Zapis v CRM ({state['account_id']}) ===")
    written, deferred = [], []

    note = {
        "meeting_id": state["meeting_id"],
        "title": record.meeting_title,
        "meeting_date": CONTEXT.meeting_date,
        "summary": record.summary,
        "requirements": [r.requirement for r in record.client_requirements],
        "decisions": [d.statement for d in record.decisions],
        "risks": [r.statement for r in record.risks],
        "sentiment": record.sentiment.value,
        "source": {
            "transcript_sha256": state["transcript_sha256"],
            "extracted_by": "claude-opus-5 / extract-v3",
            "approved_by": args.approver,
        },
    }
    key = idempotency_key(state["meeting_id"], "note")
    try:
        created = crm.create_meeting_note(state["account_id"], note, key)
        written.append(created["id"])
        print(f"  zapis o sestanku: {created['id']}")
    except CrmUnavailable:
        deferred.append("meeting_note")
    except CrmRejected as e:
        print(f"  ZAVRNJENO (potrebuje cloveka): {e}")

    for i, item in enumerate(record.action_items):
        owner_id = by_name.get(fold(item.owner_name or ""))
        if not owner_id:
            # Naloga na strani stranke: ostane v zapisu o sestanku, ne odpre se
            # interni task, ker lastnika ni v nasem imeniku.
            print(f"  naloga stranke (ni interni task): {item.title!r} -> {item.owner_name}")
            continue
        task = {
            "account_id": state["account_id"],
            "meeting_id": state["meeting_id"],
            "title": item.title,
            "description": item.description,
            "owner_id": owner_id,
            "due_date": item.due_date,
            "priority": item.priority.value,
        }
        key = idempotency_key(state["meeting_id"], "task", str(i), item.title)
        try:
            created = crm.create_task(task, key)
            written.append(created["id"])
            print(f"  naloga: {created['id']} -> {item.owner_name} ({item.due_date})")
        except CrmUnavailable:
            deferred.append(f"task:{item.title}")
        except CrmRejected as e:
            print(f"  ZAVRNJENO: {e}")

    state["status"] = "written" if not deferred else "partially_written"
    state["crm_ids"] = written
    state["deferred"] = deferred
    state["approved_by"] = args.approver
    save_state(state)

    print(f"\nStatus: {state['status']}  (zapisano: {len(written)}, v outboxu: {len(deferred)})")
    if deferred:
        print("CRM ni bil dosegljiv. Nic ni izgubljeno — zazeni 'python pipeline.py replay'.")
    print("E-posta je pripravljena kot osnutek v postnem predalu svetovalca; poslje jo clovek.")


# ------------------------------------------------------------------------ replay


def cmd_replay(args: argparse.Namespace) -> None:
    crm = CrmClient(base_url=args.crm_url, max_retries=args.max_retries)
    sent, pending = crm.replay_outbox()
    print(f"Oddano: {sent}, se caka: {pending}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Avtomatizacija obdelave zapisnikov sestankov")
    parser.add_argument("--crm-url", default="http://127.0.0.1:8099")
    parser.add_argument("--max-retries", type=int, default=5)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="ekstrakcija + kontrole")
    p_run.add_argument("--transcript", required=True)
    p_run.add_argument("--offline", action="store_true", help="brez klica modela")
    p_run.add_argument("--fixture", help="uporabi konkreten shranjen odgovor modela (demo)")
    p_run.set_defaults(func=cmd_run)

    p_rev = sub.add_parser("review", help="prikaz paketa za pregled")
    p_rev.add_argument("meeting_id")
    p_rev.set_defaults(func=cmd_review)

    p_app = sub.add_parser("approve", help="potrditev in zapis v CRM")
    p_app.add_argument("meeting_id")
    p_app.add_argument("--approver", default="ana.kovac@svetovanje.si")
    p_app.add_argument("--force", action="store_true")
    p_app.set_defaults(func=cmd_approve)

    p_rep = sub.add_parser("replay", help="ponovna oddaja iz outboxa")
    p_rep.set_defaults(func=cmd_replay)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

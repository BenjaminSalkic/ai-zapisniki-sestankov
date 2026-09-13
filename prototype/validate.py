"""Korak 2: kontrolna plast med AI-jem in poslovnim sistemom.

Shema nam zagotovi *obliko*, tukaj preverimo *vsebino*. Vsaka kontrola vrne
ugotovitev z resnostjo:

  BLOCK  -> podatka ne zapišemo; gre nazaj človeku (ali se odstrani iz zapisa)
  REVIEW -> zapišemo šele po človeški potrditvi
  INFO   -> samo opozorilo v pregledu

Najpomembnejša kontrola je `grounding`: dobesedni citat mora obstajati v
transkriptu. To je poceni, deterministično in ujame tipično halucinacijo —
model, ki si izmisli nalogo ali rok, si mora izmisliti tudi citat.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal

from models import MeetingRecord

Severity = Literal["BLOCK", "REVIEW", "INFO"]

# Prag podobnosti za citat. 1.0 = dobesedno ujemanje. Dovolimo manjše odstopanje
# (velike/male črke, presledki, ločila), ne pa prostega preoblikovanja.
QUOTE_SIMILARITY_THRESHOLD = 0.90
MIN_QUOTE_LEN = 25
MAX_DUE_DATE_HORIZON_DAYS = 365


@dataclass
class Finding:
    severity: Severity
    path: str
    message: str


@dataclass
class ValidationResult:
    findings: list[Finding] = field(default_factory=list)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "BLOCK"]

    @property
    def needs_review(self) -> bool:
        return any(f.severity in ("BLOCK", "REVIEW") for f in self.findings)

    def add(self, severity: Severity, path: str, message: str) -> None:
        self.findings.append(Finding(severity, path, message))


_FOLD = str.maketrans("čćšžđČĆŠŽĐ", "ccszdCCSZD")


def fold_name(name: str) -> str:
    """Poenostavljena oblika imena — brez diakritik, male crke.

    Prepisi sestankov in kadrovski imenik se pogosto razlikujeta v sicnikih;
    to ne sme biti razlog za blokado.
    """
    return name.translate(_FOLD).strip().lower()


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def quote_is_grounded(quote: str, transcript_norm: str) -> bool:
    """Ali se citat (skoraj) dobesedno pojavi v transkriptu?"""
    q = _normalize(quote)
    if not q:
        return False
    if q in transcript_norm:
        return True
    # Drseče okno z isto dolžino kot citat — ujame drobne razlike v prepisu.
    matcher = difflib.SequenceMatcher(None, q, transcript_norm)
    match = matcher.find_longest_match(0, len(q), 0, len(transcript_norm))
    return (match.size / len(q)) >= QUOTE_SIMILARITY_THRESHOLD


def validate(
    record: MeetingRecord,
    transcript: str,
    meeting_date: str,
    known_employees: set[str],
) -> ValidationResult:
    result = ValidationResult()
    transcript_norm = _normalize(transcript)
    mdate = datetime.strptime(meeting_date, "%Y-%m-%d").date()
    known_folded = {fold_name(n) for n in known_employees}

    def check_quote(path: str, quote: str, severity: Severity = "BLOCK") -> None:
        if len(quote.strip()) < MIN_QUOTE_LEN:
            result.add(severity, path, f"Citat je prekratek za preverjanje: {quote!r}")
            return
        if not quote_is_grounded(quote, transcript_norm):
            result.add(
                severity,
                path,
                f"Citata ni v transkriptu (možna halucinacija): {quote[:80]!r}",
            )

    for i, req in enumerate(record.client_requirements):
        check_quote(f"client_requirements[{i}]", req.evidence.quote)

    for i, dec in enumerate(record.decisions):
        check_quote(f"decisions[{i}]", dec.evidence.quote)

    for i, risk in enumerate(record.risks):
        check_quote(f"risks[{i}]", risk.evidence.quote, severity="REVIEW")

    for i, item in enumerate(record.action_items):
        path = f"action_items[{i}]"
        check_quote(path, item.evidence.quote)

        # Odgovorna oseba mora obstajati v kadrovskem imeniku — ID-ja nikoli ne
        # ustvari model, vedno ga razrešimo z determinističnim iskanjem.
        if item.owner_name is None:
            result.add("REVIEW", path, f"Naloga brez odgovorne osebe: {item.title!r}")
        elif fold_name(item.owner_name) not in known_folded:
            candidates = difflib.get_close_matches(
                fold_name(item.owner_name), known_folded, n=1, cutoff=0.8
            )
            hint = f" Morda: {candidates[0]}?" if candidates else ""
            result.add(
                "BLOCK",
                path,
                f"Odgovorne osebe {item.owner_name!r} ni v imeniku zaposlenih.{hint}",
            )

        if item.due_date is None:
            result.add("REVIEW", path, f"Naloga brez roka: {item.title!r}")
        else:
            try:
                due = date.fromisoformat(item.due_date)
            except ValueError:
                result.add("BLOCK", path, f"Neveljaven datum: {item.due_date!r}")
            else:
                if due < mdate:
                    result.add("BLOCK", path, f"Rok {due} je pred datumom sestanka {mdate}.")
                elif due > mdate + timedelta(days=MAX_DUE_DATE_HORIZON_DAYS):
                    result.add("REVIEW", path, f"Rok {due} je nenavadno daleč v prihodnosti.")

    # Komercialni podatki gredo vedno skozi človeka — cena in časovnica sta
    # podatka z največjo poslovno škodo, če sta narobe.
    c = record.commercial
    if c.budget_mentioned or c.timeline_mentioned or c.upsell_opportunity:
        if c.evidence is None:
            result.add("BLOCK", "commercial", "Komercialni podatek brez citata.")
        else:
            check_quote("commercial", c.evidence.quote)
        result.add("REVIEW", "commercial", "Komercialni podatki zahtevajo potrditev svetovalca.")

    if record.overall_confidence < 0.75:
        result.add(
            "REVIEW",
            "overall_confidence",
            f"Nizka zanesljivost izvlečka ({record.overall_confidence:.2f}).",
        )

    for point in record.unclear_points:
        result.add("INFO", "unclear_points", point)

    if not record.action_items:
        result.add("INFO", "action_items", "Ni zaznanih nalog — preveri, ali je to pričakovano.")

    return result


def strip_blocked(record: MeetingRecord, result: ValidationResult) -> MeetingRecord:
    """Odstrani elemente z BLOCK ugotovitvijo, da ostane varen 'delni zapis'.

    Uporabno, kadar želimo v CRM vseeno zapisati to, kar je preverjeno, ostalo
    pa pustiti človeku v obravnavo.
    """
    blocked = {f.path for f in result.blocking}
    clean = record.model_copy(deep=True)
    clean.action_items = [
        item for i, item in enumerate(record.action_items) if f"action_items[{i}]" not in blocked
    ]
    clean.client_requirements = [
        r for i, r in enumerate(record.client_requirements) if f"client_requirements[{i}]" not in blocked
    ]
    clean.decisions = [
        d for i, d in enumerate(record.decisions) if f"decisions[{i}]" not in blocked
    ]
    return clean

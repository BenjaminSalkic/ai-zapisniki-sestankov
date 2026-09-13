"""Korak 1: iz nestrukturiranega transkripta v strukturirane podatke.

Uporabljamo Claude Opus 5 s "structured outputs" (`client.messages.parse`), kar
pomeni, da API sam vsili JSON shemo — odgovor je vedno veljaven `MeetingRecord`.
S tem odpade cel razred napak (parsanje, manjkajoča polja, napačni tipi) in
ostane nam samo še vsebinsko preverjanje.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import anthropic

from models import MeetingRecord

MODEL = "claude-opus-5"
PROMPT_VERSION = "extract-v3"

SYSTEM_PROMPT = """\
Si natančen analitik sestankov v svetovalnem podjetju. Tvoja naloga je EKSTRAKCIJA,
ne interpretacija in ne pisanje ponudbe.

Trdna pravila:
1. Uporabljaj IZKLJUČNO informacije iz transkripta. Ničesar ne dodajaj iz splošnega
   znanja, ne sklepaj o stvareh, ki niso bile izrečene.
2. Če podatka ni, vrni null oziroma prazen seznam. Prazno polje je pravilen odgovor;
   izmišljen podatek je najhujša možna napaka.
3. Vsako trditev (zahteva, odločitev, naloga, tveganje) podpri s poljem `evidence.quote`,
   ki je DOBESEDEN odlomek iz transkripta — kopiran znak za znak, brez preoblikovanja.
   Odlomek mora biti dolg vsaj 25 znakov in mora se dobesedno pojaviti v transkriptu.
4. Odgovorno osebo (`owner_name`) navedi samo, če je bila izrecno določena. "Nekdo bo
   pogledal" ni odgovorna oseba.
5. Roke pretvori v absolutne datume glede na podan datum sestanka. Če je rok ohlapen
   ("nekje v januarju"), ga ne ugibaj — pusti null in dodaj opombo v `unclear_points`.
6. Zneskov, odstotkov in imen ne zaokrožuj in ne popravljaj.
7. Vse, kar je dvoumno, sporno ali le namignjeno, navedi v `unclear_points`.
8. Osnutek e-pošte naj povzema samo dogovorjeno. Brez novih obljub, brez cen, ki niso
   bile izrečene, brez rokov, ki niso bili potrjeni.

Piši v jeziku transkripta.
"""

USER_TEMPLATE = """\
Metapodatki sestanka:
- datum: {meeting_date}
- naslov iz koledarja: {calendar_title}
- udeleženci iz koledarja: {calendar_participants}
- stranka (iz CRM, potrjeno): {client_name}

Transkript:
<transkript>
{transcript}
</transkript>

Iz transkripta izlušči strukturiran zapis po shemi.
"""


@dataclass
class MeetingContext:
    meeting_date: str
    calendar_title: str
    calendar_participants: list[str]
    client_name: str
    crm_account_id: str


def extract(transcript: str, ctx: MeetingContext) -> MeetingRecord:
    """Pokliče Claude in vrne validiran MeetingRecord."""
    client = anthropic.Anthropic()

    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                # Sistemski prompt je stabilen -> predpomnjenje zniža strošek.
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": USER_TEMPLATE.format(
                    meeting_date=ctx.meeting_date,
                    calendar_title=ctx.calendar_title,
                    calendar_participants=", ".join(ctx.calendar_participants),
                    client_name=ctx.client_name,
                    transcript=transcript,
                ),
            }
        ],
        output_format=MeetingRecord,
    )

    if response.stop_reason == "refusal":
        raise RuntimeError(f"Model je zavrnil zahtevo: {response.stop_details}")

    return response.parsed_output


def extract_offline(fixture_path: str | Path) -> MeetingRecord:
    """Demo brez API ključa: prebere shranjen odgovor modela iz prejšnjega teka.

    Uporabno za testiranje preostalega pipelina (validacija, CRM, retry) brez
    stroškov in brez omrežja — isti podatkovni tip kot pravi klic.
    """
    data = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    return MeetingRecord.model_validate(data)


def has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

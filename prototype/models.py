"""Strukturirana shema, ki jo mora AI vrniti iz transkripta sestanka.

Shemo uporabimo na dva načina:
  1. kot `output_format` za Claude (structured outputs) -> model *ne more* vrniti
     neveljavnega JSON-a, tipi in enumi so garantirani na ravni API-ja,
  2. kot validacijsko plast pred zapisom v CRM.

Ključna ideja proti halucinacijam: vsako polje, ki ga bomo zapisali v poslovni
sistem, nosi s sabo `evidence` (dobesedni citat iz transkripta) in `confidence`.
Citat nato programsko preverimo — če ga v transkriptu ni, podatku ne zaupamo.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class Priority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Sentiment(str, Enum):
    positive = "positive"
    neutral = "neutral"
    at_risk = "at_risk"


class Evidence(BaseModel):
    """Dobesedni odlomek iz transkripta, ki podpira trditev."""

    quote: str = Field(description="Dobesedni citat iz transkripta (kopiraj znak za znak).")
    speaker: Optional[str] = Field(default=None, description="Kdo je to izjavil.")


class ActionItem(BaseModel):
    title: str = Field(description="Kratek, glagolski opis naloge.")
    description: str = Field(description="Kaj konkretno je treba narediti.")
    owner_name: Optional[str] = Field(
        default=None,
        description=(
            "Ime osebe, ki je odgovorna, TOČNO tako kot je zapisano v transkriptu. "
            "Če odgovorna oseba ni bila izrecno določena, vrni null. Ne ugibaj."
        ),
    )
    due_date: Optional[str] = Field(
        default=None,
        description=(
            "Rok v obliki YYYY-MM-DD. Relativne izraze ('do naslednjega tedna') "
            "pretvori glede na datum sestanka. Če rok ni bil omenjen, vrni null."
        ),
    )
    priority: Priority = Priority.medium
    evidence: Evidence


class Decision(BaseModel):
    statement: str = Field(description="Odločitev, sprejeta na sestanku.")
    evidence: Evidence


class Risk(BaseModel):
    statement: str = Field(description="Tveganje, blokada ali odprto vprašanje.")
    evidence: Evidence


class ClientRequirement(BaseModel):
    requirement: str = Field(description="Konkretna zahteva ali potreba stranke.")
    evidence: Evidence


class CommercialSignal(BaseModel):
    """Komercialni podatki — najbolj tvegana kategorija, zato vedno opcijska."""

    budget_mentioned: Optional[str] = Field(
        default=None, description="Znesek/razpon točno kot je bil omenjen. Sicer null."
    )
    timeline_mentioned: Optional[str] = Field(
        default=None, description="Časovnica točno kot je bila omenjena. Sicer null."
    )
    upsell_opportunity: Optional[str] = Field(
        default=None, description="Zaznana prodajna priložnost. Sicer null."
    )
    evidence: Optional[Evidence] = None


class MeetingRecord(BaseModel):
    """Celoten strukturiran izvleček enega sestanka."""

    meeting_title: str
    client_name: Optional[str] = Field(
        default=None, description="Ime podjetja stranke, kot je omenjeno. Sicer null."
    )
    participants: List[str] = Field(description="Imena udeležencev, ki nastopajo v transkriptu.")
    summary: str = Field(description="Strnjen povzetek v 4–6 stavkih, v jeziku transkripta.")
    client_requirements: List[ClientRequirement]
    decisions: List[Decision]
    action_items: List[ActionItem]
    risks: List[Risk]
    commercial: CommercialSignal
    sentiment: Sentiment
    followup_email_draft: str = Field(
        description=(
            "Osnutek follow-up e-pošte stranki: vljuden, konkreten, povzame dogovorjeno "
            "in navede naslednje korake z roki. Brez izmišljenih obljub."
        )
    )
    unclear_points: List[str] = Field(
        description=(
            "Stvari, ki iz transkripta niso jasne in jih mora potrditi človek. "
            "Sem raje daj preveč kot premalo."
        )
    )
    overall_confidence: float = Field(
        ge=0.0, le=1.0, description="Splošna zanesljivost izvlečka (0–1)."
    )

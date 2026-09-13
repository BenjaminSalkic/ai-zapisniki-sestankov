# Avtomatizacija obdelave zapisnikov sestankov

**Benjamin Salkič** · praktična naloga · september 2026

**Koda:** https://github.com/BenjaminSalkic/ai-zapisniki-sestankov
**Dokument:** https://claude.ai/code/artifact/3193adfd-df02-4598-a0be-c805787e00e6

Prototip je v [`prototype/`](prototype/), navodila za zagon v
[prototype/README.md](prototype/README.md).

---

## Izhodišče

Svetovalec po sestanku opravi pet opravil: prebere transkript, izlušči zahteve,
napiše povzetek, vnese naloge v sistem in sestavi follow-up. Od tega so štiri
mehanska, eno pa ni: **presoja, ali je izluščeno res tisto, kar je bilo
dogovorjeno.** Zato cilj ni »AI naredi vse«, ampak: AI naredi 90 % dela, človek
pa v eni minuti potrdi ali popravi. Sistem, ki v 95 % deluje in v 5 % tiho
zapiše napačen rok stranki, je slabši od ročnega dela — ker nihče ne ve, katerih
5 % je.

Iz tega sledita dve načeli, ki držita celotno zasnovo:

1. **Zapis v sistem in komunikacija s stranko gresta vedno skozi človeka.**
   Notranji povzetek je poceni napaka, e-pošta stranki ni.
2. **Vsak podatek nosi dokaz.** Model mora za vsako trditev vrniti dobesedni
   citat iz transkripta, ki ga nato programsko preverimo.

## 1. Potek od začetka do konca

```
Sestanek ──► Transkript ──► Priprava ──► Ekstrakcija ──► Kontrole ──► Potrditev ──► Zapis
             (Teams/Zoom)   (kontekst)    (Claude)      (determ.)     (Slack)      (CRM + osnutek)
                                                            │                          │
                                                       padle kontrole             CRM ne dela
                                                            ▼                          ▼
                                                     svetovalec popravi            outbox → replay
```

1. **Prožilec.** Sestanek se konča, orodje za zapisovanje (Teams / Zoom / Fireflies)
   preko webhooka javi, da je transkript pripravljen. Vse naprej teče asinhrono v
   vrsti — nič ni vezano na to, da je uporabnik pred zaslonom.
2. **Priprava konteksta.** Iz koledarja in CRM-ja pobere datum, udeležence in
   **potrjen ID stranke**. To je pomembna podrobnost: stranke ne identificira
   model iz besedila, ampak deterministično ujemanje po e-poštni domeni
   udeležencev. Model nikoli ne ugiba, v čigav zapis piše.
3. **Ekstrakcija.** En klic Clauda z vsiljeno JSON shemo vrne povzetek, zahteve,
   odločitve, naloge z lastniki in roki, tveganja, komercialne signale, osnutek
   e-pošte in seznam nejasnosti.
4. **Kontrole.** Determinističen sloj brez AI-ja: preveri citate, roke, lastnike,
   pragove zaupanja. Rezultat je razvrstitev na `BLOCK` / `REVIEW` / `INFO`.
5. **Potrditev.** Svetovalec dobi v Slacku že izpolnjen paket z označenimi
   spornimi mesti in gumbi `Potrdi` / `Uredi` / `Zavrni`. Cilj je 30–60 sekund.
6. **Zapis.** Po potrditvi gre zapis o sestanku in naloge v CRM preko API-ja,
   follow-up pa se shrani **kot osnutek** v svetovalčev poštni predal. Sistem
   e-pošte ne pošlje sam.

## 2. Orodja in zakaj

| Plast | Izbira | Razlog |
|---|---|---|
| Transkripcija | obstoječe orodje za sestanke (Teams/Zoom/Fireflies) | To je rešen problem. Lasten Whisper le, če podatki ne smejo iz hiše. |
| Orkestracija | **n8n** (self-hosted) | Vizualen potek, vgrajeni retry in webhooki, in — bistveno — ne-razvijalec v podjetju lahko kasneje sam popravi prompt ali doda korak. Če bi bila prioriteta popoln nadzor, bi bila alternativa Python + Temporal; n8n je hitrejši do vrednosti. |
| Model | **Claude Opus 5** | Structured outputs (API vsili shemo), dobro delo s slovenščino, dovolj velik kontekst za cel transkript. Ceneje: Sonnet 5 za rutinske sestanke, Opus za kompleksne. |
| Kontrole | navaden Python | Preverjanje citatov in datumov je deterministično opravilo. Za to ni razloga uporabiti AI-ja. |
| Potrditev | Slack (Block Kit) | Svetovalci so tam. Nova aplikacija pomeni novo vedenje, ki ga ni. |
| Stanje | Postgres | Vsak sestanek ima status; brez tega ni vidnosti in ni ponovnega zagona. |

### Kako se n8n in Python dopolnjujeta

n8n ni nadomestilo za kodo, ampak ovojnica okoli nje. Meja teče takole:

- **n8n je vodovod.** Webhook ob koncu sestanka, branje konteksta iz koledarja in
  CRM-ja, vrsta in ponovni poskusi, Slack sporočilo z gumbi, čakanje na
  potrditev, klic nazaj.
- **Python je logika.** Shema, prompt, klic modela preko SDK-ja, preverjanje
  citatov, poslovna pravila, razrešitev lastnikov in ID-jev, idempotenca, outbox.

V produkciji je prototipov paket izpostavljen kot dve HTTP funkciji, ki ju n8n
pokliče:

```
POST /extract   transkript + kontekst   ->  izvleček + ugotovitve kontrol
POST /commit    potrjen izvleček        ->  zapis v CRM + osnutek e-pošte
```

Razlog za tako razmejitev je praktičen. Prompt in kontrole je treba verzionirati,
pokriti z eval setom in pregledati v code reviewju — klikanje po vizualnem
urejevalniku tega ne prenese. Vodovod pa je ravno tisto, kar se v podjetju
najpogosteje spreminja (drug kanal za potrditev, dodatno polje, drug prejemnik),
in prav je, da za to ni treba razvijalca.

## 3. Kako AI dobi strukturo iz nestrukturiranega besedila

Ključna izbira je **structured outputs**: shemo podamo API-ju, ta pa jamči, da
bo odgovor veljaven JSON z zahtevanimi polji in tipi. S tem odpade cel razred
napak (manjkajoča polja, JSON v markdownu, napačni tipi) in ostane samo še
vsebinsko preverjanje.

```python
response = client.messages.parse(
    model="claude-opus-5",
    system=SYSTEM_PROMPT,            # stabilen -> prompt caching
    messages=[{"role": "user", "content": user_prompt}],
    output_format=MeetingRecord,     # Pydantic shema
)
record = response.parsed_output      # validiran objekt, ne niz
```

Bistvo prompta (celoten v [`prototype/extract.py`](prototype/extract.py)):

> Si natančen analitik sestankov. Tvoja naloga je **ekstrakcija, ne
> interpretacija**.
> 1. Uporabljaj izključno informacije iz transkripta.
> 2. Če podatka ni, vrni `null` ali prazen seznam. **Prazno polje je pravilen
>    odgovor; izmišljen podatek je najhujša možna napaka.**
> 3. Vsako trditev podpri z `evidence.quote` — dobesednim odlomkom, kopiranim
>    znak za znak, dolgim vsaj 25 znakov.
> 4. Odgovorno osebo navedi samo, če je bila izrecno določena. »Nekdo bo
>    pogledal« ni odgovorna oseba.
> 5. Roke pretvori v absolutne datume glede na datum sestanka. Ohlapnih rokov ne
>    ugibaj — pusti `null` in dodaj opombo v `unclear_points`.
> 6. Zneskov in imen ne zaokrožuj in ne popravljaj.

Izsek sheme — cela je v [`prototype/models.py`](prototype/models.py):

```json
{
  "action_items": [{
    "title": "Izvesti pilot in predstaviti rezultate natančnosti",
    "owner_name": "Marko Zupan",
    "due_date": "2026-09-30",
    "priority": "high",
    "evidence": { "quote": "Rezultate pilota lahko predstavim do konca septembra, recimo tridesetega.",
                  "speaker": "Marko Zupan" }
  }],
  "commercial": { "budget_mentioned": "6.000 EUR fiksno za pilot", "evidence": {...} },
  "unclear_points": ["Rok za pošiljanje predloga pogodbe ni bil izrecno dogovorjen."],
  "overall_confidence": 0.89
}
```

Polji `evidence` in `unclear_points` sta srce rešitve. Nista okras — sta vhod za
naslednji korak.

## 4. Zapis v poslovni sistem

Po potrditvi dva klica: `POST /v1/accounts/{id}/meeting-notes` in `POST /v1/tasks`
za vsako nalogo. Trije detajli, ki odločajo, ali bo to zdržalo produkcijo:

- **Lastnika naloge razreši sistem, ne model.** Model vrne ime, sistem ga ujame
  v kadrovskem imeniku in šele takrat nastane `owner_id`. Model ID-ja nikoli ne
  napiše. Enako velja za ID stranke in šifrante statusov.
- **Idempotenčni ključ** = hash(`meeting_id` + tip + vsebina). Ponovni poskus po
  timeoutu vrne prvotni zapis namesto podvojene naloge. V prototipu spoštuje to
  tudi lažni CRM.
- **Sledljivost.** Vsak zapis nosi `transcript_sha256`, verzijo prompta, model in
  e-pošto potrjevalca. Čez tri mesece se da za katerokoli nalogo ugotoviti, iz
  česa je nastala in kdo jo je potrdil.

Naloge, katerih lastnik je na strani stranke, ostanejo v zapisu o sestanku in se
ne odpirajo kot interni taski.

## 5. Kako preprečimo napačne ali izmišljene podatke

Pet plasti, od najcenejše do najdražje:

| # | Kontrola | Kaj ujame |
|---|---|---|
| 1 | **Shema (structured outputs)** | Napačna oblika, manjkajoča polja, izmišljene vrednosti enumov. Nemogoče po konstrukciji. |
| 2 | **Preverjanje citatov** | Vsak `evidence.quote` mora biti v transkriptu (normalizirano ujemanje, prag 0.90). Najmočnejša kontrola: model, ki si izmisli nalogo, si mora izmisliti tudi citat — in ta pade. |
| 3 | **Poslovna pravila** | Lastnik obstaja v imeniku; rok ni pred sestankom in ni več kot leto naprej; datum je veljaven; komercialni podatek ima citat. |
| 4 | **Pragovi in usmerjanje** | `overall_confidence < 0.75`, naloga brez lastnika ali roka, vsak komercialni podatek → obvezen človeški pregled. |
| 5 | **Človeška potrditev** | Obvezna pred zapisom v CRM in pred vsakim stikom s stranko. Nepogojno. |

Razvrstitev po resnosti: `BLOCK` (ne gre skozi), `REVIEW` (samo s potrditvijo),
`INFO` (opozorilo v pregledu).

V prototipu je to pognano na namerno pokvarjenem izvlečku z izmišljeno nalogo
»podpis pogodbe za 45.000 EUR« — kontrole jo ujamejo štirikrat:

```
[BLOCK ] action_items[4]: Citata ni v transkriptu (možna halucinacija): 'Strinjamo se s ceno…'
[BLOCK ] action_items[4]: Odgovorne osebe 'Janez Kranjc' ni v imeniku zaposlenih.
[BLOCK ] action_items[4]: Rok 2026-09-01 je pred datumom sestanka 2026-09-08.
[BLOCK ] commercial:      Citata ni v transkriptu (možna halucinacija): 'Celotna implementacija…'
=== Status: blocked ===
```

**Kar bi dodal pred produkcijo:** eval set 30–50 zgodovinskih transkriptov z
ročno označenimi pravilnimi izhodi, ki teče ob vsaki spremembi prompta. Brez
tega je vsaka sprememba prompta ugibanje. Merim dvoje: koliko pravih nalog je
sistem zgrešil in koliko izmišljenih je spustil skozi — drugo je dražje.

## 6. Kaj, če kakšen sistem ne dela

Vse odpornost izhaja iz ene odločitve: **transkript je izvorna resnica, vsak
korak pa je ponovljiv iz shranjenega stanja.** Zato noben izpad ne pomeni
ponovnega ročnega dela.

| Odpove | Odziv |
|---|---|
| **Model (API)** | SDK sam ponovi ob 429/5xx; ob daljšem izpadu gre sestanek v vrsto in se obdela kasneje. Zamuda ni škoda — svetovalec dobi obvestilo. |
| **CRM API** | Do 5 poskusov z eksponentnim odlogom + jitter, samo za prehodne napake (429, 5xx, timeout). Nato **outbox**: potrjen zapis gre na disk s statusom `pending` in se odda, ko je CRM spet gor. Idempotenčni ključi poskrbijo, da se nič ne podvoji. |
| **CRM zavrne (4xx)** | Ponavljanje nima smisla — gre v dead-letter in svetovalcu z razlogom. |
| **Slack** | Nadomestni kanal je e-pošta; sestanek ostane v stanju `awaiting_approval`, dokler ga nekdo ne obdela. |
| **Transkripcija** | Nič ni izgubljeno: posnetek ostane, obdelava se ponovi. |

Preizkušeno v prototipu — pipeline ob ugasnjenem CRM-ju odloži tri zapise v
outbox, po ponovnem zagonu (celo z 40 % napak) pa jih vse odda brez podvojitev.

Poleg tega: **vsak sestanek ima status** (`extracted` → `awaiting_approval` →
`written` / `blocked` / `partially_written`), kar pomeni, da se da kadarkoli
vprašati »kaj visi in zakaj«. Sestanek, ki več kot 48 ur čaka na potrditev,
sproži opomnik — najpogostejši tihi odpoved ni tehnična, ampak da nekdo pozabi
klikniti `Potrdi`.

## Kaj bi naredil naslednje

1. Eval set na resničnih transkriptih — brez meritve je vse ostalo mnenje.
2. Dva tedna v načinu »AI predlaga, človek vedno potrdi«, z merjenjem, koliko
   popravkov je dejansko potrebnih.
3. Šele če je delež popravkov nizek, bi za nizko tvegane dele (notranji povzetek,
   naloge z jasnim lastnikom in rokom) razmislil o samodejni potrditvi.
   Komercialni podatki in komunikacija s stranko ostanejo pri človeku vedno.

---

### Opomba o uporabi AI orodij

Nalogo sem reševal s Claude Code. Uporabno je bilo predvsem za hitro pisanje
prototipa in kode brez zunanjih odvisnosti. Sam sem določil zasnovo — zlasti
idejo, da vsak podatek nosi dobesedni citat, ki ga nato preverimo programsko, in
strogo ločnico med tem, kar sme zapisati model (imena, besedilo), in tem, kar
razreši sistem (ID-ji, šifranti). Prvi osnutek prompta je bil preveč popustljiv
(»če nisi prepričan, oceni«), kar je natanko nasprotje tega, kar želimo — zato
je zdaj v pravilih izrecno, da je prazno polje pravilen odgovor.

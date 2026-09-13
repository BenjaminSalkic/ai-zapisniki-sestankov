# Prototip: od transkripta do CRM-ja

Delujoč prototip osrednjega dela rešitve. Pokriva ekstrakcijo s Claudom,
kontrolno plast proti halucinacijam, človeško potrditev in odporen zapis v
poslovni sistem (retry, idempotenca, outbox).

V produkciji je to tisti del, ki teče **za** n8n-om: n8n skrbi za webhooke,
Slack in vrsto, ta paket pa za shemo, prompt, kontrole in zapis preko API-ja.
Izpostavljen bi bil kot `POST /extract` in `POST /commit`; CLI spodaj je ista
logika, le da jo poganja človek namesto orkestratorja.

## Zagon

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 1. terminal — lažni CRM
.venv/bin/python mock_crm.py --port 8099

# 2. terminal — pipeline
export ANTHROPIC_API_KEY=sk-ant-...          # brez ključa uporabi --offline
.venv/bin/python pipeline.py run --transcript samples/transcript_01.txt
.venv/bin/python pipeline.py approve MTG-<id>
```

Brez API ključa deluje vse ostalo — `--offline` prebere shranjen odgovor modela
iz `samples/extraction_01.json`, tako da se da kontrole, CRM in odpornost
preizkusiti brez stroškov.

## Trije scenariji, ki jih je vredno pognati

**1. Normalen potek**

```bash
.venv/bin/python pipeline.py run --transcript samples/transcript_01.txt --offline
.venv/bin/python pipeline.py approve MTG-d53553cfeb
```

Zapiše en `meeting-note` in dve nalogi. Ponoven `approve` zaradi idempotenčnih
ključev ne ustvari dvojnikov — CRM vrne prvotne zapise.

**2. Halucinacija**

```bash
.venv/bin/python pipeline.py run --transcript samples/transcript_01.txt \
  --fixture samples/extraction_01_halucinacija.json
```

Fixture vsebuje izmišljeno nalogo (»podpis pogodbe za 45.000 EUR«). Kontrolna
plast jo ujame štirikrat: citata ni v transkriptu, odgovorne osebe ni v imeniku,
rok je pred datumom sestanka, komercialni citat je izmišljen. Status postane
`blocked` in v CRM ne gre nič.

**3. CRM ne dela**

```bash
# ugasni mock_crm.py
.venv/bin/python pipeline.py --max-retries 3 approve MTG-d53553cfeb
# -> 3 poskusi z eksponentnim odlogom, nato outbox

# prizgi ga nazaj, po možnosti nestabilnega
.venv/bin/python mock_crm.py --fail-rate 0.4
.venv/bin/python pipeline.py replay
```

Nič se ne izgubi in nič se ne podvoji.

## Datoteke

| Datoteka | Vloga |
|---|---|
| `models.py` | Pydantic shema izhoda — hkrati JSON shema za structured outputs |
| `extract.py` | Prompt + klic Claude Opus 5 (`messages.parse`) |
| `validate.py` | Kontrolna plast: preverjanje citatov, rokov, lastnikov, pragov |
| `pipeline.py` | Orkestrator + CLI (`run`, `review`, `approve`, `replay`) |
| `crm_client.py` | HTTP klient: retry, idempotenca, outbox, dead-letter |
| `mock_crm.py` | Lažni poslovni sistem, zna simulirati izpade |
| `samples/` | Transkript + shranjena odgovora modela (pravilen in pokvarjen) |

## Kaj prototip namenoma ne pokriva

Transkripcijo zvoka, pravo Slack integracijo, avtentikacijo, bazo (stanje je v
JSON datotekah v `.state/`) in eval set. To so znane, rešljive stvari; namen
prototipa je pokazati logiko, ne produkcijske infrastrukture.

Opomba o preverjanju: potek, kontrole, idempotenca in outbox so pognani in
delujejo. Klic na pravi API (`extract()`) v tem okolju ni bil izveden, ker ni
bilo na voljo API ključa — koda sledi dokumentirani uporabi SDK-ja
(`client.messages.parse` + `output_format`), preverjen pa je bil vse ostalo.

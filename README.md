# Avtomatizacija obdelave zapisnikov sestankov

**Benjamin Salkič** · praktična naloga · september 2026

**Koda:** https://github.com/BenjaminSalkic/ai-zapisniki-sestankov
**Dokument:** https://claude.ai/code/artifact/3193adfd-df02-4598-a0be-c805787e00e6

Prototip je v [`prototype/`](prototype/), navodila za zagon v
[prototype/README.md](prototype/README.md).

---

## Izhodišče

Ko sem razmišljal, kaj je pri tej nalogi pravzaprav težko, sem ugotovil, da ni
težko nič od tistega, kar svetovalec po sestanku počne ročno. Prebrati
transkript, izluščiti zahteve, napisati povzetek, vnesti naloge v sistem — vse
to model naredi v enem klicu in bo v večini primerov naredil dobro.

Težko je nekaj drugega: presoditi, ali je izluščeno res tisto, kar je bilo
dogovorjeno. Zato si nisem zastavil cilja, da bi AI naredil vse. Cilj je, da
naredi devetdeset odstotkov, človek pa v eni minuti potrdi ali popravi.

Razlog za tako zastavitev je preprost. Sistem, ki v petindevetdesetih odstotkih
primerov deluje, v petih pa tiho zapiše napačen rok ali napačno ceno, je za
uporabnika slabši od ročnega dela, ker nihče ne ve, katerih pet odstotkov je.
Ljudje takemu sistemu nehajo zaupati precej hitreje, kot se sistem izboljša, in
potem za vsak slučaj vse preverjajo znova. Prihranek izgine, delo pa ostane.

Vse ostalo izhaja iz dveh odločitev, ki sem ju sprejel na začetku:

1. **Zapis v sistem in vsak stik s stranko gresta skozi človeka.** Napačen
   notranji povzetek nekdo opazi in popravi. Napačna e-pošta stranki je že zunaj.
2. **Vsak podatek nosi dokaz.** Za vsako trditev mora model vrniti dobesedni
   citat iz transkripta. Citat potem preverimo s kodo, ne z drugim modelom.

## 1. Potek

```
Sestanek ──► Transkript ──► Priprava ──► Ekstrakcija ──► Kontrole ──► Potrditev ──► Zapis
             (Teams/Zoom)   (kontekst)    (Claude)      (determ.)     (Slack)      (CRM + osnutek)
                                                            │                          │
                                                       padle kontrole             CRM ne dela
                                                            ▼                          ▼
                                                     svetovalec popravi            outbox → replay
```

1. **Prožilec.** Sestanek se konča in orodje za zapisovanje, karkoli podjetje že
   uporablja, preko webhooka javi, da je transkript pripravljen. Od tu naprej
   vse teče asinhrono v vrsti. Nič ne sme biti odvisno od tega, ali je takrat
   kdo pred zaslonom.
2. **Priprava konteksta.** Datum, udeležence in ID stranke poberemo iz koledarja
   in CRM-ja, ne iz besedila. To se zdi podrobnost, pa ni: stranko ujamemo po
   e-poštni domeni udeležencev, tako da model nikoli ne odloča, v čigav zapis
   pišemo.
3. **Ekstrakcija.** En sam klic modela z vsiljeno JSON shemo. Ven pride povzetek,
   zahteve stranke, odločitve, naloge z lastniki in roki, tveganja, komercialni
   signali, osnutek e-pošte in seznam stvari, ki modelu niso bile jasne.
4. **Kontrole.** Sloj navadne kode brez AI-ja, ki preveri citate, roke, lastnike
   in pragove zaupanja. Vsaka ugotovitev dobi eno od treh stopenj: `BLOCK`,
   `REVIEW`, `INFO`.
5. **Potrditev.** Svetovalec dobi v Slacku že izpolnjen paket, s posebej
   označenimi mesti, ki so padla na kontrolah, in tremi gumbi. Računam na pol
   minute do minute dela. Če bo trajalo dlje, se tega ljudje ne bodo držali in
   smo tam, kjer smo bili.
6. **Zapis.** Zapis o sestanku in naloge gredo v CRM preko API-ja, follow-up pa
   se shrani kot osnutek v svetovalčev poštni predal. Pošlje ga človek. Ne zato,
   ker model ne bi znal napisati spodobnega sporočila, ampak ker je e-pošta
   stranki edina stvar v celotni verigi, ki je ni mogoče vzeti nazaj.

## 2. Orodja

To je moja izbira, ne edina prava. Pri kombinaciji n8n in Pythona bi razumel
ugovor, zato jo pojasnim pod tabelo.

| Plast | Izbira | Razlog |
|---|---|---|
| Transkripcija | obstoječe orodje za sestanke | Teams, Zoom ali Fireflies to že znajo. Lasten Whisper bi imel smisel samo, če posnetki ne smejo iz hiše. |
| Orkestracija | n8n (self-hosted) | Vizualen potek, vgrajeni webhooki in ponovni poskusi, predvsem pa lahko kasneje kdo, ki ni razvijalec, sam doda korak ali zamenja kanal. Za popoln nadzor bi vzel Python in Temporal, a bi do prve uporabne verzije prišel počasneje. |
| Model | Claude Opus 5 | Structured outputs, spodobna slovenščina in dovolj velik kontekst, da gre cel transkript notri brez rezanja. Če bi bil strošek problem, bi rutinske sestanke pognal na Sonnetu. |
| Kontrole | navaden Python | Ali se citat pojavi v besedilu in ali je datum za datumom sestanka, sta vprašanji, ki imata en sam pravilen odgovor. Za to ne rabim modela. |
| Potrditev | Slack | Svetovalci so tam. Nova aplikacija pomeni novo navado, ki je ni. |
| Stanje | Postgres | Vsak sestanek ima status. Brez tega ne veš, kaj visi, in ne moreš ničesar pognati še enkrat. |

### Kje je meja med n8n in Pythonom

Na prvi pogled se podvajata, po mojem pa se ne. n8n sem izbral za vodovod:
webhook ob koncu sestanka, branje konteksta, vrsta in ponovni poskusi, Slack
sporočilo z gumbi, čakanje na potrditev, klic nazaj. Python pa za logiko: shemo,
prompt, klic modela, preverjanje citatov, poslovna pravila, razrešitev imen v
ID-je, idempotenco in outbox.

V produkciji bi bil paket iz prototipa izpostavljen kot dva klica, ki ju n8n
uporabi:

```
POST /extract   transkript + kontekst  ->  izvleček + ugotovitve kontrol
POST /commit    potrjen izvleček       ->  zapis v CRM + osnutek e-pošte
```

Meja teče tam, kjer teče razlika v tem, kako se stvari spreminjajo. Prompt in
kontrole je treba verzionirati, pokriti s testi in pregledati, preden gredo v
produkcijo, česar v vizualnem urejevalniku ni mogoče početi resno. Vodovod pa se
spreminja pogosto in navadno tako, da za to ne bi smel biti potreben razvijalec:
drug kanal za potrditev, dodatno polje, drug prejemnik.

## 3. Ekstrakcija in shema

Namesto da bi model prosil za JSON in potem upal, mu shemo vsilimo preko API-ja.
Odgovor je s tem zagotovo veljaven JSON s pravimi polji in tipi. Sliši se kot
malenkost, v praksi pa odpade cel razred sitnosti: manjkajoča polja, JSON zavit
v markdown, niz tam, kjer pričakuješ število.

```python
response = client.messages.parse(
    model="claude-opus-5",
    system=SYSTEM_PROMPT,            # stabilen -> prompt caching
    messages=[{"role": "user", "content": user_prompt}],
    output_format=MeetingRecord,     # Pydantic shema
)
record = response.parsed_output      # validiran objekt, ne niz
```

Prompt je krajši, kot sem pričakoval. Večino dela opravi shema, prompt pa skrbi
predvsem za to, da model ne zapolnjuje praznin (celoten je v
[`prototype/extract.py`](prototype/extract.py)):

> Si natančen analitik sestankov. Tvoja naloga je **ekstrakcija, ne
> interpretacija**.
> 1. Uporabljaj izključno informacije iz transkripta.
> 2. Če podatka ni, vrni `null` ali prazen seznam. **Prazno polje je pravilen
>    odgovor; izmišljen podatek je najhujša možna napaka.**
> 3. Vsako trditev podpri z `evidence.quote`, dobesednim odlomkom, kopiranim
>    znak za znak, dolgim vsaj 25 znakov.
> 4. Odgovorno osebo navedi samo, če je bila izrecno določena. »Nekdo bo
>    pogledal« ni odgovorna oseba.
> 5. Roke pretvori v absolutne datume glede na datum sestanka. Ohlapnih rokov ne
>    ugibaj, pusti `null` in dodaj opombo v `unclear_points`.
> 6. Zneskov in imen ne zaokrožuj in ne popravljaj.

Drugo točko sem moral napisati dvakrat. V prvem osnutku je pisalo nekaj v smislu
»če nisi prepričan, podaj najboljšo oceno«, kar je pri tej nalogi natanko
narobe. Model, ki ocenjuje, bo ocenil tudi rok, ki ga nihče ni izrekel.

Izsek iz strukture, celota je v [`prototype/models.py`](prototype/models.py):

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

Polji `evidence` in `unclear_points` nista tam zaradi lepšega. Prvo uporabi
naslednji korak, drugo pa pride svetovalcu pred oči.

## 4. Zapis preko API-ja

Po potrditvi gresta ven dva klica, `POST /v1/accounts/{id}/meeting-notes` in
`POST /v1/tasks` za vsako nalogo. Bolj kot to, kaj v njiju je, se mi zdi
zanimivo, česa ni.

Model ne napiše nobenega ID-ja. Vrne ime, recimo »Marko Zupan«, sistem pa ga
poišče v kadrovskem imeniku in šele takrat nastane `owner_id`. Isto velja za ID
stranke in za šifrante statusov. Če imena v imeniku ni, naloga ne gre skozi,
ampak gre na pregled, skupaj s predlogom najbližjega ujemanja.

Vsak POST nosi idempotenčni ključ, ki je hash sestanka, tipa zapisa in vsebine.
Brez tega je vsak timeout potencialna podvojena naloga, in ravno timeouti so
tisto, kar se v praksi dogaja najpogosteje.

Vsak zapis nosi še sha256 transkripta, verzijo prompta, ime modela in e-pošto
tistega, ki ga je potrdil. Čez tri mesece, ko bo kdo vprašal, od kod je prišla
neka naloga, bo to edini način, da mu odgovoriš.

Naloge, katerih lastnik je pri stranki, ostanejo zapisane v zapisu o sestanku in
se ne odpirajo kot interni taski.

## 5. Kontrole

To je del, ki mi je vzel največ časa, in po mojem tudi najbolj zanimiv del
naloge.

Splošen nasvet se glasi »dodaj človeka v zanko«, kar drži, samo po sebi pa ne
zadošča. Če človeku pokažeš lepo oblikovan povzetek s petnajstimi postavkami,
jih bo po tretjem sestanku potrdil, ne da bi jih prebral. Zato mora sistem sam
znati povedati, kateri postavki ne zaupa, in človeka usmeriti tja.

Pet plasti, od najcenejše proti najdražji:

| Plast | Kaj ujame |
|---|---|
| Shema (structured outputs) | Napačno obliko, manjkajoča polja, izmišljene vrednosti šifrantov. Po konstrukciji nemogoče. |
| Preverjanje citatov | Vsak `evidence.quote` mora biti v transkriptu, ujemanje je normalizirano s pragom 0,90. |
| Poslovna pravila | Lastnik obstaja v imeniku. Rok ni pred sestankom in ni več kot leto naprej. Datum je veljaven. Komercialni podatek ima citat. |
| Pragovi | `overall_confidence < 0,75`, naloga brez lastnika ali roka in vsak komercialni podatek gredo obvezno na pregled. |
| Človeška potrditev | Obvezna pred zapisom v CRM in pred vsakim stikom s stranko. |

Jedro je druga vrstica. Model, ki si izmisli nalogo, si mora izmisliti tudi
citat, ta pa pade na navadnem iskanju po besedilu. Kontrola ne stane nič in ne
uporablja AI-ja, kar mi je pomembno: idejo, da bi halucinacije lovil z drugim
klicem modela, sem zavrgel, ker problem samo premakne za korak naprej.

### Preizkus

Da to ni ostalo na papirju, sem v izvleček podtaknil nalogo, ki je v transkriptu
ni, podpis pogodbe za 45.000 evrov. Kontrole jo ujamejo štirikrat:

```
[BLOCK ] action_items[4]: Citata ni v transkriptu (možna halucinacija): 'Strinjamo se s ceno…'
[BLOCK ] action_items[4]: Odgovorne osebe 'Janez Kranjc' ni v imeniku zaposlenih.
[BLOCK ] action_items[4]: Rok 2026-09-01 je pred datumom sestanka 2026-09-08.
[BLOCK ] commercial:      Citata ni v transkriptu (možna halucinacija): 'Celotna implementacija…'
[REVIEW] overall_confidence: Nizka zanesljivost izvlečka (0.62).
=== Status: blocked ===
```

Kar tu manjka in bi bilo prvo, kar bi naredil naslednje: eval set tridesetih do
petdesetih resničnih transkriptov z ročno označenimi pravilnimi izhodi, ki teče
ob vsaki spremembi prompta. Brez tega je vsako popravljanje prompta ugibanje, ki
se sliši prepričljivo. Zanimata me dve številki: koliko pravih nalog sistem
zgreši in koliko izmišljenih spusti skozi. Druga je dražja, ker prvo človek
opazi sam.

## 6. Odpornost na izpade

Vse skupaj stoji na eni odločitvi: transkript je izvorna resnica, vsak korak pa
se da ponoviti iz shranjenega stanja. Dokler to drži, noben izpad ne pomeni, da
mora kdo karkoli delati še enkrat ročno.

| Odpove | Odziv |
|---|---|
| Model (API) | SDK sam ponovi ob 429 in 5xx. Ob daljšem izpadu gre sestanek v vrsto in se obdela kasneje. Zamuda pri tej nalogi ni škoda, samo svetovalec mora vedeti, da zapisnik še ni pripravljen. |
| CRM API | Do pet poskusov z eksponentnim odlogom in jitterjem, in to samo za napake, ki so lahko prehodne (429, 5xx, timeout). Če tudi zadnji poskus pade, gre potrjen zapis v outbox na disk in se odda, ko je CRM spet dosegljiv. Idempotenčni ključi poskrbijo, da se nič ne podvoji. |
| CRM zavrne (4xx) | Ponavljanje nima smisla, ker se ne bo izšlo. Zapis gre v dead-letter in svetovalcu, z navedenim razlogom. |
| Slack | Nadomestni kanal je e-pošta. Sestanek ostane v stanju `awaiting_approval`, dokler ga kdo ne obdela. |
| Transkripcija | Nič ni izgubljeno, posnetek ostane in obdelavo pač ponovimo. |

Preizkusil sem samo primer s CRM-jem, ker je edini, ki sem ga znal verodostojno
simulirati. Ob ugasnjenem strežniku pipeline odloži tri zapise v outbox, po
ponovnem zagonu, s štiridesetimi odstotki namerno vrnjenih napak, pa jih odda
vse in nobenega ne podvoji.

Zadnja stvar, ki se je na začetku nisem spomnil. Vsak sestanek ima status, od
`extracted` preko `awaiting_approval` do `written`, `blocked` ali
`partially_written`, tako da se da kadarkoli vprašati, kaj visi in zakaj.
Najpogostejša odpoved takih sistemov po mojem sploh ni tehnična. Je ta, da nekdo
pozabi klikniti *Potrdi* in tega nihče ne opazi, zato sestanek, ki čaka več kot
48 ur, sproži opomnik.

## Bonus: prototip

Prototip je Python paket z eno samo zunanjo odvisnostjo. Napisal sem ga, ker se
mi zdi, da se o kontrolah proti halucinacijam veliko lažje pogovarjamo, če jih
je mogoče pognati. Vsebuje shemo, prompt, kontrolno plast, orkestrator s CLI-jem,
odporen HTTP klient in lažni CRM, ki zna simulirati izpade. To je tisti del, ki
bi v produkciji tekel za n8n-ovima klicema `/extract` in `/commit`.

Pognal sem tri scenarije:

| Scenarij | Rezultat |
|---|---|
| Normalen potek | Zapis o sestanku in dve nalogi v CRM. Ponoven `approve` ne ustvari dvojnikov. |
| Halucinacija | Štiri `BLOCK` ugotovitve, status `blocked`, v CRM ne gre nič. |
| CRM ne dela | Trije poskusi z odlogom, nato outbox. Po ponovnem zagonu vse oddano, nič podvojeno. |

Česa ni: transkripcije zvoka, prave Slack integracije, avtentikacije in baze,
saj je stanje shranjeno kar v JSON datotekah. Prav tako ni pognan klic na pravi
API, ker v okolju, kjer sem delal, nisem imel ključa. Koda sledi dokumentirani
uporabi SDK-ja, vse ostalo pa teče na shranjenem odgovoru modela. To pišem zato,
ker se mi zdi pošteno ločiti, kaj je preverjeno in kaj ne.

### Kaj bi naredil naslednje

1. Eval set na resničnih transkriptih, ker je brez meritve vse ostalo mnenje.
2. Dva tedna v načinu, kjer AI predlaga in človek vedno potrdi, z merjenjem,
   koliko popravkov je v resnici potrebnih. Šele ta številka pove, ali se stvar
   splača.
3. Če je delež popravkov nizek, bi za nizko tvegane dele, recimo notranji
   povzetek in naloge z jasnim lastnikom in rokom, razmislil o samodejni
   potrditvi. Komercialni podatki in komunikacija s stranko po mojem ostanejo
   pri človeku tudi potem.

---

### Opomba o uporabi AI orodij

Nalogo sem delal s Claude Code. Največ mi je pomagal pri pisanju prototipa, se
pravi pri delu, kjer sem vnaprej vedel, kaj hočem, in me je zanimala samo
hitrost. Zasnovo sem določil sam, predvsem tisti del s citati in ločnico med
tem, kar sme napisati model, in tem, kar mora razrešiti sistem.

Kjer je bilo treba presojati, prvega predloga nisem vzel. Prvi osnutek prompta
je bil preveč popustljiv in bi pri tej nalogi delal natanko narobe, idejo o
preverjanju halucinacij z drugim klicem modela pa sem zavrgel in jo nadomestil z
navadno primerjavo besedila. Se mi zdi, da je pri teh orodjih to bistveno:
uporabna so ravno toliko, kolikor znaš presoditi, kaj ti vrnejo.

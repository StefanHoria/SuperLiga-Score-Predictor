# PredictieScor — SuperLiga României

Aplicație locală de predicție statistică pentru SuperLiga: rezultate, goluri,
cornere, cartonașe, șuturi, jucători și **bilete de pariuri** generate automat,
verificate după meciuri și îmbunătățite din propriile rezultate.

Totul rulează pe calculatorul tău. Baza de date e un fișier SQLite în `data/`,
interfața pornește pe `localhost`, iar cheia de API stă în `.env` (exclus din
git). Singurul trafic către exterior sunt cererile de descărcare a datelor.

---

## Pornire rapidă

```bash
pip install -r requirements.txt
```

```bash
cp .env.example .env
```

Completează `APIFOOTBALL_KEY` în `.env` (cont gratuit pe
[dashboard.api-football.com](https://dashboard.api-football.com/register)).
Fără cheie aplicația merge, dar fără statistici detaliate pe meci.

```bash
python scripts/fetch_data.py
```

```bash
python -m streamlit run src/predictiescor/app/main.py
```

Se deschide în browser pe `http://localhost:8501`.

---

## Cum arată aplicația

| Pagină | Ce găsești |
|---|---|
| **Prezentare generală** | Clasament, forța echipelor după model, evoluția Elo |
| **Predicție meci** | Toate piețele pentru orice pereche de echipe: 1X2, scor exact, peste/sub, ambele marchează, handicap asiatic, cornere, cartonașe, marcatori |
| **Echipe** | Profil complet: formă, acasă/deplasare, statistici vs media ligii, radar |
| **Jucători** | Clasamente, loturi, predicții de marcatori |
| **Etapa următoare** | Predicții pentru toate meciurile programate |
| **Performanța modelului** | Backtest cronologic, calibrare, comparație cu cotele pieței |
| **Date și surse** | Butoane de actualizare, ce sursă merge, cât de complete sunt datele, unelte de curățare |
| **Bilete** | Generare, verificare, acuratețe, învățare, surprize |
| **Live** | Scorul meciurilor în desfășurare, cartonașele și cornerele de până acum, șansele recalculate pe loc, starea ponturilor tale și ponturi propuse din situația din teren |
| **Știri** | Absențe, reveniri și transferuri citite din presă, cu propoziția-dovadă lângă fiecare |
| **Transferuri** | Veniri și plecări pe club, cu sumele plătite și încasate, și profitul rezultat |

---

## De unde vin datele

Aplicația combină șapte surse. Fiecare completează doar câmpurile pe care
celelalte nu le-au adus, deci o sursă slabă nu strică niciodată o sursă bună.

| Sursă | Ce aduce | Acoperire | Stare |
|---|---|---|---|
| **football-data.co.uk** | rezultate + cote 1X2 | toate sezoanele, **inclusiv cel curent** | ✅ merge |
| **LPF.ro** (oficial) | 23 de statistici pe meci + formații, marcatori, minute jucate | **sezonul curent** | ✅ merge |
| **Flashscore** | scor live, scor final imediat, scorul la pauză, ora exactă de start | **sezonul curent** | ✅ merge |
| **Flashscore — cote** | cote pre-meci de la 5 case: 1X2, peste/sub (7 linii), ambele marchează, șansă dublă | meciuri viitoare **și** trecute | ✅ merge |
| **Presa (RSS)** | absențe, reveniri, transferuri — ca text, nu ca date pentru model | doar de acum înainte | ✅ merge |
| **Transfermarkt** | calendar, rezultate, loturi, minute, goluri, cartonașe, **accidentări și suspendări** | toate sezoanele, **inclusiv cel curent** | ✅ merge |
| **API-Football** | idem + xG și statistici pe jucător | **doar 2022–2024** pe planul gratuit | ✅ merge, cu limite |
| **Sofascore** | statistici bogate pe meci | — | ⛔ răspunde `403` la acces automat |
| **FBref** | xG alternativ, statistici avansate | — | ⛔ protejat de Cloudflare |

### LPF.ro — sursa oficială a ligii

Acoperă exact golul lăsat de API-Football: statistici detaliate pentru
**sezonul în curs**. Pentru fiecare meci: șuturi (total, pe poartă, blocate,
din afara careului), cornere, cartonașe galbene și roșii, posesie, atacuri,
pase (total, precise, în careu), faulturi date și primite, driblinguri,
lovituri libere, interceptări, deposedări, dueluri câștigate.

Pe lângă statisticile de echipă, LPF publică **formațiile complete și
cronologia meciului** — goluri, cartonașe și schimbări, cu minutul fiecăruia.
Din ele reconstruim, pentru fiecare jucător: minute jucate, titular sau
rezervă, goluri, cartonașe galbene și roșii. Asta face ca piața de **marcatori**
să se poată deconta pe sezonul curent.

Paginile sunt numerotate secvențial (`lpf.ro/statistici/{id}`, 8 meciuri pe
etapă), deci providerul parcurge intervalul fără să aibă nevoie de o pagină de
listare. `lpf_first_match_id` din `config.yaml` marchează începutul sezonului —
schimbă-l când începe sezonul următor.

**Autogolurile.** LPF le publică cu aceeași iconiță ca golurile obișnuite, pe
partea jucătorului care le-a înscris — nu se pot distinge din pagină. De aceea
decontarea nu are încredere oarbă în marcatori: **acceptă datele doar dacă
golurile pe jucători reconstruiesc exact scorul fiecărei echipe**. Când nu se
potrivesc (3 din 53 de meciuri în sezonul curent), piața de marcatori e marcată
*anulată* în loc să fie decontată greșit.

**Detaliu care contează.** Paginile meciurilor viitoare există deja. Unele au
toate valorile zero; altele afișează cifre perfect plauzibile (șuturi, posesie,
cornere) pentru meciuri care nu s-au disputat — nu am putut stabili ce
reprezintă. De aceea garanția nu stă în ce citim de pe pagină, ci în baza de
date: **providerul scrie doar pe meciuri care au deja un scor confirmat din
altă sursă**. LPF nu creează niciodată meciuri și nu decide ce s-a jucat; rolul
lui este strict de îmbogățire.

### Flashscore — scor live

Pagina competiției conține feed-ul intern al site-ului direct în HTML, în format
delimitat, fără token și fără JavaScript:

```
~AA÷0Sgejp7D¬AD÷1788112800¬CX÷FCSB¬AF÷UTA Arad¬AG÷1¬AH÷3¬AB÷3¬ER÷Runda 7
```

De aici ies patru lucruri pe care celelalte surse nu le dau la timp:

* **scorul live**, în timpul meciului;
* **scorul final în secunda în care se fluieră** — football-data publică de două
  ori pe săptămână, Transfermarkt cu întârziere;
* **scorul la pauză**;
* **ora exactă de start, în UTC**.

**O onestitate despre stări:** din feed am putut confirma direct doar codurile
`1` (programat) și `3` (încheiat) — la momentul scrierii nu era niciun meci în
desfășurare, deci codul pentru „live” nu a putut fi verificat. Providerul **nu
se bazează pe o valoare anume**: tratează drept live orice meci care nu e nici
programat, nici încheiat, dar are deja scor. Un cod necunoscut nu strică nimic.
La fel, minutul curent este căutat în mai multe câmpuri posibile, iar dacă nu
apare niciunde, pagina Live îl estimează din timpul scurs de la start.

#### Cote pre-meci

Golul cel mai serios pe care îl avea aplicația: football-data publică cotele
**odată cu rezultatul**, deci pentru meciurile viitoare aveam 2 din 186. Fără
cote înainte de meci nu se poate face nici detecție reală de valoare, nici
comparație cu piața la momentul potrivit.

Pagina de meci de la Flashscore nu conține cotele, dar cere ea însăși un
endpoint GraphQL cu interogare persistată, găsit uitându-mă ce cereri face
pagina în browser:

```
https://global.ds.lsapp.eu/odds/pq_graphql
  ?_hash=ope2&eventId=<id>&bookmakerId=<n>&betType=HOME_DRAW_AWAY&betScope=FULL_TIME
```

Răspunde `200` **fără niciun antet special** — nici token, nici Referer — și dă
și cota curentă, și **cota de deschidere**.

**Verificat înainte de implementare**, pe 31 august 2026:

| Ce | Rezultat |
|---|---|
| Meciuri viitoare cu cote | **9 din 10** (cel ratat era peste 12 zile) |
| Meciuri deja jucate | **8 din 8** — deci se poate completa și în urmă |
| Case de pariuri | 5: Betano, Superbet, Getsbet, Unibet, MrBit |
| Marja pe casă | **0,9 % – 4,9 %** (football-data avea 8,4 %) |
| Piețe | 1X2, peste/sub (7 linii), ambele marchează, șansă dublă, handicap asiatic, scor corect |

**Două lucruri au fost verificate, nu ghicite**, și amândouă erau greșite la
prima încercare: meniul de case răspunde `400` fără `geoIpSubdivisionCode`, iar
câmpurile pentru șansa dublă se numesc `homeOrDraw` / `awayOrDraw` / `noDraw`,
nu cum presupusesem. Un test verifică explicit că numele vechi nu potrivesc
nimic.

**Cotele existente nu sunt atinse.** football-data folosește deja numele `avg`
și `max`, iar pe ele se sprijină backtestul; agregatele noastre se numesc
`fs_avg` și `fs_max`. Fiecare casă își păstrează numele ei.

**Fragilitatea, spusă pe față:** `_hash` e identificatorul unei interogări
persistate și **se poate schimba când Flashscore își actualizează site-ul**.
Când se întâmplă, providerul ridică o eroare explicită și oprește pasul, în loc
să scrie tăcut zero rânduri și să pară că meciurile n-au cote.

Pasul rulează doar la actualizarea **completă**, nu la cea rapidă: un meci costă
`case × piețe` cereri, deci o rulare durează minute. Bugetul implicit e de 120
de cereri, iar meciurile cele mai apropiate se servesc primele — același tipar ca
la API-Football. Se oprește din `.env` cu `FLASHSCORE_ODDS=false`.

**Termeni de utilizare:** cotele sunt afișate public pe pagina meciului, dar
endpointul nu e documentat, iar condițiile Flashscore nu permit explicit accesul
automat. Am semnalat asta înainte de a construi; decizia de a folosi sursa îți
aparține.

#### Ora afișată vs. ora stocată

Sunt două lucruri diferite, și amândouă au fost greșite pe rând.

**Stocarea** e în UTC, ca baza să nu depindă de mașina pe care rulează. Corect,
și rămâne așa.

**Afișarea** trebuie însă să fie ora României — altfel meciul de la 20:30 apare
ca 17:30. Pe pagina *Etapa următoare* ora se scria chiar **fără niciun marcaj**,
deci nici măcar nu se vedea că e altceva decât ora locală. Conversia se face
acum la afișare, prin `local_time()` și `local_series()` din
[`common.py`](src/predictiescor/app/common.py), iar acolo unde încape scrie și
fusul (`EEST` vara, `EET` iarna). Unsprezece teste acoperă conversia, inclusiv
meciurile târzii care cad în ziua următoare și trecerea la ora de iarnă.

#### Un bug de fus orar, găsit prin comparație

Flashscore publică momentul startului ca timestamp UTC. Comparând cu ce aveam în
bază, s-a văzut o diferență sistematică de două ore: **Transfermarkt afișează ora
Germaniei, LPF ora României**, iar ambele erau citite ca și cum ar fi fost deja
UTC. Toate meciurile erau stocate cu două-trei ore mai târziu decât se joacă —
ceea ce strica și fereastra de verificare automată a biletelor.

Cele două parsere au fost corectate (`ZoneInfo("Europe/Berlin")`, respectiv
`ZoneInfo("Europe/Bucharest")`, cu ora de vară tratată corect), iar rândurile
scrise înainte au fost reparate din feed de `correct_kickoff_times()`:
**168 de meciuri corectate**.

#### Probabilități în timpul meciului

La 1-0 în minutul 70, șansele nu mai sunt cele de dinainte de start. Modelul de
dinaintea meciului dă ritmul de golare al fiecărei echipe (λ pe 90 de minute);
din el rezultă direct situația din teren:

```
goluri rămase ~ Poisson(λ × minute_rămase / 90)
scor final = scor curent + goluri rămase
```

Restul piețelor se deduc din aceeași matrice, cu funcțiile deja existente — deci
un „peste 2.5” live e calculat de exact același cod ca unul de dinainte de meci.

**Ce nu se aplică:** corecția Dixon-Coles pentru scoruri mici. Ea descrie
dependența dintre golurile unui meci *întreg*; pe un rest de meci nu are aceeași
semnificație, iar folosirea ei ar fi o extindere nejustificată. **Ce nu se
modelează deloc:** eliminările, schimbarea de tactică după gol, oboseala. E un
model de ritm, nu unul de joc.

Pe pagina **Live**, când nu e niciun meci în desfășurare, poți simula orice
situație — alegi echipele, scorul și minutul — și vezi cum se mișcă șansele.

**Termeni de utilizare:** feed-ul e public și neprotejat, dar condițiile
Flashscore nu permit explicit accesul automat. Cererile se fac rar și cu pauză
între ele, iar sursa se poate opri complet din `.env`
(`FLASHSCORE_ENABLED=false`). Decizia de a o folosi îți aparține.

Providerii pentru Sofascore și FBref sunt scriși complet și testați ca structură;
dacă sursele devin accesibile, se activează din `.env` fără altă modificare.
Aplicația **nu încearcă să ocolească protecțiile anti-bot** — detectează blocajul,
se oprește curat și folosește restul surselor.

### Limitele planului gratuit API-Football

Două limite, ambele respectate automat de cod:

* **100 de cereri pe zi.** Statisticile se cer *per meci*, deci baza se umple
  în câteva zile de rulări succesive. Meciurile cele mai recente au prioritate.
* **10 cereri pe minut.** Providerul lasă 6.5 secunde între cereri; altfel
  primești `429` și pierzi cereri din cota zilnică degeaba.
* **Doar sezoanele 2022–2024.** Sezonul curent nu e inclus — de aceea
  rezultatele curente vin de la football-data.co.uk, statisticile pe meci de la
  LPF.ro, iar jucătorii de azi de la Transfermarkt.

---

## Cum rămân datele actualizate

### Din interfață (butonul)

**⬇️ Actualizează datele**, din bara laterală, pe orice pagină. Aduce **tot ce
are nevoie modelul**, în unsprezece pași: rezultate, scor live, calendar,
statistici oficiale LPF, știri, cote pre-meci, loturi și absențe, statistici
detaliate pe meci — apoi curăță, înregistrează predicțiile și decontează
biletele.

**Pașii scumpi se sar dacă sunt proaspeți.** Altfel butonul ar fi însemnat un
sfert de oră de așteptare la fiecare apăsare, degeaba: loturile nu se schimbă
de două ori pe zi, iar cotele nu se mișcă în cinci minute cât să merite încă
șase minute de cereri.

| Pas | Rămâne proaspăt |
|---|---|
| Cote pre-meci (Flashscore) | 3 ore |
| Statistici detaliate pe meci | 6 ore |
| Loturi și jucători | 12 ore |

Restul rulează mereu — durează secunde și aduc exact ce se schimbă des.

Măsurat pe 6 septembrie 2026: **prima rulare 278 secunde**, a doua imediat
după, **96 de secunde**, cu cei trei pași scumpi săriți.

Din terminal, `python scripts/update.py --force-all` ignoră fereastra de
prospețime și reia tot.

### Din terminal

`scripts/update.py` rulează exact același cod ca butoanele (ambele apelează
`predictiescor.data.refresh`), deci nu există două comportamente diferite.

```bash
python scripts/update.py
```

### Programare automată, dintr-o comandă

```bash
python scripts/update.py --install
```

Creează o sarcină Windows care rulează complet, zilnic la 04:30: aduce
rezultatele noi **și continuă completarea statisticilor istorice**, în limita
cotei API. Backfill-ul are de recuperat ~900 de meciuri (sezoanele 2022–2024),
adică vreo zece zile de rulare — după care modelele de cornere, cartonașe și
șuturi se antrenează pe de nouă ori mai multe date decât acum.

Împreună cu `python scripts/watch.py --install` (verificarea biletelor la
fiecare meci), aplicația se întreține singură.

| Comandă | Durată | Când |
|---|---|---|
| `python scripts/update.py --quick` | ~10 s | des (rezultate, calendar, statistici LPF) |
| `python scripts/update.py` | ~2–10 min | o dată pe zi |
| `python scripts/update.py --with-tickets` | idem | dimineața, înainte de meciuri |
| `python scripts/update.py --budget 0 --no-players` | ~5 s | ca `--quick`, dar rulează și curățarea și decontarea biletelor |

### Automatizare pe Windows

Rulează la fiecare 3 ore, în fundal:

```bash
schtasks /create /tn "PredictieScor" /sc hourly /mo 3 /tr "python C:\Users\stefa\Desktop\Proiecte\PredictieScor\scripts\update.py --quick"
```

Și o rulare completă zilnic la 09:00:

```bash
schtasks /create /tn "PredictieScor complet" /sc daily /st 09:00 /tr "python C:\Users\stefa\Desktop\Proiecte\PredictieScor\scripts\update.py --with-tickets"
```

Ștergi o sarcină cu `schtasks /delete /tn "PredictieScor" /f`.

**Cât de „live" e:** rezultatele apar la câteva minute–ore după meci (atât
Transfermarkt cât și football-data.co.uk actualizează după fluierul final).
Nu există scor pe minut — pentru asta ar trebui un plan API plătit. Pentru
predicții și bilete, actualizarea după fiecare etapă este suficientă.

---

## Bilete

### Generare

```bash
python scripts/tickets.py --next
```

Construiește 5–10 bilete pentru următoarea zi cu meciuri, în formatul de pe
site-urile de pariuri: mai multe ponturi, fiecare cu cota lui, cotă totală și
câștig potențial.

Opt strategii, cu profiluri de risc diferite:

| Strategie | Profil |
|---|---|
| **Bilet sigur** | șansă mare, cotă mică |
| **Bilet echilibrat** | compromis între șansă și cotă |
| **Bilet de valoare** | doar unde modelul bate cota casei |
| **Bilet pe goluri** | peste/sub, ambele marchează |
| **Bilet pe statistici** | cornere și cartonașe |
| **Bilet cu marcatori** | jucători care marchează |
| **Bilet cu cotă mare** | improbabil, dar plătește mult |
| **Bilet pe rezultate** | doar 1X2 și șansă dublă |
| **Bilet combinat pe un meci** | mai multe ponturi din același meci, cu șansa calculată prin simulare |

Două reguli țin biletele oneste:

1. **Un singur pont per meci** la toate strategiile în afară de „Bilet
   combinat", unde ponturile din același meci sunt permise pentru că șansa
   comună se calculează prin simulare, nu prin înmulțire.
2. **Cotele afișate sunt „corecte"** (1 ÷ șansă). Casele scad o marjă, deci
   cota reală va fi mai mică. Un bilet merită doar dacă cota oferită de casă
   e mai mare decât cea calculată aici.

### Verificare automată, după fiecare meci

```bash
python scripts/watch.py --install
```

La fiecare ciclu citește și **fluxurile de știri, cel mult o dată pe oră** —
independent de meciuri. Fără asta, o accidentare anunțată la prânz, în ziua
meciului de seară, nu intra în bază decât dacă apăsai butonul: ciclul ajungea
la fluxuri doar când lipsea un rezultat, și nici atunci mereu, fiindcă
Flashscore îl aduce de obicei pe calea rapidă.

Înregistrează o sarcină Windows care rulează din 15 în 15 minute, **fără
fereastră de consolă** (`pythonw.exe`; jurnalul ajunge în `data/watch.log`). Nu
face nicio cerere de rețea decât dacă **chiar s-a încheiat un meci** între timp:
știe programul, deci știe și când are rost să verifice.

Fereastra e de **1 oră și 50 de minute** de la start — 45+15+45 de minute plus
prelungirile înseamnă că un meci nu se poate încheia mai devreme. Era de 2h15 pe
vremea când rezultatul venea de la football-data, care publică de două ori pe
săptămână; Flashscore are scorul final imediat, deci putem verifica mai devreme.

Când chiar lipsește un rezultat, se încearcă **întâi calea ieftină**: o singură
cerere la Flashscore, care durează aproximativ o secundă. Refresh-ul complet —
care descarcă un CSV întreg și mai interoghează două site-uri — rulează doar
dacă după aceea tot lipsește ceva. Dacă Flashscore cade, ciclul continuă pe
drumul lung, nu se oprește.

| Comandă | Ce face |
|---|---|
| `python scripts/watch.py --status` | ce așteaptă și când e următoarea verificare utilă |
| `python scripts/watch.py --once` | o singură verificare, acum |
| `python scripts/watch.py` | rulează continuu, în terminal |
| `python scripts/watch.py --uninstall` | oprește sarcina automată |

Un meci intră în lista „de verificat" doar dacă a început cu peste 1h50 în
urmă **și** fie nu are încă rezultat, fie mai are ponturi nedecontate. Un bilet
rămas deschis din cauza *altui* meci nu declanșează verificări inutile pe cel
deja decontat, iar meciurile mai vechi de o săptămână fără rezultat sunt
abandonate în loc să fie reinterogate la nesfârșit.

### Verificare manuală și învățare

```bash
python scripts/tickets.py --settle --learn
```

Fiecare pont e comparat cu ce s-a întâmplat efectiv, **de îndată ce meciul lui
s-a jucat** — nu se așteaptă restul biletului. Consecințele:

* Un bilet cu un pont ratat se închide pe loc: rezultatul e deja decis.
* Un bilet ale cărui ponturi decise au ieșit toate rămâne deschis, iar coloana
  *ponturi ieșite* arată pe unde e (`1/2`, `2/3`).

Când datele lipsesc (de exemplu cornerele unui meci fără statistici), pontul e
marcat **anulat**, nu ghicit — biletele anulate nu contaminează statisticile de
acuratețe.

Din rezultate se învață două lucruri:

* **Calibrarea pe piață.** Dacă pe cornere modelul spune 70% dar se întâmplă în
  55% din cazuri, e prea încrezător acolo; corecția se aplică automat la
  biletele următoare.
* **Greutatea strategiilor.** Strategiile profitabile sunt propuse mai des.
  Greutatea se schimbă doar după minim 8 bilete, cu shrinkage — ca un noroc
  de moment să nu răstoarne concluzia.

### Își cunoaște fiecare strategie șansele?

O rată de reușită singură induce în eroare. „Bilet cu cotă mare" iese **0 % din
16** și pare stricat — dar la o șansă prezisă de 14 % pe bilet, șansa să nu iasă
niciunul e **15 %**: ghinion obișnuit, nu defect.

De aceea tabelul are o coloană de *cât de neobișnuit* ar fi rezultatul dacă
modelul ar avea dreptate (test binomial în două cozi). Măsurat pe 6 septembrie
2026, **nicio strategie nu deviază neobișnuit** — toate peste 15 %.

Diferența față de piețe: la nivel de **pont**, marcatorii chiar deviază
semnificativ (46,9 % preziși vs 14,3 % reali, 3 abateri standard). La nivel de
**bilet** nu se vede, fiindcă 9 bilete sunt prea puține și combinarea ponturilor
diluează semnalul. Piețele sunt măsurătoarea mai ascuțită.

### Avertisment când s-a mutat piața

Dacă un pont stă pe un meci unde cota s-a schimbat mult de la deschidere,
biletul primește un semn. Nu schimbă predicția — spune doar că **modelul
lucrează cu informație mai veche decât casele**. E singurul semnal din
aplicație care vine din ceva ce modelul nu are.

### Cum se citește verificarea

Fiecare bilet e un card, nu un rând într-un tabel din care trebuie să-l alegi.
Se vede dintr-o privire: eticheta de stare colorată, cota, șansa, profitul, o
bară cu *câte ponturi au ieșit* și, dedesubt, **fiecare pont cu semnul lui**,
meciul, scorul și ce s-a întâmplat de fapt („3 goluri", „0 cartonașe", „nu avem
date pe jucători pentru acest meci").

Biletele sunt grupate pe zi, cu bilanțul zilei în titlu. Tabelul clasic a rămas,
dar strâns într-un panou — nu mai e singura cale de a vedea ceva.

Toate ponturile se aduc **într-o singură interogare** (`legs_for_tickets`), nu
una per bilet.

### Istoric onest

```bash
python scripts/tickets.py --backfill 30
```

Reface biletele pentru ultimele 30 de zile cu meciuri, **reantrenând modelul
pentru fiecare zi doar pe meciurile de dinaintea ei**. Doar așa acuratețea
raportată e reală. Fără asta, modelul ar „paria" pe meciuri pe care le-a văzut
deja la antrenare, iar cifrele ar fi flatante și inutile.

```bash
python scripts/tickets.py --report
```

---

## Modelele

### Goluri — Dixon-Coles

```
λ_gazdă   = exp(atac_gazdă   + apărare_oaspete + avantaj_teren)
λ_oaspete = exp(atac_oaspete + apărare_gazdă)
```

Golurile sunt aproximativ Poisson, dar scorurile mici (0-0, 1-0, 0-1, 1-1) apar
mai des decât prezice un Poisson independent — parametrul `rho` corectează exact
acele patru celule.

Două detalii care contează:

* **Ponderare în timp.** Un meci de acum 240 de zile cântărește jumătate cât
  unul de ieri (`half_life_days` în `config.yaml`).
* **Regularizare ca prior bayesian.** Fiecare echipă primește echivalentul a
  `shrinkage_matches` meciuri fictive de echipă medie. Fără asta, o nou-promovată
  cu 6 meciuri bune ajunge artificial pe primul loc în clasamentul forței.

Din matricea de scor rezultă **toate** piețele: 1X2, șansă dublă, scor exact,
peste/sub pe orice linie, ambele marchează, handicap asiatic, diferență de
goluri, par/impar.

### xG reconstruit din zonele de șut

API-Football publică xG, dar planul gratuit acoperă doar 2022–2024. LPF publică
zonele de șut pentru sezonul curent, dar nu și xG. Diferența se acoperă:
învățăm din sezoanele unde avem ambele cât valorează un șut din fiecare zonă,
apoi aplicăm formula unde xG lipsește.

Modelul e deliberat constrâns, nu o regresie liberă:

* **fără termen liber** — zero șuturi înseamnă zero xG;
* **coeficienți obligatoriu pozitivi** — un șut nu poate scădea xG-ul.

Fără constrângeri, regresia dă șuturilor din afara careului un coeficient
*negativ* (coliniaritate cu totalul șuturilor) — absurd fizic și fără
generalizare. Cu ele, R² **crește** de la 0.562 la 0.587, iar coeficienții se
citesc direct ca valoarea medie a unui șut:

| Zonă | Goluri per șut |
|---|---|
| Șut din careu | 0.129 |
| Șut pe poartă (bonus) | 0.061 |
| Șut blocat | 0.020 |
| Șut din afara careului | 0.001 |

xG-ul real nu se suprascrie niciodată: rămâne în coloana `xg`, estimarea în
`xg_estimated`. Se știe mereu care valoare vine de la furnizor.

### Piețe corelate — simulare Monte Carlo

Două ponturi din același meci sunt corelate: „gazda câștigă" și „peste 2.5
goluri" cresc împreună. Înmulțirea probabilităților ar minți despre șansa
biletului.

Simulăm meciul de 20.000 de ori și numărăm în câte desfășurări ies toate
ponturile. Diferența e reală:

| Combinație | Înmulțit | Real (simulat) |
|---|---|---|
| FCSB câștigă + peste 2.5 | 28.5% | **33.9%** (1.19×) |
| FCSB câștigă + sub 2.5 | 29.1% | **24.0%** (0.83×) |

Fiecare simulare e transformată în aceeași structură folosită la decontarea
reală și trecută prin același cod. Un pont e evaluat la preț exact cum va fi
decontat după meci — nu pot apărea diferențe între cele două.

### Statistici secundare — 22 de indicatori, model multiplicativ + Negative Binomial

```
așteptat = medie_ligă × ofensiva_echipei × permisivitatea_adversarului × factor_teren
```

Cornerele și cartonașele sunt supra-dispersate față de Poisson, deci
distribuția e Negative Binomial, cu dispersia estimată din reziduuri.

### Jucători

Rate la 90 de minute cu shrinkage spre media poziției, înmulțite cu minutele
așteptate, apoi **scalate astfel încât suma pe echipă să fie egală cu golurile
prezise de Dixon-Coles**. Așa cele două modele rămân consistente între ele.

### Elo — reper independent și parte din amestec

Rating clasic, cu update amplificat de diferența de goluri.

Măsurat prin backtest, amestecul Elo + Dixon-Coles **ajută**: RPS scade de la
0.2099 la 0.2086, iar acuratețea urcă de la 45.1% la 47.6%. Ponderea implicită
e 0.4, dar curba e plată între 0.3 și 0.6 — la ~600 de meciuri testate,
diferențele de acolo sunt zgomot.

Amestecul e logaritmic (media geometrică ponderată), iar matricea de scor e
**repondera­tă** ca marginalele ei 1X2 să fie exact cele amestecate. Altfel
scorurile exacte și piețele de goluri ar contrazice noul 1X2.

### Calibrarea 1X2 — măsurată, neaplicată

Un model poate ordona corect echipele și totuși să exprime prost încrederea.
Am testat dacă un strat de calibrare ar ajuta, antrenând pe primele 561 de
meciuri din backtest și testând pe următoarele 374:

| Metrică | necalibrat | izotonică | Platt |
|---|---|---|---|
| RPS | **0.2088** | 0.2121 | 0.2090 |
| log loss | 1.0345 | 1.0933 | **1.0335** |
| Brier | 0.6202 | 0.6278 | **0.6200** |

Regresia izotonică **înrăutățește** tot — e prea flexibilă și învață zgomotul.
Platt nu schimbă nimic (câștiguri de ordinul 0.0002).

Motivul: modelul e **deja bine calibrat** pe 1X2. Abaterea medie ponderată
între prezis și observat e 0.027 — sub trei puncte procentuale. Nu există
distorsiune sistematică de corectat.

De aceea calibratorul nu e legat în predicție. Modulul rămâne ca diagnostic,
vizibil pe pagina *Performanța modelului*: dacă modelul se schimbă mult, îți
spune în câteva secunde dacă situația s-a schimbat.

Atenție la distincție: piețele **secundare** (cornere, marcatori) chiar sunt
prost calibrate — cornere 59% prezis vs 52% real, marcatori 46% vs 38% — dar
acelea se corectează deja automat, din biletele decontate.

### Calibrarea pe piețe, după backfill-ul curat

Măsurat pe 336 de ponturi decontate, 6 septembrie 2026:

| Piață | Ponturi | Prezis | Real | Diferență |
|---|---|---|---|---|
| total goluri | 106 | 80,7 % | 80,2 % | **−0,6** |
| cartonașe | 76 | 77,3 % | 76,3 % | −1,0 |
| șansă dublă | 67 | 72,0 % | 67,2 % | −4,8 |
| cornere | 34 | 62,9 % | 55,9 % | −7,0 |
| echipa marchează | 22 | 81,3 % | 77,3 % | −4,1 |
| **marcatori** | 21 | 46,9 % | **14,3 %** | **−32,6** |
| 1X2 | 7 | 45,1 % | 57,1 % | +12,1 |

Pe ansamblu: prezis 73,2 %, real 69,0 %. Piețele de goluri și cartonașe sunt
**foarte bine calibrate** — sub un punct diferență pe peste 180 de ponturi.
Marcatorii sunt cazul patologic; restul diferențelor sunt în zgomot la
dimensiunile astea de eșantion.

### Ce NU a ajutat

Avantajul de teren estimat **per echipă** (în loc de unul comun pe ligă) nu a
schimbat practic nimic: RPS 0.2101 → 0.2099, în zgomot. Codul a rămas, cu
shrinkage puternic, dar nu te aștepta la câștig din el. Îl poți dezactiva
punând `home_advantage_spread` foarte mare în `config.yaml`.

**Reglarea parametrilor.** Valorile din `config.yaml` (timp de înjumătățire
240, shrinkage 20, pondere Elo 0,4) nu fuseseră niciodată măsurate ca grilă. Am
făcut reglarea pe trei parametri, cu **holdout**: aleg pe primele 70 % din
meciurile testate, raportez pe ultimele 30 %, la care nu m-am uitat când am ales.

| Pas | Ales pe setul de reglare | RPS reglare | RPS holdout |
|---|---|---|---|
| timp de înjumătățire | 400 (din 120–400) | 0.2101 ↓ | 0.2107 |
| shrinkage | 10 (din 10–35) | 0.2097 ↓ | 0.2109 |
| pondere Elo | 0,4 | 0.2097 | 0.2109 |
| **actual: 240 / 20 / 0,4** | — | 0.2104 | **0.2105** |

La fiecare pas metrica de reglare s-a îmbunătățit, și la fiecare pas holdout-ul
**nu a urmat-o**. Configurația „optimizată" iese pe holdout **cu 0,0004 mai
proastă** decât cea actuală. Manual de carte despre supra-ajustare — și exact
motivul pentru care holdout-ul nu e opțional.

Pe toată grila, RPS-ul pe holdout stă între 0.2104 și 0.2114. Modelul e practic
**insensibil** la acești parametri în intervale rezonabile. Valorile actuale
rămân neschimbate.

Un lucru util a ieșit totuși: cu `elo_ensemble_weight = 0`, holdout-ul urcă la
**0.2114**, cel mai prost din toată grila. Amestecul cu Elo chiar ajută, cam
0,0005 RPS — confirmare independentă a alegerii din configurație.

**Efectul arbitrului asupra cartonașelor** — plauzibil, dar nedemonstrabil cu
datele de acum. Măsurat pe 155 de meciuri cu arbitru cunoscut și cartonașe
pentru ambele echipe, 27 de arbitri, **mediană 5 meciuri per arbitru**:

| Model | MAE | RMSE |
|---|---|---|
| doar media ligii (reper) | 1.9419 | 2.3791 |
| arbitru, fără shrinkage | 1.9646 | 2.4387 |
| arbitru, shrinkage k=10 | **1.8972** | **2.3442** |

Câștigul de 2.3 % pare real până la verificare: interval de încredere 90 %
**[−0.008, +0.097]** — conține zero; test de permutare **p = 0.059**. Iar `k=10`
a fost ales uitându-ne la aceleași rezultate pe care le judecam, deci și acel
0.059 e optimist.

Descompunerea varianței spune același lucru mai direct: varianța **între**
arbitri (1.258) e mai *mică* decât cea pe care ar produce-o pura întâmplare
(1.475) — semnal estimat **zero**. Cu cinci meciuri de arbitru, mediile per
arbitru sunt aproape numai zgomot.

Codul rămâne în [`referees.py`](src/predictiescor/modeling/referees.py),
nelegat de predicții. **Merită refăcută măsurătoarea** când vor fi ~500 de
meciuri și 15+ per arbitru; atunci testul ar avea putere reală.

---

## Evaluare

Pagina *Performanța modelului* rulează un **backtest walk-forward**: pentru
fiecare meci testat, modelul e reantrenat folosind doar meciurile jucate
înaintea lui. Nicio informație din viitor nu ajunge în model.

Metrici raportate, comparate cu **cotele pieței cu marja eliminată**:

* **RPS** (Ranked Probability Score) — standardul pentru fotbal, ține cont că
  „egal" e între „victorie gazdă" și „victorie oaspete". Mai mic = mai bine.
* **Log loss**, **Brier**, **acuratețe**, **calibrare pe intervale**.
* **ROI** simulat pe pariurile unde modelul depășește piața cu un prag ales.

Piața e un adversar foarte greu de bătut. Dacă modelul se apropie de ea, e deja
bun; dacă o depășește constant pe multe sute de meciuri, atunci merită discutat.

### Când modelul nu e de acord cu piața, cine are dreptate?

Întrebarea de fond a biletelor. Pe istoricul de bilete nu se poate răspunde —
119 bilete și 266 de ponturi sunt dominate de noroc. Dar aceeași întrebare se
pune pe **1004 meciuri cu cote de închidere**, adică pe de patru ori mai multă
informație. Măsurat pe 31 august 2026:

**1. Unde se abate modelul, cât îl costă abaterea** (RPS, mai mic = mai bine):

| Dezacord cu piața | Meciuri | RPS model | RPS piață | Avantaj model |
|---|---|---|---|---|
| 0–3 % | 94 | 0.2304 | 0.2312 | **+0.0008** |
| 3–6 % | 368 | 0.1995 | 0.1984 | −0.0011 |
| 6–10 % | 327 | 0.2093 | 0.2023 | −0.0070 |
| peste 10 % | 215 | 0.2214 | 0.2004 | **−0.0210** |

Tendința e monotonă și clară: **cu cât modelul se abate mai mult de piață, cu
atât e mai prost**. Acolo unde sunt de acord, sunt la egalitate; acolo unde
modelul are „păreri proprii", pierde de zece ori mai mult teren.

**2. Duelul favoriților** — cele 167 de meciuri unde aleg favoriți diferiți:

| | Model | Piață |
|---|---|---|
| A nimerit | 39 (**23.4 %**) | 70 (**41.9 %**) |

Piața are dreptate de aproape două ori mai des. Testul e imun la calibrare: nu
contează cât de încrezător e fiecare, ci doar pe cine pariază.

**3. ROI simulat, cu interval de încredere de 90 %:**

| Preț folosit | Prag 2 % | Prag 5 % | Prag 8 % |
|---|---|---|---|
| cotă medie (marjă 8.4 %) | −13.1 % *(semnificativ)* | −15.6 % *(semnificativ)* | −16.2 % *(semnificativ)* |
| cel mai bun preț | −6.2 % *(nedistinct de zero)* | −9.0 % *(nedistinct)* | −9.4 % *(nedistinct)* |
| reper Pinnacle, cel mai bun preț | −7.7 % *(nedistinct)* | −16.1 % *(semnificativ)* | −21.4 % *(semnificativ)* |

**Concluzia, spusă direct:** logica de „valoare" nu are avantaj. Mai mult — un
avantaj real ar trebui să **crească** odată cu pragul, fiindcă cerem o diferență
tot mai mare față de piață. Aici scade. Combinat cu tabelul 1 și cu duelul
favoriților, verdictul e că **dezacordurile modelului sunt eroarea lui, nu
informație în plus**, iar ponturile de valoare pariază exact pe erorile lui.

O nuanță de corectitudine: luând **cel mai bun preț disponibil** în loc de cota
medie, pierderea la prag mic nu se mai distinge de zero — adică plătești marja
și cam atât. Doar când ceri un avantaj mare pierzi demonstrabil.

**Ce înseamnă asta practic:** biletele rămân un instrument de analiză. Dacă vrei
să le folosești ca filtru, filtrul care rezistă la măsurătoare e „unde modelul e
de acord cu piața", nu „unde se abate de la ea".

#### Remăsurat cu cote mai bune — verdictul nu se schimbă

Odată ce cotele pre-meci de la Flashscore au devenit disponibile, întrebarea
firească era dacă rezultatul de mai sus se datora cumva cotelor slabe de la
football-data (marjă 10,2 %). Am completat cotele Flashscore pentru cele
**54 de meciuri jucate din sezonul curent** — tot ce acoperă feed-ul lor — și am
remăsurat pe 31 august 2026.

**Întâi validarea sursei**, pe cele 47 de meciuri care au ambele surse:

| | football-data | Flashscore |
|---|---|---|
| Marjă medie | 10,21 % | **3,14 %** |
| RPS | 0.1867 | 0.1862 |

Diferența medie absolută între probabilitățile implicite, după eliminarea
marjei: **0.0052** — o jumătate de punct procentual. Corelația pe șansa gazdei:
**0.9989**. Cele două surse spun practic același lucru; Flashscore o spune doar
cu o marjă de trei ori mai mică.

**Apoi remăsurarea.** Pe cele 54 de meciuri, rezultatul pare **inversat**:
modelul câștigă pe dezacordurile mari (+0.0410) și bate piața la duelul
favoriților (30,0 % vs 20,0 %). Tentant — și greșit.

Testul de control lămurește de ce. Aceleași meciuri, prețuite cu **cotele
vechi**:

| Set | Meciuri | Duel favoriți | Avantaj pe dezacord mare |
|---|---|---|---|
| Flashscore | 54 | model 30,0 % vs piață 20,0 % *(n=10)* | **+0.0410** |
| football-data, **aceleași** | 47 | model 37,5 % vs piață 25,0 % *(n=8)* | **+0.0466** |
| football-data, toate | **1004** | model 23,4 % vs piață **41,9 %** *(n=167)* | **−0.0210** |

Cotele vechi, pe același eșantion mic, dau aceeași imagine favorabilă — chiar
puțin mai favorabilă. **Deci nu cotele au schimbat rezultatul, ci eșantionul.**
Sezonul curent se întâmplă să fie o perioadă în care modelul arată bine pe
opt-zece meciuri de dezacord, adică pe zgomot.

ROI-ul o spune la fel de limpede: +21,6 % la pragul de 8 %, cu interval
**[−26,4 %, +72,6 %]** pe 21 de pariuri. Un interval de o sută de puncte nu e o
măsurătoare, e o părere.

**Concluzia rămâne cea de pe 1004 meciuri.** Când un eșantion de 54 contrazice
unul de 1004, câștigă cel de 1004. Cotele mai bune sunt un câștig real pentru
prețuri și pentru afișare — dar nu au schimbat nimic despre valoare.

#### Despre intervalul de încredere

Fără el, citeam zgomot drept avantaj: un ROI de +8 % pe 60 de pariuri nu
înseamnă nimic dacă intervalul e [−25 %, +40 %]. Intervalul se calculează prin
reeșantionare pe **meciuri**, nu pe pariuri: ponturile de pe același meci nu
sunt independente (dacă gazda câștigă, pontul pe „egal" pică automat), iar
tratarea lor ca independente ar da un interval fals de îngust. Există un test
care verifică exact asta.

**Atenție la tabelul de praguri:** sunt mai multe praguri încercate deodată,
deci cel mai bun dintre ele arată mereu mai bine decât merită. Folosește-l ca să
vezi *forma*, nu vârful.

---

## Structura proiectului

```
PredictieScor/
├── config.yaml                    ID-uri de ligă, sezoane, parametri de model
├── .env                           chei și comutatoare (exclus din git)
├── data/predictiescor.db          baza SQLite
├── scripts/
│   ├── fetch_data.py              ingestie inițială / pe surse
│   ├── update.py                  actualizare periodică (de programat)
│   ├── watch.py                   verificare automată după fiecare meci
│   └── tickets.py                 bilete: generare, decontare, învățare
└── src/predictiescor/
    ├── config.py                  citirea configurației
    ├── utils/
    │   ├── teamnames.py           normalizarea numelor de echipe
    │   └── http.py                client HTTP cu cache și rate limiting
    ├── storage/                   schema SQLite și accesul la date
    ├── data/                      cei șapte provideri + agregatorul
    │   ├── flashscore.py          scor live, statistici, ore de start
    │   ├── flashscoreodds.py      cote pre-meci de la 5 case
    │   ├── news.py                fluxuri RSS din presa sportivă
    │   ├── newsfacts.py           text liber → fapte, cu nivel de încredere
    │   ├── transfers.py           veniri, plecări și sumele lor
    │   ├── lpf.py                 sursa oficială a ligii
    │   └── refresh.py             pașii de actualizare (buton + terminal)
    ├── modeling/
    │   ├── dixon_coles.py         modelul de goluri
    │   ├── markets.py             derivarea tuturor piețelor
    │   ├── inplay.py              probabilități în timpul meciului
    │   ├── livetips.py            ponturi din situația din teren
    │   ├── secondary.py           cornere, cartonașe, șuturi
    │   ├── xg.py                  xG reconstruit din zonele de șut
    │   ├── simulation.py          piețe corelate, Monte Carlo
    │   ├── players.py             predicții pe jucători
    │   ├── elo.py                 rating Elo
    │   ├── calibration.py         calibrare măsurată (neaplicată)
    │   ├── referees.py            efect arbitru măsurat (neaplicat)
    │   ├── evaluate.py            backtest și metrici
    │   ├── disagreement.py        model vs piață, cu interval de încredere
    │   └── pipeline.py            leagă totul
    ├── tickets/                   generare, decontare, învățare, surprize
    └── app/                       dashboard-ul Streamlit
```

---

## Normalizarea numelor de echipe

Fiecare sursă scrie altfel: `FCSB`, `Steaua Bucuresti`, `FC FCSB`, `(3.) FCSB`.
`utils/teamnames.py` le mapează pe toate la un nume canonic, altfel aceeași
echipă ar apărea de trei ori în baza de date.

Două capcane rezolvate explicit:

* **`Universitatea Craiova` vs `FC U Craiova 1948`** sunt cluburi *diferite*.
  O regulă dură, aplicată înaintea oricărei potriviri aproximative, împiedică
  contopirea lor.
* **Anii din denumiri** (`FC Rapid 1923`, `CFR 1907 Cluj`) sunt decor și se
  ignoră la comparare — cu excepția lui `1948`, prins de regula de mai sus.

Dacă o sursă introduce o variantă nouă, pagina *Date și surse* o semnalează și
oferă un buton de unire. Poți repara și din cod:

```bash
python -c "import sys; sys.path.insert(0,'src'); from predictiescor.storage.db import Database; print(Database().deduplicate())"
```

---

## Configurare

Tot ce merită reglat este în `config.yaml`, cu explicații lângă fiecare
parametru: timpul de înjumătățire, shrinkage-ul, liniile de pariere, bugetul de
cereri, sezoanele de descărcat.

Parametrii modelului pot fi testați direct din interfață, pe pagina
*Performanța modelului*: schimbi timpul de înjumătățire sau shrinkage-ul,
rulezi backtestul și vezi imediat efectul asupra RPS-ului.

---

## Ce scrie presa

Patru fluxuri RSS din presa sportivă românească, citite pentru un singur lucru:
**absențe, reveniri și transferuri**. RSS, nu scraping de pagini de listare —
fluxul e publicat anume ca să fie citit automat.

| Sursă | Articole în flux | Text integral | Categorie |
|---|---|---|---|
| **sport.ro** | 1106 | nu (~250 car.) | **„Superliga" = 412** |
| **fanatik.ro** | 72 | **da (~2900 car.)** | amestecat |
| digisport.ro | 100 | nu (~86 car.) | lipsește |
| prosport.ro | 20 | nu | „Fotbal Intern" |

sport.ro dă acoperirea, fanatik dă adâncimea. Pagina articolului se descarcă
doar pentru știrile care trec de filtru — de obicei zece-cincisprezece pe zi.
**De la sport.ro nu se descarcă deloc**: paginile lor pun teasere către alte
articole chiar în corpul știrii, iar din ele ieșeau fapte atribuite articolului
greșit. Categoria „Superliga" e însă exactă, iar rezumatul prinde de obicei
faptul din titlu.

### Trei niveluri de încredere

| | Marcă | Exemplu |
|---|---|---|
| ✅ **confirmat** | fapt împlinit, spus ca atare | „a semnat", „s-a accidentat", „nu va juca" |
| 🟡 **probabil** | urmează să se întâmple | „e aproape de", „99%", „urmează să" |
| ⚪ **zvon** | discuție, nu fapt | „potrivit surselor", „ar putea", „negociază" |

Cea mai **slabă** marcă din propoziție dă tonul: *„Potrivit surselor, a semnat
oficial"* rămâne zvon, fiindcă ancora e „potrivit surselor". Când nu există
nicio marcă, răspunsul e tot zvon — o știre care nu spune nimic ferm nu e o
confirmare.

### Cât de bine merge, numărat pe articole reale

Măsurat pe 31 august 2026, pe 21 de articole → 26 de fapte (13 confirmate,
1 probabil, 12 zvonuri). **Am citit toate cele 13 fapte confirmate și le-am
notat unul câte unul: 12 clar corecte, 1 marginal, 0 greșite.**

Primul rulaj arăta însă 12 corecte, **4 clar greșite** și 3 marginale. Cele
patru greșeli au fost reparate, iar fiecare a rămas ca test:

| Greșeala | Cauza | Repararea |
|---|---|---|
| „Andrei Rațiu" → jucătorul *Doru Andrei* | prenume citit ca nume de familie | prenumele frecvente nu mai potrivesc singure |
| „UTA Arad" → jucătorul *Ofri Arad* | nume de echipă citit ca jucător | numele echipelor se șterg înainte de căutare |
| Djokovic de la tenis → fundașul *Damjan Djokovic* | filtru de relevanță prea permisiv | categoria sursei decide **în ambele sensuri** |
| „a părăsit **terenul** la pauză" → transfer | verb corect, obiect greșit | listă de lucruri care nu sunt clubul |

Ultima era cea mai frecventă — trei din cele patru greșeli. „A părăsit ședința
de pregătire" și „a părăsit terenul" ieșeau amândouă ca plecări de la club.

### Ce nu poate face

* Nu înțelege ironia, citatele din altcineva („X a negat că pleacă") sau
  întrebările din titluri — deși un titlu terminat cu „?" nu mai poate fi
  „confirmat", ci cel mult „probabil".
* Într-o propoziție cu doi jucători și un singur verb, atribuie faptul celui
  mai apropiat de verb. E corect de cele mai multe ori, dar *„cine vine la FCSB
  după plecarea lui Tănase"* poate ieși ca venire pe numele lui Tănase. Astfel
  de cazuri cad aproape întotdeauna în „zvon".
* Numele de familie purtat de doi jucători din ligă nu se leagă de niciunul —
  faptul se consemnează cu numele scris în text, marcat **nelegat**.
* **„Confirmat" înseamnă că articolul o spune ca sigură**, nu că am verificat-o
  noi. De aceea propoziția-dovadă e mereu afișată: citești și decizi singur.

### Coroborarea

Când două publicații diferite spun același lucru, faptul primește 🔁 *2 surse*.
E singura verificare independentă pe care o avem fără să citim noi articolele.
La măsurătoarea de mai sus, accidentarea lui Dennis Politic apărea în două
surse, iar plecarea lui Florin Tănase în trei.

### Nimic din toate astea nu intră în model

Deliberat, și din același motiv ca la absențe: presa publică doar prezentul.
Nu putem ști ce se scria înaintea meciurilor deja jucate, deci un ajustaj
construit pe ele **nu ar putea fi validat**. Datele se strâng datate; când vor
fi câteva luni de istoric, întrebarea devine măsurabilă.

O observație din prima măsurătoare: niciuna dintre cele 4 absențe găsite în
presă nu apărea în lista de 25 de indisponibili de la Transfermarkt. Asta e de
așteptat — **presa e mai rapidă** — dar înseamnă și că verificarea încrucișată
nu are încă pe ce să se sprijine.

---

## Când sursele nu sunt de acord

Transfermarkt își actualizează loturile cu întârziere. Pe 6 septembrie 2026,
**Florin Tănase apărea încă în lotul FCSB** — deși trei publicații scriseseră,
cu o săptămână înainte, că a semnat cu Omonia Nicosia. La fel Daniel Bîrligea.

Nu e o eroare de citire: pagina de club chiar îi listează. Datele reflectă
fidel sursa; sursa e în urmă.

Aici își câștigă locul fluxurile de știri. `reported_departures()` compară cele
două surse și scoate conflictele — jucători dați plecați de presă, dar încă în
lot:

| Jucător | Încă listat la | Surse |
|---|---|---|
| Florin Tănase | FCSB | 3 |
| Anzor Mekvabishvili | Universitatea Craiova | 2 |
| Daniel Bîrligea | FCSB | 1 |

**Îi excludem din predicții** când articolul afirmă plecarea ca fapt
(`confirmat`), nu ca zvon.

### Verificate una câte una

Prima versiune semnala 8 jucători. Citindu-le pe rând, **4 erau greșite** —
trei cauze distincte, fiecare reparată și acoperită de un test:

| Greșeala | Cauza | Regula adăugată |
|---|---|---|
| „El Sawy a plecat de la **Rapid**" — el e la UTA Arad | plecare de la un club anterior | clubul din propoziție trebuie să fie clubul lui de acum |
| „Țicu pleacă de la **Dinamo**" — el e la Farul | idem | idem |
| Crețu s-a despărțit de FCSB… apoi a revenit | plecare anulată ulterior | o revenire confirmată mai nouă anulează plecarea |
| „Bîrligea a fost cedat de CFR **la** FCSB" | e o *venire*, cu același verb | „de la X" = dinspre X; „la Y" = către Y |

Au rămas 4, toate corecte. Verificat și invers: Crețu e din nou în predicții
pentru FCSB, Țicu pentru Farul.

### Când nici sursele nu știu

Bîrligea chiar plecase — la Frosinone — dar **nicio sursă nu o spunea**:
Transfermarkt îl lista, iar cele două știri despre el descriau *venirea* de la
CFR. Pentru astfel de cazuri există marcajul manual, pe pagina *Jucători →
Absenți*: îl marchezi plecat, e exclus ca oricare altul, iar marcajul se poate
anula. Nu șterge nimic.

Conflictele se văd pe pagina **Jucători → Absenți**, cu propoziția din articol,
ca să le poți verifica. Nu ascundem dezacordul, îl arătăm.

### Cine poate fi scos din lot

Prima versiune scotea pe oricine lipsea de pe pagina Transfermarkt. Greșit:
jucătorii aduși de LPF au numele legal complet („Manuel Alberto Cunha Lopes"),
iar Transfermarkt folosește nume scurte — nu se potrivesc niciodată. Așa au
ajuns „fără club" **16 jucători care jucaseră cu o zi înainte**, printre care
Korenica, Heras Garcia și Zandbergen.

Două gărzi acum:

* se judecă **doar jucătorii pe care sursa îi cunoaște** (au id Transfermarkt);
  absența celorlalți de pe pagină nu înseamnă nimic;
* nimeni care **a jucat în ultimele 30 de zile** nu e scos — cine a fost pe
  teren săptămâna trecută e la club.

### Transferuri în timpul sezonului

LPF vedea jucătorul în formația clubului nou, nu-l găsea în lotul acelui club
și crea un rând nou. Rezultatul: **doi Korenica**, unul la CFR Cluj și unul la
FCSB, cu istoricul rupt în două. Acum, înainte de a crea un rând, se caută
jucătorul la celelalte echipe — potrivire exactă pe nume, ca să nu unim oameni
diferiți — și se mută.

### Un jucător, un singur rând

Modelul grupa aparițiile pe `(jucător, echipă)`, deci un transfer îi rupea
istoricul în două: Korenica avea 418 minute, dar doar 45 la FCSB — sub pragul de
270, deci absent din predicțiile FCSB, în timp ce ratele lui de la CFR stăteau
nefolosite.

Acum gruparea e pe **jucător**, cu o separație care contează:

* **ratele la 90 de minute** (goluri, assist-uri, cartonașe) se calculează din
  *toate* aparițiile — sunt însușirea lui, nu a clubului;
* **minutele așteptate** se calculează doar din meciurile la clubul **actual**,
  raportate la câte meciuri a avut clubul de când a venit — rolul lui acolo e
  altă poveste decât la clubul precedent.

Echipa vine din lotul curent (`current_squads()`), nu din majoritatea
aparițiilor. Korenica apare acum la FCSB cu 45 de minute așteptate — exact cât
a jucat acolo — dar cu rata de goluri din toate cele 418.

Al doilea duplicat venea din completarea cu totaluri de sezon: acelea stau sub
clubul vechi, iar verificarea „îl am deja?" se făcea *pe echipă*, deci nu-l
găsea. Se caută acum și fără echipă.

### Și cine chiar a plecat din ligă

Separat: `upsert_player` muta un jucător când îl găsea la alt club **din ligă**,
dar cei plecați în străinătate nu mai apăreau nicăieri — rămâneau agățați de
fostul club pentru totdeauna. Acum, după fiecare citire a unui lot, cine nu mai
e în el se detașează (`team_id` devine NULL). Jucătorul **nu** se șterge:
aparițiile lui din meciurile trecute rămân, și e corect să rămână.

---

## Zero nu înseamnă „zero", când sursa n-a apucat să scrie

LPF publică pagina meciului **treptat**: imediat după fluierul final apar
carto­nașele, iar restul tabelului stă pe zero încă vreo câteva ore. Garda de
la citire cerea doar ca *ceva* să nu fie zero — cele două cartonașe îi
ajungeau — deci pagina trecea și scriam „0 cornere, 0 șuturi" ca valori
adevărate.

Al doilea capăt, fără de care primul n-ar fi ajutat: pagina se citea **o
singură dată în viața ei**. Ce s-a scris atunci rămânea acolo pentru
totdeauna.

### Cât de mult

Cinci din cele 61 de meciuri jucate anul acesta, verificate față de Flashscore:

| meci | în bază | de fapt |
|---|---|---|
| FCSB — UTA Arad | 0 cornere | 9 |
| CFR Cluj — Farul | 0 | 11 |
| Farul — FC Botoșani | 0 | 11 |
| FC Botoșani — Sepsi | 0 | 8 |
| Oțelul — Rapid | 0 | 9 |

Sezoanele 2022–2024, aduse de API-Football, n-au niciun asemenea rând: e strict
de la sursa LPF adăugată anul ăsta.

### Ce strica

Modelul de cornere și cartonașe se antrena pe zerourile astea, deci mediile
ligii ieșeau prea mici. Iar decontarea judeca „peste 8.5 cornere" contra unui
zero inventat: pontul *sub 8.5* de pe FCSB — UTA ieșise **câștigat** la un meci
cu nouă cornere, iar *peste 7.5* de pe același meci ieșise **pierdut**.

### Reparația

Reperul sunt statisticile care nu pot fi zero într-un meci chiar jucat: nicio
echipă nu termină cu zero pase și zero posesie. Când ele sunt zero, pagina nu e
completată, iar din ea nu se ia nimic — nici cartonașele, care la Oțelul —
Rapid arătau 2 când fuseseră 3.

Recitirea cere acum ca rândul stocat să fie **întreg** (`passes > 0`), altfel
pagina intră din nou în listă la următoarea rulare. `upsert_team_stats`
folosea deja `COALESCE`, deci o valoare adevărată suprascrie zeroul, iar un
`NULL` nu șterge ce se știe.

### Și nu era de ajuns

Pagina nu se umple dintr-o bucată, și nici mereu în aceeași ordine. La UTA Arad
— Csikszereda statisticile erau toate acolo — 450 de pase, 17 șuturi, 8 cornere
— dar **cartonașele** stăteau pe zero și au apărut abia peste două zile. Pontul
„peste 2.5 cartonașe" se decontase deja contra unui zero.

Orice gardă pusă pe un câmp anume ar rata alt câmp. Deci paginile meciurilor
din ultimele **7 zile se recitesc oricum**, până când LPF termină de completat:
vreo două etape la fiecare rulare, ieftin, și singurul mod de a prinde și
completările târzii, și corecturile.

Recitirea întregului sezon, o dată, a mai schimbat un singur meci — cele două
cartonașe de mai sus — dar a adus și datele pe jucători care lipseau, iar
biletul de marcatori din 5 septembrie a ieșit din „anulat" în **câștigat**.

Pentru biletele deja judecate pe cifrele greșite există `resettle_matches()`:
redeschide ponturile de pe meciurile date și le judecă din nou. Decontarea
obișnuită nu se mai uită la un pont odată decis — și bine face, altfel ar
rescrie istoria la fiecare rulare — dar când se repară **datele**, verdictul
trebuie refăcut. După reparație, Brier-ul pe piața de cornere a scăzut de la
0.274 la **0.258**.


---

## Cine are ultimul cuvânt pe cornere și cartonașe

LPF e sursa oficială a ligii și rămâne sursa pentru restul statisticilor. Dar
pe **cele două piețe pe care se pariază** s-a dovedit nesigură, iar comparația
cu Flashscore, pe toate cele 61 de meciuri ale sezonului, o arată:

| | coincid | diferă |
|---|---|---|
| cornere | 54 / 61 | 7 |
| cartonașe galbene | 53 / 59 | 6 |

Cele mai multe diferențe sunt de unu-doi și țin de cum numără fiecare sursă.
Una însă nu: la **Dinamo — FCSB**, LPF publică 2-2 cornere la un meci cu 32 de
șuturi, unde Flashscore dă 14.

Deci Flashscore are ultimul cuvânt pe `corners`, `yellow_cards` și
`red_cards`; LPF completează doar unde nu are nimeni nimic.

### Precedența nu depinde de ordinea pașilor

Tentația e să lași „cine scrie ultimul câștigă" și să pui pasul Flashscore
după cel LPF. Ar merge — până la prima reordonare a pașilor, când s-ar strica
în tăcere.

În loc de asta, `upsert_team_stats` primește `fill_only`: coloanele pe care
providerul le pune **doar dacă nu le are nimeni**. LPF le trece pe cele trei
acolo, Flashscore le scrie normal. Rezultatul e același în ambele ordini — și
există un test exact pentru asta.

```sql
-- normal:     valoarea nouă câștigă
corners = COALESCE(excluded.corners, match_team_stats.corners)
-- fill_only:  valoarea veche rămâne
corners = COALESCE(match_team_stats.corners, excluded.corners)
```

### Ce s-a schimbat la completare

Zece meciuri corectate, cel mai mult Dinamo — FCSB (4 → 14 cornere). Două
ponturi și-au schimbat verdictul — „sub 10.5 cornere" a trecut din pierdut în
câștigat, la fel „peste 2.5 cartonașe" — iar un bilet a ieșit pe plus.

Efectul pe acuratețe, măsurat *fără* nicio schimbare de model, doar pentru că
adevărul e mai exact:

| piață | Brier înainte | după | abatere de calibrare |
|---|---|---|---|
| cornere | 0.258 | **0.245** | −0.070 → **−0.041** |
| cartonașe | 0.174 | **0.165** | −0.025 → **−0.013** |

O cerere per meci, deci lista se ține scurtă: la o rulare obișnuită sunt cele
opt meciuri ale etapei, plus orice meci mai vechi căruia îi lipsesc cornerele.

---
## Ponturi live, din ce s-a întâmplat până acum

Ideea încape într-o propoziție: **ce s-a jucat e sigur, restul se estimează
din ritmul de până acum amestecat cu ce se aștepta înainte de start.** Trei
cornere în 45 de minute nu înseamnă nici șase la final (ritmul observat, luat
orbește), nici nouă și jumătate (așteptarea, ca și cum nu s-ar fi jucat nimic).

Amestecul e o actualizare Gamma-Poisson. Așteptarea cântărește cât un meci
întreg de observații, deci la minutul 45 cele două trag la fel; spre final
decide aproape numai ce s-a văzut — și trebuie să decidă, fiindcă atunci a mai
rămas puțin de estimat.

```
ritm_pe_90 = (a · λ + k) / (a + m / 90)        a = 1 meci de „greutate"
```

Restul de meci se numără cu **Poisson, nu Negative Binomial**. Dispersia
măsurată în `secondary.py` descrie împrăștierea unui meci întreg, în bună parte
din diferențele *dintre* meciuri — arbitru, miză, teren. La minutul 48 acelea
s-au întâmplat deja și nu se mai repetă pe rest; aplicată pe rămășiță, ar lărgi
degeaba intervalul. E același raționament pentru care `inplay.py` nu aplică
corecția Dixon-Coles pe restul de meci.

### Ce se afișează

Un pont pe piață, nu cinci linii vecine care spun același lucru, și numai
între 65% și 97%: sub prag nu merită spus, peste el nu mai e un pont — „peste
0.5 goluri" la 2-0 are probabilitatea 1 și nu e o știre.

**Ordinea e după cât a mutat meciul probabilitatea**, nu după cât de mare e.
Că favorita a rămas favorită nu spune nimic; că „sub 8.5 cornere" a urcat de la
43% la 82% spune.

| pontul | șansă | cotă corectă | înainte de meci | mișcare |
|---|---|---|---|---|
| cornere: sub 8.5 | 81.6% | 1.23 | 43.1% | **+38.5** |
| total goluri 2.5: sub | 86.9% | 1.15 | 49.4% | +37.5 |
| ambele marchează: nu | 80.1% | 1.25 | 49.4% | +30.6 |
| șansă dublă: 1X | 80.7% | 1.24 | 77.2% | +3.5 |

### Ce nu sunt

**Nu sunt „valoare".** Pe 1004 meciuri s-a măsurat că acolo unde modelul se
desparte de piață greșește mai des modelul. Aici nici măcar nu se compară cu o
cotă live — nu avem așa ceva. Cota afișată e cea *corectă* după model: profit
există doar dacă găsești una mai mare la casă.

**Cartonașele se adună spre final**, iar modelul presupune ritm constant. Nu
avem minutele lor în bază, deci profilul nu poate fi măsurat; până atunci,
„peste" la cartonașe iese subestimat. Scrie și pe pagină.

### Un bug găsit în timp ce se construia

Flashscore scrie „Univ. Craiova" și „'U' Cluj"; modelul cunoaște
„Universitatea Craiova" și „Universitatea Cluj". Fără traducere,
`attack.get(nume, 1.0)` întorcea tăcut forța medie a ligii pentru amândouă —
deci **toate** cifrele de pe pagina de Live erau calculate cu echipe medii:
35.8% pentru gazdă în loc de 42.0%. Nimeni n-ar fi observat, fiindcă numărul
ieșea plauzibil. Numele trec acum prin `canonical_team()`.


## Transferuri, cu bani cu tot

Pagina de transferuri a fiecărui club de la Transfermarkt are două tabele —
veniri și plecări — și, spre deosebire de presă, **publică sumele**. Se aduc la
actualizarea completă, o dată pe club, și ajung în tabelul `transfers`.

Sezonul curent: **581 de transferuri la 16 cluburi**, 9.34 mil. € plătiți și
18.21 mil. € încasați în toată liga.

### Gratis și nepublicată nu sunt același lucru

Cea mai ușoară greșeală de făcut aici e să pui pe zero o sumă care lipsește.
Un club care și-a vândut jucătorii pe sume nedezvăluite ar apărea atunci că i-a
dat degeaba — și bilanțul lui ar minți.

Deci `0.0` înseamnă **gratis, și se știe** („free transfer", „loan transfer"),
iar `None` înseamnă **nu s-a publicat** („?"). Numai primele intră în profit;
celelalte sunt numărate separat și scrise pe pagină, ca să știi cât de completă
e socoteala: **442 gratis, 96 nepublicate, 190 împrumuturi**.

Formele pe care le înțelege parserul:

| cum scrie sursa | ce înseamnă |
|---|---|
| `€1.00m`, `€600k` | suma, în euro |
| `free transfer` | gratis, cunoscut → `0` |
| `Loan fee: €1.50m` | împrumut **cu taxă** → suma, marcat împrumut |
| `loan transfer` | împrumut fără taxă → `0` |
| `?` | nepublicată → `None` |

Pe pagină, tabelul „cele mai scumpe mutări" păstrează o coloană cu **textul
original al sursei**, ca interpretarea să poată fi verificată fără să deschizi
Transfermarkt.

### Capul tabelului spune care e care

Nu poziția tabelului în pagină, ci antetul: coloana **„Left"** înseamnă *de
unde a venit* — deci tabelul de **veniri**; **„Joined"** înseamnă *unde a
plecat*. Invers de cum sună.

### Bilanțul, pe cluburi

|  | plătit | încasat | profit |
|---|---|---|---|
| FCSB | 1.70 mil. € | 5.10 mil. € | **+3.40 mil. €** |
| Rapid București | 0.35 mil. € | 2.90 mil. € | +2.55 mil. € |
| Farul Constanța | 0 | 2.30 mil. € | +2.30 mil. € |
| … | | | |
| Universitatea Craiova | 5.90 mil. € | 3.10 mil. € | **−2.80 mil. €** |

### O sursă mai bună decât presa pentru plecări

Efectul lateral util: transferurile spun cine a plecat **cu bani cu tot**, mai
devreme și mai sigur decât presa. Cazul care a pornit toată treaba — Daniel
Bîrligea era arătat în continuare la FCSB, deși plecase de o săptămână — se
rezolvă acum singur: rândul „FCSB → Frosinone, Loan fee: €1.50m" e destul.

Sunt puse în `departed_players()` cu două gărzi, ambele necesare:

* rândurile **„End of loan"** nu sunt plecări — sunt întoarceri la clubul
  proprietar, uneori cu dată în viitor (așa au fost păstrați Garita și
  Dumbravanu);
* nimeni care **a jucat în ultimele 30 de zile** nu e scos, orice ar scrie
  tabelul (așa a rămas Korenica, transferat dar prezent pe teren).


## Absențele: le colectăm, dar modelul nu le vede (încă)

Comentariul din schemă spune problema: *„Fără asta, modelul presupune că tot
lotul e apt, iar absența unui golgheter trece neobservată."* Exact informația
despre care știm că piața o are și noi nu.

Două lucruri s-au reparat aici:

1. **Absențele nu se actualizau din refresh deloc.** Pasul „Loturi și jucători"
   aducea loturile, dar nu chema `fetch_absences` — accidentările se
   împrospătau doar la ingestia completă a sezonului. Acum se aduc la fiecare
   actualizare completă.
2. **Se salvează o fotografie la fiecare actualizare.** Transfermarkt publică
   doar starea de *acum*; fără istoric nu vom ști niciodată cine lipsea la
   meciurile deja jucate, deci un ajustaj pentru absențe **nu s-ar putea măsura
   vreodată**. Tabelele `availability_snapshots` și
   `availability_snapshot_rows` rezolvă asta.

Antetul fotografiei se scrie chiar și când nu lipsește nimeni, ca să putem
deosebi „nimeni nu era absent" de „nu ne-am uitat": `availability_on(dată)` dă
absenții, `had_availability_snapshot(dată)` spune dacă avem dreptul la o
concluzie.

**Ajustajul în sine nu e implementat, deliberat.** Nu poate fi validat astăzi,
iar proiectul a respins deja calibrarea izotonă și avantajul de teren per echipă
tocmai fiindcă măsurătoarea nu le-a susținut. Peste câteva luni de fotografii,
întrebarea devine măsurabilă — atunci merită construit, nu înainte.

---

## Limitări, spuse pe față

* **xG-ul acoperă doar 2022–2024** (limita planului gratuit API-Football).
  Cornerele, cartonașele și șuturile din sezonul curent vin de la LPF.ro, dar
  xG nu e publicat de LPF.
* **Scorul live există, dar modelul din timpul meciului e simplu.** Vede doar
  scorul și minutul; nu știe de eliminări, de schimbarea de tactică după gol sau
  de oboseală. Iar minutul e uneori estimat din timpul scurs de la start, nu
  citit din feed — deci în jurul pauzei poate fi cu până la un sfert de oră
  greșit.
* **Modelul nu bate piața.** Cotele caselor încorporează informații pe care
  modelul nu le are — accidentări, lot rotit, motivație. Biletele sunt un
  instrument de analiză, nu o metodă de câștig.
* **Cotele pre-meci vin dintr-un endpoint nedocumentat.** Merge azi, verificat
  pe date reale; se poate opri oricând, fără preaviz. Codul semnalează asta
  explicit în loc să tacă, dar nu îl poate repara singur.
* **Backfill-ul de bilete e onest, dar scurt.** Pe câteva zeci de bilete,
  ROI-ul e dominat de noroc. Ai nevoie de câteva sute pentru o concluzie.
* **Piața de marcatori e nefolosibilă deocamdată.** Backfill-ul refăcut fără
  scurgere (6 septembrie 2026) arată cât de mult ascundea ea: modelul prezice
  **46,9 %**, se întâmplă **14,3 %** — de trei ori mai puțin, pe 21 de ponturi.
  Diferența e de **3 abateri standard**, deci nu e zgomot. Cu scurgerea
  activă, aceleași cifre arătau 46,3 % vs 36,4 %.

  Calibratorul a prins corecția (pe intervalul 50–65 % prezicea 57,4 % și s-a
  observat 0 % din 5), deci probabilitățile se domolesc automat. Dar 21 de
  ponturi sunt puține și pentru calibrator: **nu te baza pe biletele cu
  marcatori** până nu se strâng câteva sute.
* **Logica de „valoare" nu are avantaj demonstrabil** — ba pe dezacordurile
  mari pierde măsurabil. Vezi *Când modelul nu e de acord cu piața*.
* **Absențele nu intră în model**, deși se colectează. Vezi secțiunea de mai
  sus: nu pot fi validate până nu se strâng câteva luni de fotografii.
* **Nici știrile nu intră în model**, din același motiv. Iar extragerea din
  text liber e pe reguli: prinde bine faptele spuse simplu, se încurcă la
  ironie și la citate. Verdictul „confirmat" e al articolului, nu al nostru.

---

## Depanare

**Diacriticele apar greșit în terminal.** Rulează cu
`PYTHONIOENCODING=utf-8 python scripts/...` sau `chcp 65001` înainte.

**`streamlit` nu e recunoscut.** Folosește `python -m streamlit run ...`.

**API-Football întoarce `Too many requests`.** Ai depășit 10 cereri pe minut.
Codul așteaptă 6.5 secunde între cereri; dacă rulezi două scripturi simultan,
limita se depășește oricum. Rulează unul singur.

**„Free plans do not have access to this season".** Normal — vezi tabelul de
mai sus. Sezonul curent vine din celelalte surse.

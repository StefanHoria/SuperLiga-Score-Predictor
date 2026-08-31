"""Din text de stire in fapte structurate: cine lipseste, cine pleaca, cine vine.

Extragere pe reguli, nu pe model de limba. Consecinta directa: **prefera sa nu
stie decat sa ghiceasca**. Fiecare fapt pastreaza propozitia din care a iesit,
ca omul sa poata verifica; iar cand numele nu se leaga sigur de un jucator din
baza, `player_id` ramane gol in loc sa fie ghicit.

Trei niveluri de incredere, in ordinea in care le cautam:

    confirmat   "oficial", "a semnat", "comunicatul clubului", "a suferit o
                ruptura", "este suspendat" - fapt implinit, spus ca atare
    probabil    "urmeaza sa", "e aproape de", "99%", "ar urma sa" - se va
                intampla, dar inca nu s-a intamplat
    zvon        "surse", "se vehiculeaza", "ar putea", "negociaza" - discutie,
                nu fapt

Cand nu gasim niciun semn, coboram la `zvon`. Presupunerea implicita e
neincrederea: o stire fara marca de confirmare nu e o confirmare.

CE NU FACE, spus pe fata:

  * Nu intelege sarcasmul, citatele din altcineva ("X a negat ca pleaca") sau
    intrebarile retorice din titluri. Negatia simpla e tratata; restul, nu.
  * Nu deosebeste un jucator de omonimul lui daca in text apare doar numele de
    familie si liga are doi cu acelasi nume - atunci nu leaga deloc.
  * Titlurile de presa exagereaza. Un fapt "confirmat" inseamna ca ARTICOLUL o
    spune ca sigur, nu ca noi am verificat-o.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable

log = logging.getLogger(__name__)

CONFIRMED, LIKELY, RUMOUR = "confirmat", "probabil", "zvon"

#: ordinea conteaza: cea mai slaba marca gasita da tonul intregii propozitii
CERTAINTY_ORDER = (RUMOUR, LIKELY, CONFIRMED)

# ----------------------------------------------------------------------
# Marcile de incredere, in romana
# ----------------------------------------------------------------------
RUMOUR_MARKERS = (
    "se zvoneste", "zvon", "se vehiculeaza", "potrivit surselor", "surse din",
    "surse apropiate", "s-ar putea", "ar putea", "ar fi", "se pare ca",
    "negociaza", "in discutii", "discuta cu", "este dorit", "il vrea",
    "pe lista", "posibil", "speculatii", "se spune", "nu e exclus",
)
LIKELY_MARKERS = (
    "urmeaza sa", "ar urma sa", "aproape de", "este aproape", "e aproape",
    "in proportie de", "99%", "90%", "asteptat sa", "se indreapta spre",
    "va semna", "urmeaza semnatura", "aproape gata", "e ca si facut",
)
CONFIRMED_MARKERS = (
    "oficial", "a semnat", "au semnat", "a fost confirmat", "confirmat de",
    "comunicat", "clubul a anuntat", "a anuntat clubul", "s-a oficializat",
    "a fost prezentat", "verdictul", "a suferit", "este out", "e out",
    "va lipsi", "rateaza", "a fost suspendat", "este suspendat",
    "e suspendat", "s-a accidentat", "s-a rupt", "operatie", "a fost operat",
    "indisponibil", "a revenit", "s-a despartit", "a plecat", "a fost cedat",
    "a fost imprumutat", "transfer definitiv",
)

# ----------------------------------------------------------------------
# Ce fel de fapt e
# ----------------------------------------------------------------------
ABSENCE_MARKERS = (
    "accidentare", "s-a accidentat", "accidentat", "ruptura", "entorsa",
    "operatie", "a fost operat", "indisponibil", "va lipsi", "rateaza meciul",
    "rateaza partida", "out pentru", "suspendat", "suspendare", "cartonas rosu",
    "a fost eliminat", "scos din lot", "nu va juca", "nu poate juca", "leziune",
)
RETURN_MARKERS = (
    "a revenit", "revine", "recuperat", "s-a recuperat", "apt de joc",
    "este apt", "e apt", "revenire", "din nou in lot", "a scapat de accidentare",
)
ARRIVAL_MARKERS = (
    "a semnat cu", "a fost transferat la", "vine la", "s-a alaturat",
    "a fost prezentat la", "noul jucator al", "transfer la", "achizitia",
    "imprumutat la", "a fost cumparat de",
)
DEPARTURE_MARKERS = (
    "pleaca de la", "a plecat de la", "s-a despartit de", "paraseste",
    "a parasit", "a fost cedat", "cedat la", "reziliat", "rezilierea",
    "si-a reziliat", "pleaca la",
)
LINEUP_MARKERS = (
    "echipa probabila", "echipele probabile", "primul unsprezece",
    "primul 11", "va incepe cu", "formula de start", "posibil unsprezece",
    "probabilul unsprezece", "cum arata echipa",
)

#: marci de TIP care sunt ele insele afirmatii ferme, nu doar subiect. "S-a
#: accidentat" e un fapt spus ca atare; "negociaza" nu e. Fara distinctia asta,
#: orice propozitie fara cuvant de confirmare cadea in "zvon" - inclusiv
#: "nu va juca in meciul de duminica", care e cat se poate de categoric.
ASSERTIVE_MARKERS = (
    "s-a accidentat", "a fost operat", "operatie", "ruptura", "entorsa",
    "leziune", "indisponibil", "va lipsi", "rateaza meciul", "rateaza partida",
    "nu va juca", "nu poate juca", "suspendat", "a fost eliminat",
    "scos din lot",
    "a revenit", "s-a recuperat", "apt de joc", "este apt", "e apt",
    "pleaca de la", "a plecat de la", "s-a despartit de", "a parasit",
    "a fost cedat", "si-a reziliat", "a semnat cu", "a fost transferat la",
    "s-a alaturat", "a fost prezentat la", "imprumutat la",
)

KIND_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("formatie_probabila", LINEUP_MARKERS),
    ("revenire", RETURN_MARKERS),
    ("absenta", ABSENCE_MARKERS),
    ("transfer_venire", ARRIVAL_MARKERS),
    ("transfer_plecare", DEPARTURE_MARKERS),
)

#: Prenume romanesti frecvente. Multe sunt si nume de familie ("Radu Marin" vs
#: "Marin Radu"), deci cand apar SINGURE nu putem sti care e care. Le scoatem
#: din cautarea dupa nume de familie: "Andrei Ratiu" era legat de jucatorul
#: "Doru Andrei", greseala prinsa pe articole reale. Pierdem cateva mentiuni
#: adevarate, dar nu inventam niciuna - compromisul corect cand cerinta e
#: "stiri sigure".
COMMON_GIVEN_NAMES = {
    "adrian", "alexandru", "alin", "andrei", "bogdan", "catalin", "ciprian",
    "constantin", "cosmin", "cristian", "dan", "daniel", "darius", "denis",
    "dragos", "eduard", "emil", "florin", "gabriel", "george", "gheorghe",
    "ion", "ioan", "iulian", "lucian", "marian", "marin", "marius", "mihai",
    "nicolae", "octavian", "ovidiu", "paul", "petru", "radu", "razvan",
    "robert", "sebastian", "silviu", "sorin", "stefan", "tudor", "valentin",
    "vasile", "victor", "vlad",
}

#: Cuvinte care pot sta legitim inaintea unui nume de familie fara sa fie
#: prenume: prepozitii, articole si denumiri de post. Orice ALTCEVA inaintea
#: numelui inseamna, cel mai probabil, un prenume strain - deci alt om.
#: "Florinel Coman" nu e jucatorul nostru "Marius Coman".
SURNAME_CONNECTIVES = {
    "lui", "la", "cu", "pe", "de", "si", "iar", "ca", "dupa", "despre",
    "pentru", "in", "din", "prin", "spre", "catre", "fara", "langa", "intre",
    "al", "ale", "un", "o", "iar", "dar", "insa", "cand", "unde", "care",
    "fundasul", "atacantul", "mijlocasul", "portarul", "jucatorul",
    "capitanul", "internationalul", "veteranul", "titularul", "rezerva",
    "antrenorul", "presedintele", "patronul", "golgheterul", "starul",
    "coechipierul", "colegul", "elevul", "omul", "fotbalistul", "sportivul",
}

#: Ce poate fi "parasit" fara sa insemne plecarea de la club. Trei din patru
#: greseli de la prima masuratoare veneau de aici: "Garita a parasit TERENUL la
#: pauza" si "Tanase a parasit SEDINTA de pregatire" ieseau ca transferuri.
NOT_THE_CLUB = (
    "terenul", "gazonul", "suprafata de joc", "sedinta", "antrenamentul",
    "antrenamentele", "cantonamentul", "vestiarul", "sala", "stadionul",
    "conferinta", "tribuna", "banca", "lotul pentru", "tara",
)

#: La fel pentru reveniri: "a revenit asupra deciziei" e despre patron, nu
#: despre un jucator care si-a incheiat accidentarea.
NOT_A_RETURN = ("asupra", "la sentimente", "in atentie", "in discutie")

#: negatia care rastoarna sensul, cautata chiar inaintea marcii
NEGATIONS = ("nu ", "n-", "fara a fi", "nici o", "nicio")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"[A-Za-zĂÂÎȘȚăâîșțĂ-ț]+")


def normalise(text: str) -> str:
    """Text mic, fara diacritice - forma in care cautam marcile."""
    stripped = unicodedata.normalize("NFKD", str(text or ""))
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    # ș si ț cu virgula scapa uneori de NFKD
    stripped = stripped.replace("ş", "s").replace("ţ", "t")
    return re.sub(r"\s+", " ", stripped.lower()).strip()


def sentences(text: str) -> list[str]:
    """Taie textul in propozitii. Fiecare fapt trebuie sa vina dintr-una."""
    return [s.strip() for s in _SENTENCE_SPLIT.split(str(text or "")) if s.strip()]


# ----------------------------------------------------------------------
def certainty_of(sentence: str) -> str:
    """Cat de sigura suna propozitia.

    Cea mai SLABA marca prezenta da tonul: "surse spun ca a semnat oficial"
    ramane zvon, fiindca ancora e "surse spun". Altfel, orice zvon care contine
    un cuvant tare ar fi promovat la certitudine.

    Cand nu exista nicio marca, raspunsul e tot `zvon`: o stire care nu spune
    nimic ferm nu e o confirmare.
    """
    low = normalise(sentence)
    if any(m in low for m in RUMOUR_MARKERS):
        return RUMOUR
    # O intrebare nu e o afirmatie. "Baiaram pleaca de la U Craiova?" era
    # trecut la confirmat, desi titlul insusi se intreaba.
    if sentence.rstrip().endswith("?"):
        return LIKELY
    if any(m in low for m in LIKELY_MARKERS):
        return LIKELY
    if any(m in low for m in CONFIRMED_MARKERS):
        return CONFIRMED
    if any(m in low for m in ASSERTIVE_MARKERS):
        return CONFIRMED
    return RUMOUR


def _negated(low: str, marker: str) -> bool:
    """E marca precedata de o negatie? ("nu va lipsi" nu e o absenta)"""
    position = low.find(marker)
    if position < 0:
        return False
    window = low[max(0, position - 22):position]
    return any(n in window for n in NEGATIONS)


def _leaves_the_club(low: str, marker: str) -> bool:
    """Chiar despre club e vorba, sau doar despre teren?

    "A parasit terenul la pauza" nu e un transfer. Ne uitam la ce urmeaza
    imediat dupa verb.
    """
    after = low[low.find(marker) + len(marker):][:40].lstrip()
    return not any(after.startswith(word) for word in NOT_THE_CLUB)


def _returns_to_play(low: str, marker: str) -> bool:
    """Revine jucatorul, sau cineva revine asupra unei decizii?"""
    after = low[low.find(marker) + len(marker):][:30].lstrip()
    return not any(after.startswith(word) for word in NOT_A_RETURN)


def kind_of(sentence: str) -> tuple[str, str] | None:
    """Ce fel de fapt descrie propozitia, si ce marca l-a declansat."""
    low = normalise(sentence)
    for kind, markers in KIND_RULES:
        for marker in markers:
            if marker not in low or _negated(low, marker):
                continue
            if kind == "transfer_plecare" and not _leaves_the_club(low, marker):
                continue
            if kind == "revenire" and not _returns_to_play(low, marker):
                continue
            return kind, marker
    return None


# ----------------------------------------------------------------------
@dataclass
class PlayerIndex:
    """Cauta jucatori dupa nume in text, fara sa ghiceasca.

    Numele complet ("Andrei Ratiu") leaga sigur. Numele de familie singur
    ("Ratiu") leaga doar daca e unic in liga SI nu e in acelasi timp prenume la
    altcineva. Fara a doua conditie, "Andrei Ratiu" era legat de jucatorul
    "Doru Andrei", fiindca "Andrei" e numele lui de familie - o greseala pe
    care am prins-o pe articole reale.

    Inainte de cautare, numele echipelor se sterg din text. Altfel "UTA Arad"
    il gasea pe jucatorul "Ofri Arad", iar "FC Botosani" pe oricine se numeste
    Botosani.
    """

    by_full: dict[str, int] = field(default_factory=dict)
    by_surname: dict[str, list[int]] = field(default_factory=dict)
    given_names: set[str] = field(default_factory=set)
    teams: dict[int, int | None] = field(default_factory=dict)
    names: dict[int, str] = field(default_factory=dict)
    team_phrases: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, rows: Iterable[dict[str, Any]],
              team_names: Iterable[str] = ()) -> "PlayerIndex":
        index = cls()
        for row in rows:
            player_id = int(row["player_id"])
            name = str(row["name"] or "").strip()
            if not name:
                continue
            index.names[player_id] = name
            index.teams[player_id] = row.get("team_id")
            low = normalise(name)
            index.by_full.setdefault(low, player_id)
            parts = _WORD.findall(low)
            if len(parts) >= 2:
                index.by_surname.setdefault(parts[-1], []).append(player_id)
                index.given_names.update(parts[:-1])

        # cele mai lungi intai, ca "FC Botosani" sa fie sters inaintea lui
        # "Botosani" si sa nu ramana bucati
        index.team_phrases = sorted(
            {normalise(t) for t in team_names if t}, key=len, reverse=True)
        return index

    def mask_teams(self, text: str) -> str:
        """Scoate numele echipelor, ca sa nu fie citite drept jucatori."""
        low = normalise(text)
        for phrase in self.team_phrases:
            if phrase:
                low = low.replace(phrase, " ")
        return low

    def find(self, text: str) -> list[tuple[str, int | None, int]]:
        """Jucatorii din text: (nume, id sau None, pozitia in propozitie).

        Pozitia conteaza: cand o propozitie pomeneste doi jucatori si un singur
        verb ("cine vine la FCSB dupa plecarea lui Tanase"), faptul se atribuie
        celui mai apropiat de verb, nu amandurora.

        Daca a gasit macar un nume complet, se opreste acolo: numele de familie
        singure din aceeasi propozitie sunt aproape sigur aceleasi persoane,
        scrise a doua oara.
        """
        low = self.mask_teams(text)
        found: list[tuple[str, int | None, int]] = []
        seen: set[str] = set()

        for full, player_id in self.by_full.items():
            if len(full) <= 4:
                continue
            hit = re.search(rf"\b{re.escape(full)}\b", low)
            if hit and full not in seen:
                seen.add(full)
                found.append((self.names[player_id], player_id, hit.start()))

        if found:
            return sorted(found, key=lambda f: f[2])

        for surname, ids in self.by_surname.items():
            if (len(surname) < 4 or surname in self.given_names
                    or surname in COMMON_GIVEN_NAMES):
                continue
            hit = re.search(rf"\b{re.escape(surname)}\b", low)
            if not hit or surname in seen:
                continue
            seen.add(surname)

            # Un prenume strain inaintea numelui de familie inseamna alt om:
            # "Florinel Coman" nu e "Marius Coman". Consemnam numele asa cum e
            # scris, dar nu-l legam de nimeni.
            written, linked = self._resolve_surname(low, hit.start(), surname, ids)
            found.append((written, linked, hit.start()))
        return sorted(found, key=lambda f: f[2])

    def _resolve_surname(self, low: str, position: int, surname: str,
                         ids: list[int]) -> tuple[str, int | None]:
        """Numele de afisat si id-ul, pentru o potrivire pe nume de familie."""
        if len(ids) > 1:                      # omonimi in liga: nu ghicim
            return surname.title(), None

        player_id = ids[0]
        before = low[:position].rstrip()
        prefix = before.rsplit(" ", 1)[-1] if " " in before else before
        own = set(_WORD.findall(normalise(self.names[player_id]))[:-1])

        # Orice cuvant care nu e conectiv si nu e prenumele LUI inseamna alt om.
        # Regula e mai stricta decat o lista de prenume: "Florinel" nu apare in
        # nicio lista, dar e limpede ca nu e "Marius".
        if (prefix and prefix.isalpha() and len(prefix) >= 3
                and prefix not in own and prefix not in SURNAME_CONNECTIVES):
            return f"{prefix.title()} {surname.title()}", None
        return self.names[player_id], player_id


# ----------------------------------------------------------------------
@dataclass
class NewsFact:
    kind: str
    certainty: str
    player_name: str
    player_id: int | None
    team: str | None
    evidence: str
    detail: str | None = None


def extract_facts(text: str, index: PlayerIndex,
                  team_finder=None) -> list[NewsFact]:
    """Faptele dintr-un articol: cel mult unul pe propozitie.

    Un singur jucator per propozitie, cel mai apropiat de verbul care a dat
    tipul faptului. "Cine vine la FCSB dupa plecarea lui Tanase" pomeneste doi
    oameni si un singur verb; atribuindu-l amandurora, iesea un transfer de
    VENIRE pe numele celui care tocmai plecase.

    `team_finder` primeste o propozitie si intoarce numele canonic al echipei
    gasite in ea, sau None.
    """
    facts: list[NewsFact] = []
    seen: set[tuple[str, str]] = set()

    for sentence in sentences(text):
        found_kind = kind_of(sentence)
        if found_kind is None:
            continue
        kind, marker = found_kind

        players = index.find(sentence)
        if not players:
            continue

        marker_at = normalise(sentence).find(marker)
        if marker_at < 0:
            marker_at = 0
        name, player_id, _ = min(
            players, key=lambda p: abs(p[2] - marker_at))

        key = (kind, name)
        if key in seen:
            continue
        seen.add(key)
        facts.append(NewsFact(
            kind=kind, certainty=certainty_of(sentence), player_name=name,
            player_id=player_id,
            team=team_finder(sentence) if team_finder else None,
            evidence=sentence.strip()[:400], detail=marker))
    return _drop_shadow_duplicates(facts)


def _drop_shadow_duplicates(facts: list[NewsFact]) -> list[NewsFact]:
    """Scoate numele de familie nelegate cand acelasi om apare deja legat.

    "Valentin Cretu" (legat) si "Cretu" (nelegat, fiindca liga are doi Cretu)
    sunt acelasi fapt scris de doua ori in acelasi articol. Il pastram pe cel
    legat.
    """
    linked = {(f.kind, normalise(f.player_name)) for f in facts
              if f.player_id is not None}
    if not linked:
        return facts

    kept: list[NewsFact] = []
    for fact in facts:
        if fact.player_id is not None:
            kept.append(fact)
            continue
        shadow = normalise(fact.player_name)
        if any(kind == fact.kind and shadow in full for kind, full in linked):
            continue
        kept.append(fact)
    return kept


# ----------------------------------------------------------------------
def corroboration(facts: Iterable[dict[str, Any]]) -> dict[tuple[str, str], int]:
    """De cate SURSE diferite e sustinut fiecare fapt.

    Doua publicatii care spun acelasi lucru valoreaza mult mai mult decat una
    singura - e singura verificare independenta pe care o avem fara sa citim
    articolele noi insine.
    """
    seen: dict[tuple[str, str], set[str]] = {}
    for fact in facts:
        key = (str(fact["kind"]), str(fact["player_name"]))
        seen.setdefault(key, set()).add(str(fact.get("source") or ""))
    return {key: len(sources) for key, sources in seen.items()}

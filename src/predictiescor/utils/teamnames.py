"""Normalizarea numelor de echipe din SuperLiga.

Fiecare provider scrie numele altfel ("FCSB", "Steaua Bucuresti", "FC FCSB",
"FCSB Bucuresti"). Modulul mapeaza toate variantele la un nume canonic unic,
ca datele din API-Football / FBref / Sofascore / football-data.co.uk sa poata
fi unite pe aceeasi echipa.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import get_close_matches

# Nume canonic -> lista de variante intalnite la provideri
CANONICAL_TEAMS: dict[str, list[str]] = {
    "FCSB": [
        "fcsb", "fc fcsb", "steaua", "steaua bucuresti", "fcsb bucuresti",
        "fc steaua bucuresti", "steaua bucharest", "fcsb bucharest",
    ],
    "CFR Cluj": [
        "cfr cluj", "cfr 1907 cluj", "cfr", "cfr cluj napoca", "cfr 1907",
        "cfr cluj-napoca", "fc cfr 1907 cluj",
    ],
    "Universitatea Craiova": [
        "universitatea craiova", "u craiova", "cs u craiova", "craiova",
        "cs universitatea craiova", "u. craiova", "univ craiova",
    ],
    "FC U Craiova 1948": [
        "fc u craiova 1948", "fcu craiova", "fc u craiova", "craiova 1948",
        "fcu craiova 1948", "fc universitatea craiova 1948",
        "universitatea craiova 1948", "u craiova 1948", "univ craiova 1948",
        "u. craiova 1948", "craiova 1948 sa",
    ],
    "Rapid Bucuresti": [
        "rapid", "rapid bucuresti", "fc rapid", "fc rapid bucuresti",
        "rapid bucharest", "rapid buc",
    ],
    "Dinamo Bucuresti": [
        "dinamo", "dinamo bucuresti", "fc dinamo", "fc dinamo bucuresti",
        "dinamo bucharest", "dinamo 1948",
    ],
    "Farul Constanta": [
        "farul constanta", "farul", "fc farul constanta", "fcv farul",
        "fcv farul constanta", "viitorul",
        "viitorul constanta", "fc viitorul", "sc farul constanta",
    ],
    "Sepsi OSK": [
        "sepsi osk", "sepsi", "sepsi sfantu gheorghe", "sepsi osk sfantu gheorghe",
        "sepsi sf gheorghe", "sepsi sf. gheorghe", "acs sepsi osk",
    ],
    "FC Botosani": [
        "fc botosani", "botosani", "acs fc botosani",
    ],
    "UTA Arad": [
        "uta arad", "uta", "fc uta arad", "uta arad 1945",
    ],
    "Corvinul Hunedoara": [
        "corvinul", "corvinul hunedoara", "cs corvinul 1921 hunedoara",
        "corvinul 1921", "hunedoara",
    ],
    "Poli Timisoara": [
        "poli timisoara", "acs poli timisoara", "politehnica timisoara",
        "timisoara", "asu politehnica timisoara",
    ],
    "Pandurii Targu Jiu": [
        "pandurii", "pandurii targu jiu", "cs pandurii lignitul targu jiu",
    ],
    "FC Vaslui": [
        "vaslui", "fc vaslui",
    ],
    "ASA Targu Mures": [
        "targu mures", "asa targu mures", "asa tg mures", "tg mures",
    ],
    "FC Brasov": [
        "fc brasov", "brasov", "fc brasov steagul rosu",
    ],
    "Corona Brasov": [
        "corona brasov", "corona", "fc corona brasov",
    ],
    "Gloria Bistrita": [
        "bistrita", "gloria bistrita", "fc gloria 1922 bistrita",
    ],
    "Ceahlaul Piatra Neamt": [
        "ceahlaul", "ceahlaul piatra neamt", "piatra neamt",
    ],
    "CS Turnu Severin": [
        "cs turnu severin", "turnu severin", "drobeta turnu severin",
    ],
    "Daco-Getica Bucuresti": [
        "daco getica bucuresti", "daco-getica bucuresti", "daco getica",
        "juventus bucuresti", "juventus colentina",
    ],
    "Sageata Navodari": [
        "navodari", "sageata navodari",
    ],
    "Petrolul Ploiesti": [
        "petrolul ploiesti", "petrolul", "fc petrolul", "fc petrolul ploiesti",
        "petrolul 52", "petrolul 52 ploiesti",
    ],
    "Universitatea Cluj": [
        "universitatea cluj", "u cluj", "fc universitatea cluj", "u. cluj",
        "univ cluj", "fc u cluj",
    ],
    "Otelul Galati": [
        "otelul galati", "otelul", "asc otelul galati", "fc otelul galati",
    ],
    "Hermannstadt": [
        "hermannstadt", "fc hermannstadt", "afc hermannstadt", "sibiu",
        "hermannstadt sibiu",
    ],
    "Unirea Slobozia": [
        "unirea slobozia", "slobozia", "afc unirea 04 slobozia",
        "unirea 04 slobozia", "afc unirea slobozia",
    ],
    "Gloria Buzau": [
        "gloria buzau", "buzau", "sc gloria buzau", "fc gloria buzau",
        "gloria 2018 buzau",
    ],
    "Poli Iasi": [
        "poli iasi", "politehnica iasi", "csm politehnica iasi", "iasi",
        "csm poli iasi", "fc politehnica iasi",
    ],
    "Csikszereda": [
        "csikszereda", "csikszereda miercurea ciuc", "fk csikszereda",
        "miercurea ciuc", "fk csikszereda miercurea ciuc",
        "csikszereda m. ciuc", "csikszereda m ciuc",
    ],
    "Metaloglobus": [
        "metaloglobus", "metaloglobus bucuresti", "cs metaloglobus bucuresti",
        "cs metaloglobus",
    ],
    "FC Arges": [
        "fc arges", "arges", "fc arges pitesti", "pitesti", "arges pitesti",
    ],
    "FC Voluntari": [
        "fc voluntari", "voluntari",
    ],
    "Chindia Targoviste": [
        "chindia targoviste", "chindia", "ac chindia targoviste", "targoviste",
    ],
    "CS Mioveni": [
        "cs mioveni", "mioveni", "cs mioveni 2000",
    ],
    "Academica Clinceni": [
        "academica clinceni", "clinceni", "academica",
    ],
    "Astra Giurgiu": [
        "astra giurgiu", "astra", "fc astra giurgiu",
    ],
    "Gaz Metan Medias": [
        "gaz metan medias", "gaz metan", "medias", "cs gaz metan medias",
    ],
    "Concordia Chiajna": [
        "concordia chiajna", "chiajna", "cs concordia chiajna", "concordia",
    ],
    "Dunarea Calarasi": [
        "dunarea calarasi", "calarasi", "afc dunarea 2005 calarasi",
    ],
    "Ripensia Timisoara": [
        "ripensia timisoara", "ripensia",
    ],
    "Sportul Snagov": [
        "sportul snagov", "snagov", "turnu magurele",
    ],
}

# Tokeni generici de eliminat inainte de potrivirea fuzzy
# NB: "cfr" NU e zgomot - e chiar identitatea lui CFR Cluj.
_NOISE_TOKENS = {
    "fc", "fcv", "cs", "acs", "acsc", "afc", "afk", "asc", "csm", "scm", "sc",
    "ss", "fk", "ac", "club", "sportiv", "asociatia",
    "bucuresti", "bucharest", "napoca", "04", "52",
}

# Anii din denumiri ("FC Rapid 1923", "CFR 1907 Cluj") sunt decor, nu identitate.
# Exceptia - "1948" la Craiova - e prinsa mai jos de `_hard_rule`, care ruleaza
# INAINTE de orice normalizare, deci se poate elimina aici fara risc.
_YEAR_RE = re.compile(r"^(18|19|20)\d{2}$")

_ALIAS_INDEX: dict[str, str] = {}


def strip_diacritics(text: str) -> str:
    """Sterge diacriticele romanesti si orice alt semn combinat."""
    text = str(text)
    for src, dst in (
        ("\u0219", "s"), ("\u015f", "s"), ("\u0218", "S"), ("\u015e", "S"),
        ("\u021b", "t"), ("\u0163", "t"), ("\u021a", "T"), ("\u0162", "T"),
    ):
        text = text.replace(src, dst)
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def slugify(name: str) -> str:
    """Forma de comparatie: minuscule, fara diacritice sau punctuatie."""
    # unele surse lipesc pozitia in clasament de nume: "(7.) Farul", "FCSB (3.)"
    name = re.sub(r"\(\s*\d+\s*\.\s*\)", " ", str(name))
    name = strip_diacritics(name).lower().strip()
    name = re.sub(r"[^a-z0-9\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _core_tokens(name: str) -> str:
    """Elimina tokenii generici (FC, CS, 'Bucuresti', ani) pentru fuzzy match."""
    tokens = [t for t in slugify(name).split()
              if t not in _NOISE_TOKENS and not _YEAR_RE.match(t)]
    return " ".join(tokens) if tokens else slugify(name)


def _rebuild_index() -> None:
    _ALIAS_INDEX.clear()
    for canon, aliases in CANONICAL_TEAMS.items():
        _ALIAS_INDEX[slugify(canon)] = canon
        _ALIAS_INDEX.setdefault(_core_tokens(canon), canon)
        for alias in aliases:
            _ALIAS_INDEX[slugify(alias)] = canon
            _ALIAS_INDEX.setdefault(_core_tokens(alias), canon)


_rebuild_index()

_UNKNOWN: set[str] = set()


# Reguli dure aplicate INAINTE de orice potrivire fuzzy. Fiecare regula este
# (predicat pe slug) -> nume canonic. Exista pentru perechile de cluburi care
# se scriu aproape la fel dar sunt entitati complet diferite.
_HARD_RULES: list[tuple[str, str]] = [
    ("1948", "FC U Craiova 1948"),
]


def _hard_rule(slug: str) -> str | None:
    if "craiova" in slug and "1948" in slug:
        return "FC U Craiova 1948"
    return None


def canonical_team(name: str, *, fuzzy: bool = True, cutoff: float = 0.85) -> str:
    """Intoarce numele canonic al echipei.

    Daca numele nu poate fi mapat, intoarce inputul curatat (ca sa nu pierdem
    meciuri) si il inregistreaza in `unknown_teams()` pentru audit manual.
    """
    if not name or not str(name).strip():
        return ""

    slug = slugify(name)
    forced = _hard_rule(slug)
    if forced:
        return forced

    if slug in _ALIAS_INDEX:
        return _ALIAS_INDEX[slug]

    core = _core_tokens(name)
    if core in _ALIAS_INDEX:
        return _ALIAS_INDEX[core]

    if fuzzy:
        keys = list(_ALIAS_INDEX.keys())
        match = get_close_matches(slug, keys, n=1, cutoff=cutoff)
        if not match:
            match = get_close_matches(core, keys, n=1, cutoff=cutoff)
        if match:
            return _ALIAS_INDEX[match[0]]

    _UNKNOWN.add(str(name).strip())
    return str(name).strip()


def unknown_teams() -> list[str]:
    """Numele care nu au putut fi mapate - adauga-le in CANONICAL_TEAMS."""
    return sorted(_UNKNOWN)


def register_alias(alias: str, canonical: str) -> None:
    """Adauga la runtime un alias nou (folosit din pagina de administrare)."""
    CANONICAL_TEAMS.setdefault(canonical, []).append(slugify(alias))
    _rebuild_index()
    _UNKNOWN.discard(alias)


def all_canonical_teams() -> list[str]:
    return sorted(CANONICAL_TEAMS.keys())

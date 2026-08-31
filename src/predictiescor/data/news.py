"""Provider de stiri: fluxuri RSS din presa sportiva romaneasca.

De ce RSS si nu scraping de pagini de listare: fluxul e publicat anume ca sa
fie citit automat, e stabil, si ne scuteste de zeci de cereri. Pagina
articolului se descarca DOAR pentru stirile care trec de filtru - de obicei
zece-cincisprezece pe zi.

Ce da fiecare sursa, masurat pe 31 august 2026:

    sursa        articole  text integral in flux  categorie
    sport.ro       1106    nu (~250 caractere)    "Superliga" (412)
    fanatik.ro       72    da (~2900 caractere)   amestecat, "FCSB" (25)
    digisport.ro    100    nu (~86 caractere)     lipseste
    prosport.ro      20    nu                     "Fotbal Intern" (8)

De aceea: sport.ro da acoperirea, fanatik da adancimea, iar pentru celelalte
descarcam pagina cand titlul promite ceva.

Fluxurile contin mult tenis, mondenitati si fotbal strain. Filtrul de
relevanta e in doua trepte - categoria daca exista, altfel numele echipelor din
SuperLiga in titlu - iar apoi cerem si o marca de subiect (accidentare,
transfer, formatie) inainte sa cheltuim o cerere pe articol.

Termeni de utilizare: fluxurile sunt publice si destinate citirii automate.
Cererile se fac rar, cu pauza intre ele, si sursa se poate opri din `.env`
(`NEWS_ENABLED=false`).
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable

from .base import DataProvider, IngestResult
from .newsfacts import (
    KIND_RULES, PlayerIndex, extract_facts, normalise,
)

log = logging.getLogger(__name__)

CONTENT_NS = {"content": "http://purl.org/rss/1.0/modules/content/"}

#: Fluxurile: (sursa, adresa, categorii acceptate, descarcam pagina?)
#:
#: La sport.ro NU descarcam pagina, desi fluxul da doar un rezumat: paginile
#: lor pun teasere catre alte articole chiar in corpul stirii, iar din ele
#: ieseau fapte atribuite articolului gresit. Categoria "Superliga" e insa
#: exacta si rezumatul de ~250 de caractere prinde de obicei faptul din titlu.
FEEDS: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    ("sport.ro", "https://www.sport.ro/rss",
     ("superliga", "fotbal intern"), False),
    ("fanatik.ro", "https://www.fanatik.ro/feed", (), True),
    ("digisport.ro", "https://www.digisport.ro/rss", (), True),
    ("prosport.ro", "https://www.prosport.ro/feed", ("fotbal intern",), True),
)

#: sursele de la care nu descarcam pagina articolului
SUMMARY_ONLY = {source for source, _, _, fetch in FEEDS if not fetch}

#: sub atat, ce vine in flux e doar un rezumat - merita descarcat articolul
MIN_BODY_CHARS = 400

#: nu ne uitam mai in urma de atat
DEFAULT_WINDOW_DAYS = 5

#: cat din corpul articolului folosim; dincolo de atat incep teaserele
BODY_LIMIT = 2500

_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")


def strip_html(html: str) -> str:
    text = _TAG.sub(" ", _SCRIPT.sub(" ", str(html or "")))
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
            .replace("&quot;", '"').replace("&#039;", "'")
            .replace("&bdquo;", '"').replace("&rdquo;", '"'))
    return re.sub(r"[ \t]+", " ", text).strip()


@dataclass
class Article:
    source: str
    url: str
    title: str
    summary: str
    body: str
    category: str | None
    published_at: datetime | None

    @property
    def text(self) -> str:
        """Tot ce stim despre articol, pentru cautarea de fapte."""
        return "\n".join(p for p in (self.title, self.summary, self.body) if p)


def _text_of(item: ET.Element, tag: str) -> str:
    node = item.find(tag)
    return (node.text or "").strip() if node is not None and node.text else ""


def _published(item: ET.Element) -> datetime | None:
    raw = _text_of(item, "pubDate")
    if not raw:
        return None
    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def parse_feed(xml: str, source: str) -> list[Article]:
    """Articolele dintr-un flux RSS."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise RuntimeError(f"flux ilizibil de la {source}: {exc}") from exc

    articles: list[Article] = []
    for item in root.findall(".//item"):
        link = _text_of(item, "link")
        title = _text_of(item, "title")
        if not link or not title:
            continue
        encoded = item.find("content:encoded", CONTENT_NS)
        body = strip_html(encoded.text) if encoded is not None and encoded.text else ""
        articles.append(Article(
            source=source, url=link, title=strip_html(title),
            summary=strip_html(_text_of(item, "description")),
            body=body,
            category=_text_of(item, "category") or None,
            published_at=_published(item),
        ))
    return articles


# ----------------------------------------------------------------------
def is_relevant(article: Article, categories: tuple[str, ...],
                team_words: set[str]) -> bool:
    """Vorbeste articolul despre SuperLiga?

    Cand sursa publica o categorie utila, ea decide - si decide in ambele
    sensuri. Initial categoria doar promova un articol, iar restul cadeau pe
    cautarea numelor de echipa; asa a trecut o stire despre Djokovic la tenis,
    legata apoi de fundasul Damjan Djokovic din liga. Daca sursa spune
    "Tenis", nu ne mai uitam.
    """
    if categories:
        if not article.category:
            return False
        return normalise(article.category) in categories
    haystack = normalise(f"{article.title} {article.summary}")
    return any(word in haystack for word in team_words)


def looks_useful(article: Article) -> bool:
    """Promite articolul un fapt de tipul celor cautate?

    Filtrul rulează INAINTE de descarcarea paginii, deci decide pe titlu si
    rezumat. Preferam sa scapam un articol decat sa descarcam cincizeci degeaba.
    """
    haystack = normalise(article.text)
    return any(marker in haystack
               for _, markers in KIND_RULES for marker in markers)


# ----------------------------------------------------------------------
class NewsProvider(DataProvider):
    name = "stiri"
    provides = ("absente", "reveniri", "transferuri", "formatii probabile")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # presa nu e o sursa de date: o interogam rar si politicos
        self.http.delay = max(self.http.delay, 3.0)
        self._index: PlayerIndex | None = None
        self._team_words: set[str] | None = None

    @property
    def available(self) -> bool:
        return self.cfg.secrets.news_enabled

    # ------------------------------------------------------------------
    def _team_vocabulary(self) -> set[str]:
        """Cuvintele dupa care recunoastem o echipa din liga in text.

        Luam si numele intreg, si cuvantul distinctiv ("botosani", "corvinul"),
        sarind peste cele generice care ar potrivi orice ("fc", "cs", "sport").
        """
        if self._team_words is not None:
            return self._team_words
        generic = {"fc", "cs", "cfr", "acs", "afc", "sc", "fcv", "u", "sport",
                   "club", "municipal", "asociatia", "1948", "otelul"}
        words: set[str] = set()
        for name in self.db.current_season_team_names():
            low = normalise(name)
            words.add(low)
            for token in re.findall(r"[a-z]+", low):
                if len(token) >= 5 and token not in generic:
                    words.add(token)
        self._team_words = words
        return words

    def _find_team(self, sentence: str):
        from ..utils.teamnames import canonical_team

        low = normalise(sentence)
        for name in self.db.current_season_team_names():
            if normalise(name) in low:
                return name
        for token, name in self._token_to_team().items():
            if re.search(rf"\b{re.escape(token)}\b", low):
                return canonical_team(name)
        return None

    def _token_to_team(self) -> dict[str, str]:
        generic = {"fc", "cs", "cfr", "acs", "afc", "sc", "fcv", "sport",
                   "club", "municipal", "bucuresti", "1948"}
        mapping: dict[str, str] = {}
        for name in self.db.current_season_team_names():
            for token in re.findall(r"[a-z]+", normalise(name)):
                if len(token) >= 5 and token not in generic:
                    mapping.setdefault(token, name)
        return mapping

    def _player_index(self) -> PlayerIndex:
        if self._index is None:
            with self.db.connect() as conn:
                rows = conn.execute(
                    "SELECT player_id, name, team_id FROM players").fetchall()
            self._index = PlayerIndex.build(
                [dict(r) for r in rows], self.db.current_season_team_names())
        return self._index

    # ------------------------------------------------------------------
    def fetch_articles(self, *, window_days: int = DEFAULT_WINDOW_DAYS,
                       limit: int = 40) -> list[Article]:
        """Articolele relevante si promitatoare din toate fluxurile."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        team_words = self._team_vocabulary()
        picked: list[Article] = []

        for source, url, categories, _fetch in FEEDS:
            try:
                xml = self.http.get_text(url, ttl_hours=0.25)
                articles = parse_feed(xml, source)
            except Exception as exc:  # noqa: BLE001
                log.warning("fluxul %s nu a putut fi citit: %s", source, exc)
                continue

            for article in articles:
                if article.published_at and article.published_at < cutoff:
                    continue
                if not is_relevant(article, categories, team_words):
                    continue
                if not looks_useful(article):
                    continue
                picked.append(article)

        picked.sort(key=lambda a: a.published_at or datetime.min.replace(
            tzinfo=timezone.utc), reverse=True)
        return picked[:limit]

    def fetch_body(self, article: Article) -> Article:
        """Descarca pagina articolului cand fluxul a dat doar un rezumat."""
        if article.source in SUMMARY_ONLY:
            return article
        if len(article.body) >= MIN_BODY_CHARS:
            return article
        try:
            html = self.http.get_text(article.url, ttl_hours=72)
        except Exception as exc:  # noqa: BLE001
            log.debug("articolul %s nu s-a putut descarca: %s", article.url, exc)
            return article

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        for junk in soup(["script", "style", "nav", "footer", "aside"]):
            junk.decompose()
        container = (soup.select_one("article") or soup.select_one("main")
                     or soup.body)
        if container is not None:
            article.body = re.sub(
                r"\n{3,}", "\n\n", container.get_text("\n", strip=True))[:20000]
        return article

    # ------------------------------------------------------------------
    def ingest(self, articles: Iterable[Article]) -> IngestResult:
        """Scrie articolele si faptele extrase din ele."""
        result = IngestResult(source=self.name)
        index = self._player_index()

        for article in articles:
            facts = extract_facts(article.text, index, team_finder=self._find_team)
            if not facts:
                continue
            article_id = self.db.upsert_news_article(
                url=article.url, source=article.source, title=article.title,
                summary=article.summary, body=article.body,
                category=article.category,
                published_at=(article.published_at.isoformat()
                              if article.published_at else None))
            written = self.db.save_news_facts(
                article_id,
                [{"kind": f.kind, "certainty": f.certainty,
                  "player_name": f.player_name, "player_id": f.player_id,
                  "team": f.team, "evidence": f.evidence, "detail": f.detail}
                 for f in facts],
                published_at=(article.published_at.isoformat()
                              if article.published_at else None))
            result.player_stats += written
            result.matches += 1
        return result

    def refresh(self, *, window_days: int = DEFAULT_WINDOW_DAYS,
                limit: int = 40) -> IngestResult:
        """Un ciclu complet: fluxuri -> articole -> fapte."""
        articles = self.fetch_articles(window_days=window_days, limit=limit)
        if not articles:
            return IngestResult(source=self.name)
        full = [self.fetch_body(a) for a in articles]
        return self.ingest(full)

    # ------------------------------------------------------------------
    def fetch_season(self, season: int) -> IngestResult:
        """Presa nu are sezoane - aducem ce e proaspat."""
        if season != self.cfg.current_season:
            return IngestResult(source=self.name)
        try:
            return self.refresh()
        except Exception as exc:  # noqa: BLE001
            return IngestResult(source=self.name, errors=[str(exc)])

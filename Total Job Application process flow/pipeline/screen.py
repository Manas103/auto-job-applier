"""Eligibility screen. Alex is a U.S. permanent resident applying for Summer 2027 with a May 2028 graduation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .config import Config
from .util import today as _today

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], start=1)}
_MONTHS.update({"jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
                "oct": 10, "nov": 11, "dec": 12})
_SEASON_MONTH = {"spring": 5, "summer": 8, "fall": 12, "autumn": 12, "winter": 1}

_DATE_TOKEN = r"(?:(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|spring|summer|fall|autumn|winter)\.?\s+)?(?P<year>20\d\d)"

CITIZEN_HARD = re.compile(
    r"(must be|required to be|need to be|only)\s+(a\s+)?(u\.?\s?s\.?|united states)\s+citizens?"
    r"|u\.?\s?s\.?\s+citizenship\s+(is\s+)?required"
    r"|citizenship\s+(is\s+)?required"
    r"|(u\.?\s?s\.?|united states)\s+citizens?\s+only"
    r"|requires?\s+(active\s+)?(u\.?\s?s\.?\s+)?citizenship",
    re.I)
CITIZEN_SOFT = re.compile(r"u\.?\s?s\.?\s+citizen|citizenship|security clearance|\b(secret|top secret|ts/sci)\b|\bclearance\b", re.I)
ITAR = re.compile(r"\bITAR\b|export[- ]control|u\.?\s?s\.?\s+person", re.I)
# Language that admits Alex even next to a citizenship line: permanent resident, green card, US national, etc.
PR_OK = re.compile(r"permanent resident|green[- ]card|lawful permanent|\bu\.?\s?s\.?\s+national|"
                   r"authorized to work|work authorization|no sponsorship", re.I)


@dataclass
class ScreenResult:
    verdict: str = "pass"  # "pass" | "screen_out"
    reason: str | None = None
    flags: list[str] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)

    def out(self, reason: str, **ev: str) -> "ScreenResult":
        self.verdict = "screen_out"
        self.reason = reason
        self.evidence.update(ev)
        return self

    def flag(self, name: str, **ev: str) -> None:
        if name not in self.flags:
            self.flags.append(name)
        self.evidence.update(ev)

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "reason": self.reason, "flags": list(self.flags), "evidence": dict(self.evidence)}


def _norm_term(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().lower())


def _target_year(cfg: Config) -> int:
    for t in cfg.screen.target_terms:
        m = re.search(r"20\d\d", t)
        if m:
            return int(m.group(0))
    return 2027


def screen_metadata(job: Any, cfg: Config, today: date | None = None) -> ScreenResult:
    """Screen on listing metadata only. `job` needs .sponsorship .terms .title .category .degrees .locations."""
    res = ScreenResult()
    target_terms = {_norm_term(t) for t in cfg.screen.target_terms}
    target_year = _target_year(cfg)
    target_seasons = {t.split()[0] for t in target_terms if t}

    sp = (getattr(job, "sponsorship", "") or "").strip()
    if re.search(r"citizenship", sp, re.I) and re.search(r"required", sp, re.I):
        return res.out("citizenship_required_metadata", sponsorship=sp)

    terms = [_norm_term(t) for t in (getattr(job, "terms", None) or [])]
    terms = [t for t in terms if t and t not in ("n/a", "na", "none")]
    if not terms:
        res.flag("term_unknown")
    else:
        ok = False
        for t in terms:
            if t in target_terms:
                ok = True
                break
            # "Spring/Summer 2027", "Summer-Fall 2027": the target season and year both appear.
            if any(season in t for season in target_seasons) and str(target_year) in t:
                ok = True
                break
            if t in target_seasons or (any(season in t for season in target_seasons) and not re.search(r"20\d\d", t)):
                ok = True
                res.flag("term_unknown", term=t)
                break
        if not ok:
            return res.out(f"term_mismatch:{','.join(terms)}")

    title = getattr(job, "title", "") or ""
    tl = title.lower()
    years = {int(y) for y in re.findall(r"\b(20\d\d)\b", tl)}
    if years and target_year not in years:
        return res.out(f"title_year_mismatch:{sorted(years)}", title=title)
    title_seasons = {("fall" if s == "autumn" else s) for s in re.findall(r"\b(summer|fall|spring|winter|autumn)\b", tl)}
    if title_seasons and not (title_seasons & target_seasons):
        return res.out(f"title_term_mismatch:{','.join(sorted(title_seasons))}", title=title)

    cats = set(cfg.screen.categories)
    cat = getattr(job, "category", "") or "Other"
    if cat not in cats:
        return res.out(f"category:{cat}")

    degrees = [str(d).lower() for d in (getattr(job, "degrees", None) or [])]
    req = (cfg.screen.require_degree or "").lower()
    if req and degrees and not any(req.rstrip("'s") in d for d in degrees):
        return res.out(f"degree_mismatch:{degrees}")

    excl = cfg.screen.exclude_title_regex
    if excl and re.search(excl, tl, re.I):
        return res.out("title_excluded", title=title)

    locs = getattr(job, "locations", None) or []
    if locs:
        kinds = {classify_location(l) for l in locs}
        if "us" not in kinds:
            joined = "; ".join(locs)
            if kinds == {"non_us"} and bool(cfg.screen.get("us_locations_only", True)):
                # Certain non-U.S. posting: a hard gate (U.S. work authorization only), removed silently.
                return res.out("non_us_location", locations=joined)
            # Uncertain: Alex decides. Mixed non-U.S. plus unrecognized keeps the stronger flag.
            res.flag("non_us_location" if "non_us" in kinds else "location_unknown", locations=joined)
    return res


_US_STATE = re.compile(r"\b(A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[DLNA]|K[SY]|LA|M[EDAINSOT]|N[EVHJMYCD]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[TA]|W[AVIY])\b")
_US_EXPLICIT = re.compile(r"united states|\bu\.?s\.?a?\b|\bd\.c\.|washington,?\s*d\.?c\b|puerto rico", re.I)
_US_STATE_NAMES = (
    "alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|idaho|illinois|"
    "indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|"
    "montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|"
    "oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|"
    "west virginia|wisconsin|wyoming")
_US_CITIES = (
    "nyc|manhattan|brooklyn|jersey city|hoboken|san francisco|sf|south sf|bay area|silicon valley|palo alto|menlo park|"
    "mountain view|sunnyvale|santa clara|san jose|cupertino|redwood city|oakland|berkeley|seattle|redmond|bellevue|"
    "kirkland|los angeles|santa monica|irvine|san diego|chicago|boston|austin|dallas|plano|houston|denver|boulder|"
    "atlanta|miami|west palm beach|boca raton|tampa|orlando|philadelphia|pittsburgh|arlington|mclean|reston|herndon|"
    "baltimore|charlotte|raleigh|durham|nashville|columbus|minneapolis|detroit|ann arbor|phoenix|scottsdale|"
    r"salt lake city|las vegas|portland|kansas city|st\.? louis|cincinnati|cleveland|indianapolis|milwaukee|madison|"
    "stamford|greenwich|princeton|newark|sacramento|san antonio|omaha|richmond|rochester|buffalo|albany|providence|"
    "new haven|wilmington|hartford|louisville|memphis|tulsa|oklahoma city|fort worth|el paso|tucson|fresno|mesa|"
    "colorado springs|virginia beach|long beach|jacksonville|new orleans|honolulu|anchorage|boise|des moines|"
    "little rock|birmingham|montgomery|jackson|baton rouge|charleston|columbia|greenville|lexington|knoxville|"
    "chattanooga|dayton|toledo|akron|grand rapids|lansing|champaign|urbana|west lafayette|bloomington|iowa city|"
    "ames|lincoln|fargo|sioux falls|cheyenne|billings|missoula|reno|spokane|tacoma|eugene|corvallis|state college|"
    "ithaca|syracuse|new brunswick|piscataway|bethesda|rockville|silver spring|alexandria|fairfax|chantilly|"
    r"gainesville|tallahassee|fort lauderdale|st\.? petersburg|sarasota|naples")
_US_HINT = re.compile(rf"\b(?:{_US_STATE_NAMES}|{_US_CITIES}|remote)\b", re.I)
_NON_US = re.compile(
    r"\b(?:canada|canadian|united kingdom|u\.?k\.?|england|scotland|wales|britain|ireland|france|germany|spain|italy|"
    r"netherlands|belgium|switzerland|sweden|denmark|norway|finland|poland|czech\w*|austria|portugal|israel|india|"
    r"china|hong kong|singapore|japan|south korea|korea|taiwan|australia|new zealand|mexico|brazil|argentina|"
    r"colombia|chile|peru|united arab emirates|uae|dubai|abu dhabi|qatar|saudi arabia|bahrain|south africa|nigeria|"
    r"kenya|egypt|turkey|hungary|romania|bulgaria|greece|luxembourg|estonia|latvia|lithuania|slovakia|slovenia|"
    r"croatia|serbia|ukraine|vietnam|thailand|malaysia|indonesia|philippines|pakistan|bangladesh|sri lanka|"
    r"cayman\w*|bermuda|bahamas|"
    r"london|manchester|edinburgh|glasgow|leeds|bristol|belfast|dublin|cork|paris|lyon|berlin|munich|frankfurt|"
    r"hamburg|amsterdam|rotterdam|eindhoven|brussels|zurich|geneva|basel|vienna|toronto|vancouver|montr\S*al|"
    r"ottawa|calgary|edmonton|waterloo|kitchener|halifax|mississauga|bangalore|bengaluru|hyderabad|mumbai|pune|"
    r"chennai|delhi|gurgaon|gurugram|noida|kolkata|sydney|melbourne|brisbane|perth|auckland|tokyo|osaka|seoul|"
    r"shanghai|beijing|shenzhen|hangzhou|taipei|tel aviv|herzliya|haifa|warsaw|krakow|wroclaw|prague|stockholm|"
    r"copenhagen|oslo|helsinki|madrid|barcelona|lisbon|milan|rome|bucharest|budapest|s[a\u00e3]o paulo|mexico city|"
    r"guadalajara|monterrey|bogot[a\u00e1]|santiago|buenos aires|lima|cape town|johannesburg|lagos|nairobi)\b", re.I)
_CA_PROVINCE = re.compile(r",\s*(ON|BC|QC|AB|MB|SK|NS|NB|NL|PE|YT|NT|NU)\b")


def classify_location(loc: str) -> str:
    """'us' (certain), 'non_us' (certain), or 'unknown'. Explicit U.S. markers win over foreign city names
    (Paris, TX); foreign countries win over 'remote' (Remote in Canada)."""
    s = loc or ""
    if _US_EXPLICIT.search(s) or _US_STATE.search(s):
        return "us"
    if _NON_US.search(s) or _CA_PROVINCE.search(s):
        return "non_us"
    if _US_HINT.search(s):
        return "us"
    return "unknown"


def _is_us_location(loc: str) -> bool:
    return classify_location(loc) == "us"


def _parse_date_token(mon: str | None, year: str) -> tuple[int, int]:
    y = int(year)
    if not mon:
        return y, 6
    m = mon.lower().rstrip(".")
    if m in _SEASON_MONTH:
        return y, _SEASON_MONTH[m]
    return y, _MONTHS.get(m, 6)


def parse_grad_window(text: str) -> tuple[tuple[int, int] | None, tuple[int, int] | None, str] | None:
    """Return ((y,m) earliest | None, (y,m) latest | None, snippet) or None when no graduation bound is stated."""
    t = re.sub(r"\s+", " ", text or "")
    pats = [
        (rf"graduat\w*[^.;]{{0,80}}?\bbetween\s+{_DATE_TOKEN.replace('(?P<mon>', '(?P<mon1>').replace('(?P<year>', '(?P<year1>')}\s+(?:and|to|-|through)\s+{_DATE_TOKEN.replace('(?P<mon>', '(?P<mon2>').replace('(?P<year>', '(?P<year2>')}", "between"),
        (rf"graduat\w*[^.;]{{0,80}}?\b(?:by|before|no later than|on or before|prior to)\s+{_DATE_TOKEN}", "latest"),
        (rf"graduat\w*[^.;]{{0,80}}?\b(?:after|on or after|no earlier than|not before)\s+{_DATE_TOKEN}", "earliest"),
        (rf"graduat\w*[^.;]{{0,80}}?\b(?:in|date of|date:?)\s+{_DATE_TOKEN}\s*(?:or|and)\s+(?:later|after|beyond)", "earliest"),
        (rf"graduat\w*[^.;]{{0,80}}?\b(?:in|date of|date:?)\s+{_DATE_TOKEN}\s*(?:or|and)\s+(?:earlier|before|sooner)", "latest"),
        (rf"class of\s+(?P<year>20\d\d)", "classof"),
        (rf"expected graduation[^.;]{{0,40}}?(?P<year>20\d\d)(?:\s*(?:or|-|to)\s*(?P<year2>20\d\d))?", "classof"),
    ]
    for pat, kind in pats:
        m = re.search(pat, t, re.I)
        if not m:
            continue
        snippet = m.group(0)[:160]
        gd = m.groupdict()
        if kind == "between":
            return (_parse_date_token(gd.get("mon1"), gd["year1"]), _parse_date_token(gd.get("mon2"), gd["year2"]), snippet)
        if kind == "latest":
            return (None, _parse_date_token(gd.get("mon"), gd["year"]), snippet)
        if kind == "earliest":
            return (_parse_date_token(gd.get("mon"), gd["year"]), None, snippet)
        if kind == "classof":
            y1 = int(gd["year"])
            y2 = int(gd["year2"]) if gd.get("year2") else y1
            return ((y1, 1), (y2, 12), snippet)
    if re.search(r"graduat", t, re.I) and re.search(r"20\d\d", t):
        return None
    return None


def screen_jd(job: Any, jd_text: str, cfg: Config, today: date | None = None) -> ScreenResult:
    res = ScreenResult()
    text = jd_text or ""
    sp = (getattr(job, "sponsorship", "") or "")

    hard = CITIZEN_HARD.search(text)
    if hard:
        # If the same neighborhood as the citizenship line admits permanent residents, it is not disqualifying.
        near = text[max(0, hard.start() - 120): hard.end() + 120]
        admits_pr = bool(PR_OK.search(near))
        if not admits_pr and bool(cfg.screen.get("citizenship_required_out", True)):
            return res.out("jd_citizenship_required", snippet=_context(text, hard))
        if re.search(r"citizenship", sp, re.I) and re.search(r"required", sp, re.I) and not admits_pr:
            return res.out("citizenship_required_metadata", snippet=hard.group(0))
        res.flag("jd_citizenship_required", snippet=_context(text, hard))
    else:
        soft = CITIZEN_SOFT.search(text)
        if soft:
            res.flag("jd_mentions_citizenship_or_clearance", snippet=_context(text, soft))
    it = ITAR.search(text)
    if it:
        res.flag("itar_us_person", snippet=_context(text, it))

    grad_cfg = cfg.screen.applicant.graduation  # "2028-05"
    gy, gm = int(grad_cfg[:4]), int(grad_cfg[5:7])
    gw = parse_grad_window(text)
    if gw is None and re.search(r"graduat\w*[^.;]{0,60}\b20\d\d\b", text, re.I):
        res.flag("grad_window_unparsed")
    elif gw is not None:
        earliest, latest, snippet = gw
        me = (gy, gm)
        if earliest and me < earliest:
            return res.out(f"grad_window:{snippet}")
        if latest and me > latest:
            return res.out(f"grad_window:{snippet}")

    target_year = _target_year(cfg)
    seasons = "|".join(sorted({t.split()[0] for t in cfg.screen.target_terms if t}))
    yrs = {int(y) for y in re.findall(rf"\b(?:{seasons})\s+(20\d\d)\b", text, re.I)}
    if yrs and target_year not in yrs and not re.search(rf"\b{target_year}\b", text):
        return res.out(f"jd_term_mismatch:{sorted(yrs)}")
    return res


def _context(text: str, m: re.Match, width: int = 80) -> str:
    a = max(0, m.start() - width)
    b = min(len(text), m.end() + width)
    return re.sub(r"\s+", " ", text[a:b]).strip()

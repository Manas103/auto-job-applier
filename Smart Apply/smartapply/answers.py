"""Build the answer packet for one application.

Order of authority: the profile (standard fields, EEO, knock-outs) > experience_descriptions > answer memory >
Claude for the open-ended remainder. Claude never touches knock-out questions, and every Claude answer is validated
(option labels, lengths, em dashes) before it is written to answers.json.

The deterministic layer is deliberately broad: it was tuned against the question sets of every Greenhouse, Lever
and Ashby posting in the pipeline (2026-09-24), so the same rules apply to any board, not just the ones tested.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from . import db, intake
from .claude_runner import run_claude
from .config import Config
from .log import app_log_path, get_logger
from .models import Answer, Application, Question
from .paths import PLAYBOOKS_DIR
from .profile import DECLINE, Profile
from .util import read_json, scan_em_dashes, write_json

log = get_logger("answers")

YES_NO = {"yes": ("yes", "y", "true"), "no": ("no", "n", "false")}

# Label patterns that are knock-outs: answered from the profile or left to the human, never to Claude.
# "comfortable with" only as a terms question ("comfortable with this range"); a skills question ("AI technologies you
# are comfortable with", Harvey 2026-10-04) is Claude's to answer from the resume.
KNOCKOUT = re.compile(r"authori[sz]ed to work|work authori[sz]ation|sponsor|visa|citizen|clearance|export.?control|\bitar\b|"
                      r"relocat|commut|located in|based in|on-?site|in[- ]office|in person|hybrid|headquarters|remote|"
                      r"salary|compensation|hourly|\bpays?\b|per hour|/hour|\bwage|pay range|pay rate|comfortable with (the|this|that|these|our)\b|"
                      r"\b18\b|eighteen|previously (worked|employed)|former employee|currently employed|non-?compete|"
                      r"drug (test|screen)|start date|availab|entire duration|full[- ]time|offer deadline|hours per week|"
                      r"hours a week|preferred (geographic )?location|convict|criminal|pretrial|felony|misdemeanor|debarr", re.I)

# "What are your salary expectations?" with no posted pay: the one knock-out Claude answers, with an estimate of what
# the company usually pays for the role (Alex, 2026-10-13)
SALARY_ASK = re.compile(r"desired (salary|pay|compensation|rate)|(salary|pay|hourly|compensation)[^?.]{0,25}(expectation|requirement)|"
                        r"expected (salary|pay|compensation)|hourly rate|what (salary|pay)", re.I)

# Answered only from the profile; never composed. Without an address they are left to Alex.
LOCATION_IDS = {"location", "candidate-location", "_systemfield_location"}

# Alternatives and free-text extras that stay blank: the PDF is attached, and optional "anything else" boxes
# are not worth a Claude call.
MOOT_IDS = {"resume_text", "cover_letter_text", "cover_letter", "comments", "urls[Twitter]", "urls[Other]"}

# Optional questions nobody but Alex can answer; skipped silently instead of paying Claude to say so.
OPTIONAL_SKIP = re.compile(r"referr|employee('s)? name|pronoun|twitter|additional information|anything else|"
                           r"cover letter|name pronunciation|^if (other|yes|so|applicable|selected)\b|please specify", re.I)

# "If you answered yes above, please specify ...", "If so, ...": a follow-up to the question before it.
# a question that asks for a fact AND the story behind it ("...and what inspired you to choose it?")
ESSAY_TAIL = re.compile(r"inspir|\bwhy\b|what (led|made|drew|brought|motivated)|motivat|tell us|explain|describe", re.I)
FOLLOW_UP = re.compile(r"^\s*if (you answered |you selected |you said )?[\"'\u201c]?(yes|so)\b", re.I)

SCHOOL_ASK = re.compile(r"(which|what) (college|university|school)|(university|college|school) (do|did|are|were) you|"
                        r"(currently |last )?attend(ing|ed)? (school|university|college)|(school|university|college) (you )?(currently |last )?attend|"
                        r"alma mater|name of (your )?(school|university|college)|^school name|"
                        r"re-?confirm the (university|school|college)|select your (school|university|college)|"
                        r"^(school|university|college)( name)?$|what is your (school|college|university)\b|"
                        r"^(current )?(school|university|college)( or (school|university|college))?( name)?\s*:?\s*\*?$", re.I)

EEO_KEYS = {"gender": "gender", "race": "ethnicity", "hispanic_ethnicity": "hispanic_latino",
            "veteran_status": "veteran", "disability_status": "disability",
            "eeo[gender]": "gender", "eeo[race]": "ethnicity", "eeo[veteran]": "veteran", "eeo[disability]": "disability",
            "_systemfield_eeoc_gender": "gender", "_systemfield_eeoc_race": "ethnicity",
            "_systemfield_eeoc_veteran_status": "veteran", "_systemfield_eeoc_disability_status": "disability",
            "_systemfield_eeoc_hispanic_ethnicity": "hispanic_latino"}

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


class NeedsHuman:
    """Sentinel returned by the profile lookup: the profile knows this question is Alex's to answer."""

    def __init__(self, reason: str):
        self.reason = reason


@dataclass
class AnswerPacket:
    answers: dict[str, Answer]
    unanswered_required: list[str]
    claude_cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"answers": [a.to_dict() for a in self.answers.values()], "unanswered_required": self.unanswered_required,
                "claude_cost_usd": self.claude_cost_usd}

    @classmethod
    def load(cls, path: Path) -> "AnswerPacket":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        answers = {a["question_id"]: Answer.from_dict(a) for a in d.get("answers", [])}
        return cls(answers=answers, unanswered_required=list(d.get("unanswered_required", [])),
                   claude_cost_usd=float(d.get("claude_cost_usd", 0)))


# ---------------------------------------------------------------- option matching

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def match_option(options: list[str], wanted: Any) -> str | None:
    """Pick the option label that means `wanted`. Exact, then normalized, then yes/no and decline synonyms."""
    if wanted is None or not options:
        return None
    if isinstance(wanted, bool):
        wanted = "Yes" if wanted else "No"
    w = str(wanted).strip()
    wl = w.lower()
    norm = {o: _norm(o) for o in options}
    for o in options:
        if o.strip().lower() == wl:
            return o
    wn = _norm(wl)
    for o, n in norm.items():
        if n == wn:
            return o
    if wl in ("yes", "no"):
        for o, n in norm.items():
            if n.split(" ")[0] == wl:
                return o
        return None
    # gender wording differs per form: Male / Man / Cisgender man (DraftKings, 2026-10-01: "Man" was not matched and
    # the form fell back to "I prefer not to disclose")
    for group in (("male", "man", "men", "cisgender man", "cis man", "cisgender male"),
                  ("female", "woman", "women", "cisgender woman", "cis woman", "cisgender female")):
        if wn in group:
            for o, n in norm.items():
                if n in group:
                    return o
            break
    # race subgroups (Five Rings, 2026-10-06: a plain "Asian" wish picked "East Asian"): a South Asian profile takes
    # that subgroup or "Asian Indian" first, then a plain Asian option, never another Asian subgroup
    if wn == "south asian":
        for o, n in norm.items():
            if re.search(r"\bsouth asian\b|\basian indian\b", n):
                return o
        for o, n in norm.items():
            if re.search(r"\basian\b", n) and not re.search(r"\b(east|southeast)\s+asian\b", n):
                return o
        return None
    if wn in ("heterosexual", "straight"):
        for o, n in norm.items():
            if re.search(r"\bheterosexual\b|\bstraight\b", n):
                return o
        return None
    us_names = ("united states", "united states of america", "us", "usa", "u s", "u s a")
    if wn in us_names:
        for o, n in norm.items():
            if n in us_names:
                return o
    if "decline" in wl or "not wish" in wl or "prefer not" in wl or "do not want" in wl:
        for o, n in norm.items():
            if any(k in n for k in ("decline", "don t wish", "do not wish", "prefer not", "not to answer", "do not want",
                                    "not want to answer", "rather not")):
                return o
    if wl.startswith("i am not a protected veteran") or wl == "not a veteran":
        claims = ("identify as a veteran", "i am a veteran", "identify as one or more", "i identify as a protected")
        honest = {o: n for o, n in norm.items() if not any(c in n for c in claims)}
        for o, n in honest.items():          # he never served: "I am not a veteran" beats "not a protected veteran"
            if re.search(r"\bnot a veteran\b|\bnever served\b|no military service", n):
                return o
        for o, n in honest.items():
            if "not a protected veteran" in n:
                return o
        for o, n in norm.items():          # a plain Yes / No question ("Are you a protected veteran?")
            if n == "no":
                return o
    if wl.startswith("no, i do not have a disability") or wl.startswith("no disability"):
        for o, n in norm.items():
            if n.startswith("no i do not") or n.startswith("no i don t"):
                return o
        for o, n in norm.items():          # a plain Yes / No question ("Do you have a disability?")
            if n == "no":
                return o
    hits = [o for o, n in norm.items() if wn and (n.startswith(wn) or wn in n)]
    return min(hits, key=lambda o: len(norm[o])) if hits else None


def gpa_option(options: list[str], gpa: float) -> str | None:
    """'3.7 out of 4.0', '3.5 - 3.74', 'Below 2', '3.75 - 4', '3.9+'."""
    for o in options:
        s = re.sub(r"(out of|/|on a)\s*\d+(\.\d+)?(\s*scale)?", "", o, flags=re.I)
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]
        if len(nums) >= 2 and nums[0] <= gpa <= nums[1]:
            return o
        if len(nums) == 1 and abs(nums[0] - gpa) < 0.005 and not re.search(r"below|under|less than|above|higher|\+", o, re.I):
            return o
    for o in options:
        m = re.search(r"(below|under|less than)\s*(\d+(?:\.\d+)?)", o, re.I)
        if m and gpa < float(m.group(2)):
            return o
        m = re.search(r"(\d+(?:\.\d+)?)\s*(\+|or (above|higher|better)|and (above|higher))", o, re.I)
        if m and gpa >= float(m.group(1)):
            return o
    return None


def grad_option(options: list[str], ym: str) -> str | None:
    """'May 2028', '05/2028', 'Spring 2028', 'January 2028 - July 2028', '2028'."""
    try:
        y, m = int(ym[:4]), int(ym[5:7])
    except (TypeError, ValueError):
        return None
    month = MONTHS[m - 1]
    season = "Spring" if m <= 5 else ("Summer" if m <= 8 else "Fall")
    exact = {f"{month} {y}", f"{month[:3]} {y}", f"{month[:3]}. {y}", f"{m:02d}/{y}", f"{m}/{y}", f"{season} {y}",
             f"{y}-{m:02d}", f"{month}, {y}", f"{season}, {y}", f"{season} of {y}"}
    exact_n = {_norm(x) for x in exact}
    for o in options:
        if _norm(o) in exact_n:
            return o
    idx = {mo.lower()[:3]: i + 1 for i, mo in enumerate(MONTHS)}
    for o in options:
        r = re.findall(r"([A-Za-z]{3,9})\.?\s+(\d{4})", o)
        if len(r) == 2 and r[0][0].lower()[:3] in idx and r[1][0].lower()[:3] in idx:
            a = (int(r[0][1]), idx[r[0][0].lower()[:3]])
            b = (int(r[1][1]), idx[r[1][0].lower()[:3]])
            if a <= (y, m) <= b:
                return o
    for o in options:
        # "May/June 2028" (Geneva Trading, 2026-10-10): either month of the pair
        r = re.fullmatch(r"\s*([A-Za-z]{3,9})\.?\s*/\s*([A-Za-z]{3,9})\.?\s+(\d{4})\s*", o)
        if r and int(r.group(3)) == y and m in {idx.get(r.group(1).lower()[:3]), idx.get(r.group(2).lower()[:3])}:
            return o
        # "Spring/Summer 2028" (Lyft, 2026-10-10): either season of the pair
        if r and int(r.group(3)) == y and season.lower() in (r.group(1).lower(), r.group(2).lower()):
            return o
    if options and all(_norm(o) in {_norm(x) for x in MONTHS} for o in options if _norm(o) not in ("", "select", "select one")):
        # a month-only list ("Please select your graduation month", Whatnot 2026-10-10); the year is asked separately
        for o in options:
            if _norm(o) == _norm(month):
                return o
    for o in options:
        on = _norm(o)
        if any(x in on for x in (_norm(f"{month} {y}"), _norm(f"{season} {y}"), _norm(f"{m:02d}/{y}"))):
            return o
    for o in options:
        # "May - Aug 2028", "Sept - Dec 2027" (Verkada, 2026-10-14): a month range inside one year
        r = re.fullmatch(r"\s*([A-Za-z]{3,9})\.?\s*[-–to]+\s*([A-Za-z]{3,9})\.?,?\s+(\d{4})\s*", o)
        if r and int(r.group(3)) == y and r.group(1).lower()[:3] in idx and r.group(2).lower()[:3] in idx \
                and idx[r.group(1).lower()[:3]] <= m <= idx[r.group(2).lower()[:3]]:
            return o
    for o in options:
        if re.fullmatch(rf"\s*{y}\s*", o):
            return o
    # "2028 academic school year" (Khan Academy, 2026-10-14): the academic year that ends in his graduation year
    hits = [o for o in options if re.search(rf"\b{y}\b[^0-9]*academic", o, re.I)]
    if len(hits) == 1 and m <= 8:
        return hits[0]
    return None


def _other_option(options: list[str]) -> str | None:
    for o in options:
        if re.match(r"\s*other\b", o, re.I) and not re.search(r"other (school|university|college) not listed", o, re.I):
            continue
        if re.match(r"\s*other\b", o, re.I):
            return o
    for o in options:
        if re.match(r"\s*other\b", o, re.I):
            return o
    return None


# ---------------------------------------------------------------- deterministic answers

def _decline_option(options: list[str]) -> str | None:
    for o in options:
        if re.search(r"decline|prefer not|do not wish|don't wish|wish not|choose not|rather not|not to (answer|disclose|say)|i do not want|"
                     r"not declared|undeclared|no response|not disclosed|not specified|withhold", o, re.I):
            return o
    return None


def _statement_option(options: list[str], yes_no: str) -> str | None:
    """Options phrased as statements ("Yes, I would like...", "No, please do not..."): pick by leading word."""
    want = str(yes_no).strip().lower()[:1]
    for o in options:
        if o.strip().lower().startswith(want):
            return o
    return None


def _today_mdy() -> str:
    return date.today().strftime("%m/%d/%Y")


def _ym_from_text(text: str) -> str | None:
    """'May 2027' -> '2027-05'."""
    m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{4})", text or "")
    if not m:
        return None
    idx = {mo.lower()[:3]: i + 1 for i, mo in enumerate(MONTHS)}
    mon = idx.get(m.group(1).lower()[:3])
    return f"{m.group(2)}-{mon:02d}" if mon else None


def _id_value(qid: str, profile: Profile) -> Any:
    p = profile
    a = p.address
    table = {
        "first_name": p.first_name, "last_name": p.last_name,
        "preferred_name": p.preferred_name or p.first_name,
        "name": p.full_name, "_systemfield_name": p.full_name, "full_name": p.full_name,
        "email": p.email, "_systemfield_email": p.email,
        "phone": p.phone_digits,
        "country": re.sub(r"\s+of America$", "", a.country, flags=re.I),
        "org": p.current_organization, "company": p.current_organization,
        "urls[LinkedIn]": p.linkedin, "linkedin": p.linkedin,
        "urls[GitHub]": p.github, "github": p.github,
        "urls[Portfolio]": p.portfolio or p.github, "website": p.portfolio or p.github, "portfolio": p.portfolio or p.github,
        "resume": "__resume__", "_systemfield_resume": "__resume__",
        "eeo[disabilitySignature]": p.eeo.signature_name or p.full_name,
        "eeo[disabilitySignatureDate]": _today_mdy(),
    }
    if qid in table:
        return table[qid]
    if qid in LOCATION_IDS:
        if a.city and (a.state_name or a.state):
            return f"{a.city}, {a.state_name or a.state}"
        return NeedsHuman("address not set in profile.yaml (run setup)")
    if qid in EEO_KEYS:
        return getattr(p.eeo, EEO_KEYS[qid])
    return None


def _eeo_by_label(label: str, profile: Profile) -> Any:
    if "hispanic" in label or "latino" in label:
        return profile.eeo.hispanic_latino
    if "transgender" in label:
        return profile.eeo.transgender
    if "orientation" in label:
        return profile.eeo.sexual_orientation
    if "gender" in label or label.strip() in ("sex",):
        return profile.eeo.gender
    if "race" in label or "ethnic" in label:
        return profile.eeo.ethnicity
    if "veteran" in label:
        return profile.eeo.veteran
    if "disab" in label:
        return profile.eeo.disability
    if re.search(r"\bname\b|signature", label):
        return profile.eeo.signature_name or profile.full_name
    if "date" in label:
        return _today_mdy()
    return DECLINE


# Countries that appear on export-control / sanctions lists; two or more in a label means the list is in the label.
EMBARGO_NAMES = re.compile(r"iran|cuba|syria|north korea|russia|belarus|venezuela|crimea|sudan|myanmar|burma|china|"
                           r"afghanistan|iraq|libya|yemen|nicaragua|uzbekistan|kazakhstan", re.I)
_NON_US = re.compile(r"\b(can|canada|toronto|montreal|ontario|quebec|bc|british columbia|uk|united kingdom|england|london|"
                     r"ireland|dublin|india|bangalore|bengaluru|hyderabad|pune|singapore|hong kong|japan|tokyo|germany|"
                     r"berlin|munich|france|paris|amsterdam|netherlands|zurich|switzerland|australia|sydney|israel|"
                     r"tel aviv|mexico|brazil|poland|warsaw|china|shanghai|beijing|taiwan|korea|seoul|spain|madrid|"
                     r"sweden|stockholm|emea|apac|latam)\b", re.I)
_US_STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH "
              "OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
_US = re.compile(r",\s*(" + "|".join(_US_STATES) + r")\b|\bUSA?\b|\b(SF|LA|DC|NY)\b|(?i:united states|u\.s\.|new york|nyc|san francisco|"
                 r"bay area|chicago|los angeles|seattle|boston|austin|washington|miami|denver|atlanta|philadelphia|"
                 r"dallas|houston|san diego|san jose|palo alto|mountain view|menlo park|sunnyvale|pittsburgh|nashville|"
                 r"san mateo|redwood city|foster city|santa clara|cupertino|irvine|bellevue|redmond|kirkland|cambridge)")


def _us_location(option: str) -> bool:
    return bool(_US.search(option)) and not _NON_US.search(re.sub(r",\s*CA\b", "", option))


def rank_locations(options: list[str], prefs: list[str]) -> list[str]:
    """U.S. options only, best first by the profile's city order; U.S. options it does not name keep their order last."""
    def rank(o: str) -> int:
        return next((i for i, c in enumerate(prefs) if c.lower() in o.lower()), len(prefs))
    return sorted([o for o in options if _us_location(o)], key=rank)


def _score_option(options: list[str], value: str) -> str | None:
    """The option holding a test score: an exact number, a range ("1500-1600") or a floor ("1500+"); or the
    "did not take" option when the profile says so."""
    if not re.fullmatch(r"\d+", value.strip()):
        for o in options:
            if re.search(r"did not|didn'?t|have not|haven'?t|don'?t have|do not have|not tak|n/?a\b|not applicable|none|no score", o, re.I):
                return o
        return None
    v = int(value)
    for o in options:
        nums = [int(n) for n in re.findall(r"\d{2,4}", o)]
        if len(nums) == 1 and (nums[0] == v or (re.search(r"\+|or (above|higher|more)|and (above|up)", o) and v >= nums[0])):
            return o
        if len(nums) == 2 and nums[0] <= v <= nums[1]:
            return o
    return None


def _profile_value_for(q: Question, profile: Profile, job: dict | None = None) -> Any:
    """A value from the profile for a standard/EEO/education question, a NeedsHuman sentinel, or None
    (meaning: not deterministic; memory or Claude may answer). `job` is the pipeline row (title, locations, pay)
    for the rules that depend on the posting (commute, start date, salary)."""
    qid, label = q.id, q.label.lower()
    is_choice = q.type in ("select", "multiselect")
    is_text = q.type in ("text", "textarea", "number")
    v = _id_value(qid, profile)
    if v is not None:
        return v
    # "Enter today's date below to confirm that you have read..." (S&P HiredScore, 2026-10-15): the notice also talks
    # about disability accommodation, and the EEO rules put "No, I do not have a disability" into this date field
    if q.type == "date" and re.search(r"today['’]?s date|current date", label):
        return date.today().isoformat()
    if q.type == "file" and re.search(r"transcript|grade report|academic record", label):
        if profile.transcript_path and Path(profile.transcript_path).exists():
            return "__transcript__"
        return NeedsHuman("transcript_path not set in profile.yaml")
    if q.section == "eeoc" or q.section == "demographic":
        if re.search(r"pronoun", label) and profile.pronouns:
            # Greenhouse files pronouns under demographics (Klaviyo, 2026-10-01): answer from the profile
            return profile.pronouns
        if q.section == "demographic" and not re.search(r"gender|hispanic|latino|race|ethnic|veteran|disab|sex|orientation", label):
            return DECLINE
        return _eeo_by_label(label, profile)
    s = profile.school
    if q.section == "education":
        return {"school--0": s.name, "degree--0": s.degree_select, "discipline--0": s.major,
                "start-month--0": MONTHS[int(s.start[5:7]) - 1], "start-year--0": s.start[:4],
                "end-month--0": MONTHS[int(s.graduation[5:7]) - 1], "end-year--0": s.graduation[:4]}.get(qid)
    d = profile.defaults
    wa = profile.work_auth

    # ---- contact and links by label (Ashby and Lever custom fields carry no standard ids)
    if re.fullmatch(r"\s*(phone( number)?|mobile( number)?|telephone)\s*\*?\s*", label):
        return profile.phone_digits
    # "Please provide your phone number" (Bedrock, 2026-10-14)
    if re.search(r"(provide|enter|share) your (phone|mobile)|^\s*your (phone|mobile)( number)?", label) and is_text:
        return profile.phone_digits
    # "Please state your full legal name:" (Shield AI, 2026-10-14)
    if re.search(r"full (legal )?name|legal name", label) and is_text \
            and not re.search(r"preferred|emergency|reference|referr|parent|guardian|contact|manager|native|former|other name", label):
        return profile.full_name
    if re.search(r"linkedin", label) and is_text:
        return profile.linkedin
    if re.search(r"github", label) and is_text:
        return profile.github
    if re.search(r"\b(website|portfolio|personal site|personal website)\b", label) and is_text:
        return profile.portfolio or profile.github
    if re.search(r"preferred (first )?name|what would you like us to call you", label) and is_text:
        return profile.preferred_name or profile.first_name
    if re.search(r"pronoun", label):
        return profile.pronouns or NeedsHuman("pronouns not set in profile.yaml")
    if re.search(r"native (language|script)|name in (your )?native|name in (the )?(local|original) (script|language)", label) and is_text:
        return profile.native_name or NeedsHuman("native_name not set in profile.yaml")
    if re.search(r"notice period|notice (do|would) you (need|have) to give", label):
        if is_choice:
            for o in q.options:
                if re.search(r"\bnone\b|immediate|not applicable|n/?a\b|^\s*0\b|no notice", o, re.I):
                    return o
            # no "none": the shortest bracket ("Less than 30 days", S&P 2026-10-15)
            for o in q.options:
                if re.search(r"^\s*(less than|under|within) \d", o, re.I):
                    return o
            return NeedsHuman("no 'none' option for the notice period")
        return "None. I am a full-time student with no current employer."
    if re.search(r"current (company|employer|organi[sz]ation)", label) and is_text:
        return profile.current_organization
    # "What is your current or previous job title?" (Stripe, 2026-10-06) got the student line; the title of his most
    # recent role is what is asked. An "if yes, give company name, job title, deadline" follow-up is not this question.
    if re.search(r"(current|previous|most recent|recent|latest)( or (current|previous|most recent))? (job |position )?title|"
                 r"job title|title of your (most recent|current|previous|latest)", label) and is_text and not FOLLOW_UP.search(label):
        work = [x for x in profile.experiences if x.kind != "open_source"]
        if work:
            return max(work, key=lambda x: (x.end or "9999-99", x.start)).title
        return f"{s.degree_short} {s.major} student, {s.name}"
    # English proficiency (2026-10-13, Alex): he is a native speaker, so "Native" beats "Fluent"; without a native
    # option, the highest level offered.
    if is_choice and q.options and re.search(r"english", label) and re.search(r"proficien|fluen|level|comprehension|abilit|skill", label):
        ranked = [r"native|mother ?tongue|first language|bilingual", r"fluent|full professional|expert|excellent",
                  r"advanced|professional|proficient|high", r"intermediate|good"]
        for pat in ranked:
            hit = [o for o in q.options if re.search(pat, o, re.I) and not re.search(r"\b(not|non|limited|elementary)\b", o, re.I)]
            if hit:
                return hit[0]
    if re.search(r"language", label) and q.type == "multiselect" and not re.search(r"programming|coding|software|technical", label):
        picked = [match_option(q.options, lang) for lang in d.languages]
        picked = [x for x in picked if x]
        if not picked and re.search(r"other|additional|besides|in addition", label):
            picked = [o for o in q.options if re.search(r"^\s*none\b", o, re.I)][:1]
        return picked or NeedsHuman("no language option matches the profile")
    if re.search(r"self[- ]identif|voluntary", label) and re.search(r"disab|veteran|gender|race|ethnic|hispanic", label):
        return _eeo_by_label(label, profile)
    # "Do you identify as LGBTQ+?" (DraftKings, 2026-10-14): "No" only when the profile says heterosexual and not
    # transgender; anything else falls through to the EEO decline rule below
    if is_choice and re.search(r"lgbt", label) and not re.search(r"gender|race|ethnic|veteran|disab", label) \
            and re.search(r"heterosexual|straight", profile.eeo.sexual_orientation, re.I) and profile.eeo.transgender == "No":
        no = [o for o in q.options if re.search(r"^\s*no\b|\bnot\b|\bdon'?t\b", o, re.I)
              and not re.search(r"prefer|disclose|answer|declin|wish", o, re.I)]
        if len(no) == 1:
            return no[0]
    if is_choice and re.search(r"gender|race|ethnic|veteran|disab|lgbtq|sexual orientation|transgender", label) \
            and (dec := _decline_option(q.options)):
        v = _eeo_by_label(label, profile)
        return match_option(q.options, v) or dec
    at = profile.attestations
    yes_no = q.type == "select" and match_option(q.options, "Yes") and match_option(q.options, "No")
    # a sponsorship question can mention "your spouse or partner's visa" (Salesforce): it is never a relatives question;
    # nor is an export-control one whose fine print says "family based immigration" and "U.S. Government" (Intel,
    # 2026-10-13: answered "No" to "are you a U.S. citizen or lawful permanent resident")
    if yes_no and not re.search(r"sponsor|visa|work authori[sz]ation|citizen|permanent resident|export.?control|immigration", label) and re.search(r"(family|relative|friend|spouse|household).*(employ|work|official|government|connection|relationship)|"
                            r"(employ|work).*(family|relative|friend|spouse)", label):
        if re.search(r"government|official|public|political", label):
            return at.relatives_in_government or NeedsHuman("attestations.relatives_in_government not set in profile.yaml")
        fam = at.relatives_at_employer
        if _family_works_there(at, label, job):
            return NeedsHuman("you have family at this company: the relatives question is yours")
        if re.search(r"friend|personal relationship|individuals?|acquaint|someone", label):
            # family AND friends asked together: "No" needs both facts (Alex stated family only, 2026-10-01)
            fr = at.friends_at_employer
            if fam and fr:
                return "Yes" if "yes" in (fam.lower(), fr.lower()) else "No"
            missing = "relatives_at_employer" if not fam else "friends_at_employer"
            return NeedsHuman(f"asks about friends too; attestations.{missing} not set in profile.yaml")
        return fam or NeedsHuman("attestations.relatives_at_employer not set in profile.yaml")
    # "a family member of a partner at Ernst & Young" (Intel, 2026-10-13): the relatives fact; Alex has no family at
    # any relevant company except Initech (`relatives_employers`)
    if yes_no and re.search(r"(family member|relative)[^?]{0,80}\b(partner|officer|director|executive|employee)s? (at|of|with)\b", label):
        if _family_works_there(at, label, job):
            return NeedsHuman("you have family at this company: the relatives question is yours")
        return at.relatives_at_employer or NeedsHuman("attestations.relatives_at_employer not set in profile.yaml")
    # "bound by any confidentiality, non-compete or IP agreement" is the agreements question, answered further down
    if yes_no and re.search(r"intellectual property|(own|control)[^?]{0,40}(patent|trademark|copyright)", label) \
            and not re.search(r"bound by|non-?compet|confidentiality|agreement", label):
        return at.owns_intellectual_property or NeedsHuman("attestations.owns_intellectual_property not set in profile.yaml")
    if yes_no and re.search(r"(attest|confirm|certify).{0,40}\bno\b.{0,40}(post-?government|government).{0,30}restriction", label) \
            and at.government_employment_5y and at.government_employment_5y.lower() == "no":
        return "Yes"
    # "I have United States government or public institution employment experience" (HPE, 2026-10-14)
    if yes_no and re.search(r"government[^?.]{0,40}employment (experience|history)|government (employ|official|position|responsib|service)|elected|appointed official|public (official|international)|"
                            r"cio/deputy cio|contracting/procurement|officer or cabinet|(employed|worked) by (the )?(u\.?s\.? )?government", label):
        return at.government_employment_5y or NeedsHuman("attestations.government_employment_5y not set in profile.yaml")
    if yes_no and re.search(r"debarr|suspended|excluded from|ineligib", label):
        return at.debarred or NeedsHuman("attestations.debarred not set in profile.yaml")
    if yes_no and re.search(r"convict|criminal|pretrial|felony|misdemeanor|diversion", label):
        return at.criminal_conviction or NeedsHuman("attestations.criminal_conviction not set in profile.yaml")
    if yes_no and re.search(r"non-?compete|non-?disclosure|confidentiality|non-?solicit|post-government employment restriction", label):
        return at.non_compete_bound or NeedsHuman("attestations.non_compete_bound not set in profile.yaml")
    if yes_no and re.search(r"conflict of interest|financial (interest|relationship)|personal, financial|business relationship", label):
        return at.conflict_of_interest or NeedsHuman("attestations.conflict_of_interest not set in profile.yaml")
    if yes_no and re.search(r"(other|outside|second) (business|employment|job)|engaged or involved in any other", label):
        return at.other_employment or NeedsHuman("attestations.other_employment not set in profile.yaml")
    if yes_no and re.search(r"are you (an? )?(existing|current)\b[^?]{0,40}\b(employee|staff member|worker)", label):
        return "Yes" if d.previous_employee_default else "No"
    if is_choice and re.search(r"advertising partner|marketing|newsletter|promotional|communications about|job alert|talent (community|network)|"
                               r"contact me about", label):
        return match_option(q.options, d.marketing_consent) or _statement_option(q.options, d.marketing_consent)
    if is_choice and re.search(r"(consider|contact)(ed)? (me |you )?for (other|future|additional)|future (recruitment|openings|opportunities|positions|roles)|"
                               r"other (positions|roles|opportunities)|share (my|your) (profile|information|application) with", label) \
            and not re.search(r"advertis|marketing", label):
        return match_option(q.options, d.future_consideration) or _statement_option(q.options, d.future_consideration)
    if q.type == "multiselect" and re.search(r"type of employment|employment type|work (type|schedule) (desired|preference)", label):
        picked = [o for o in q.options if re.search(r"intern|summer|co-?op", o, re.I)]
        return picked or NeedsHuman("no internship employment type")

    # ---- how did you hear
    if re.search(r"how (did |do )?you (first )?(hear|heard|find|found|learn)|hear(d)? about|referral source|^source( of (referral|application))?\s*:?$|"
                 r"influenced your decision to apply|what prompted you to apply", label):
        if is_choice:
            best = best_how_heard(q.options)
            if best:
                return best
            for cand in how_heard_candidates(job or {}):
                picked = match_option(q.options, cand)
                if picked:
                    return picked
            return NeedsHuman("no 'how did you hear' option matches the profile")
        # free text: never "Job Board" by default (2026-10-13, AQR); an event would be a claim, the careers site is not
        return "Company careers website"

    # ---- work authorization (profile facts, never guesses)
    # "Do you require work authorization?" (Waymo, 2026-10-06) asks whether he needs one, not whether he has one:
    # the authorization rule below answered "Yes" because he is authorized.
    if re.search(r"\b(require|need)s?\b[^?]{0,20}\b(work authori[sz]ation|work permit|a visa)", label) and not re.search(r"sponsor", label):
        return "Yes" if wa.sponsorship_required else "No"
    if re.search(r"authori[sz]ed to work|legal(ly)? work authori[sz]ation|work authori[sz]ation|authori[sz]ation to work|"
                 r"legally (able|eligible|permitted) to work|eligible to work|right to work|legal authori[sz]ation", label) \
            and not re.search(r"sponsor", label):
        if is_choice and q.options and match_option(q.options, "Yes") is None:
            # statement-style options: "I am legally authorized to work for any employer in the United States."
            for o in q.options:
                on = o.lower()
                if wa.authorized_us and re.search(r"authori[sz]ed", on) and not re.search(r"\bnot\b|require|sponsor", on):
                    return o
                if not wa.authorized_us and re.search(r"\bnot\b.*authori[sz]ed", on):
                    return o
            return NeedsHuman("work authorization options do not map to the profile")
        return "Yes" if wa.authorized_us else "No"
    if re.search(r"sponsor|immigration (case|petition|filing|process|support)|(file|commence|start) an? (immigration|visa)|"
                 r"h-?1b|employment[- ]based visa", label):
        if is_choice and q.options and not wa.sponsorship_required and match_option(q.options, "No") is None:
            na = [o for o in q.options if re.search(r"not applicable|^\s*n/?a\s*$|do(es)? not (require|need)", o, re.I)]
            if len(na) == 1:
                return na[0]
        return "Yes" if wa.sponsorship_required else "No"
    if re.search(r"(hold|have|on) an? (work |student )?visa|visa (holder|status)|currently hold a visa", label) and is_choice \
            and match_option(q.options, "No") and (wa.citizen or re.search(r"permanent resident|green card", wa.status, re.I)):
        return "No"
    # military service: answered from the veteran self-identification Alex stated (2026-10-01: "military" once
    # matched the ITAR rule because it contains the letters "itar", and the form got "Yes")
    if re.search(r"\bmilitary\b|armed forces|served in the|\bveteran\b", label) and not re.search(r"spouse", label) and is_choice             and match_option(q.options, "Yes") and match_option(q.options, "No")             and re.match(r"\s*(no\b|i am not|i'm not)", profile.eeo.veteran or "", re.I):
        return "No"
    embargo = re.search(r"iran|cuba|syria|north korea|embargo|sanctioned countr", label)
    if re.search(r"citizen|export.?control|\bitar\b|u\.?s\.? person", label) and is_choice:
        # Embargoed-country questions (Salesforce, 2026-10-01) depend on his citizenship, which the profile does not
        # state. Only the "a country that is NOT Iran, Cuba, ..." wording is answered, by his U.S. permanent
        # residence. The plain "are you a citizen of Iran, Cuba, ..." question is his; the generic export-control
        # "Yes" below must never fire on it (it once did, because the label also says "export control laws").
        if embargo:
            if re.search(r"\b(that is|which is) not\b[^?]{0,40}(iran|cuba|syria|north korea)", label) \
                    and "permanent resident" in wa.status.lower() and match_option(q.options, "Yes"):
                return "Yes"
            # "are you a citizen or permanent resident of any of: <list>" (HP, 2026-10-04): "No" when the list is in
            # the label and none of his citizenships is on it (he is a U.S. permanent resident and a citizen of Canada,
            # stated 2026-10-05). A "confirm you are not ..." wording inverts the answer, so it stays his.
            if wa.citizenship and len({m.lower() for m in EMBARGO_NAMES.findall(label)}) >= 2 \
                    and not any(re.search(rf"\b{re.escape(c.lower())}\b", label) for c in wa.citizenship) \
                    and not re.search(r"(confirm|certify|attest|verify)[^.?]{0,30}\bnot\b", label) \
                    and re.search(r"citizen|national", label) and match_option(q.options, "No"):
                return "No"
            return NeedsHuman("embargoed-country question: depends on your citizenship")
        for o in q.options:
            if re.search(r"permanent resident|green card|lawfully admitted for permanent residence", o, re.I):
                if "permanent resident" in wa.status.lower():
                    return o
        if re.search(r"export.?control|\bitar\b|u\.?s\.? person", label) and wa.itar_us_person:
            if match_option(q.options, "Yes") and not any(re.search(r"citizen", o, re.I) for o in q.options):
                return "Yes"
        return NeedsHuman("citizenship/export-control option must be chosen by Alex")
    if embargo:
        return NeedsHuman("embargoed-country question: depends on your citizenship")
    if re.search(r"export.?control|\bitar\b|u\.?s\.? person", label):
        return "Yes" if wa.itar_us_person else None
    if re.search(r"security clearance|active clearance|clearance\(s\)", label):
        if is_choice:
            for o in q.options:
                if re.match(r"\s*(none|no clearance|not applicable|n/?a|i do not|no active)", o, re.I):
                    return [o] if q.type == "multiselect" else o
            if match_option(q.options, "No") and q.type == "select":
                return "No"
            return NeedsHuman("no 'none' option for clearance")
        return "No active clearance"
    if re.search(r"background check", label):
        return d.background_check
    if re.search(r"\b(18|eighteen)\b", label):
        # "Are you younger/under 18?" flips the answer (GE HealthCare, 2026-10-15, answered Yes)
        # ...but "Are you 18 or older? If you are under 18, ..." (Stryker) is the plain question
        over_q = re.search(r"(18|eighteen) (years )?(of age )?(or|and) (older|over|above)|at least (18|eighteen)|"
                           r"over (the age of )?(18|eighteen)", label)
        under = not over_q and re.search(r"(younger|under|less) than (the age of )?(18|eighteen)|"
                                         r"\bunder (the age of )?(18|eighteen)\b|\bminor\b", label)
        return ("No" if under else "Yes") if d.over_18 else ("Yes" if under else "No")
    # yes/no questions only: an essay box like "Please share your previous internship ... experience" (Xcimer) also
    # says "previous ... intern" and must never receive "No"
    if is_choice and match_option(q.options, "Yes") and match_option(q.options, "No") and re.search(
                 r"previously (worked|employed|been)|former (employee|intern)|currently employed|(worked|employed) (at|for|with) .* before|"
                 r"previous .{0,40}(employee|intern|contractor)|current(ly)? (an? |a current )?(employee|intern|contractor)( of| at| with)?|"
                 r"ever been (employed|a contractor)|been employed by", label):
        return "No" if not d.previous_employee_default else "Yes"
    if re.search(r"previously (interviewed|applied)|applied (to|with) .* before|interviewed .* before", label):
        return d.previously_interviewed
    if re.search(r"text message|sms|texting|receive text", label):
        # Walmart phrases the choices as "Opt-In to receive text messages" / "Opt-Out from receiving ..."
        yes = str(d.text_consent).strip().lower() in ("yes", "true", "y", "opt-in", "opt in")
        for o in q.options:
            if re.search(r"opt[- ]?in" if yes else r"opt[- ]?out", o, re.I):
                return [o] if q.type == "multiselect" else o
        return d.text_consent
    if re.search(r"met with .{0,40}(representative|recruiter)|(attend|met).{0,40}(recruiting|career|campus) event", label) and is_choice:
        return d.met_recruiter_event
    if q.type == "multiselect" and q.options and re.search(
            r"relocat|which (office|location|cit)|locations? (you|would|are you)|interested in (working|relocating)|"
            r"willing to work (in|at|from)|office locations?", label):
        if str(d.relocation).lower().startswith("y"):
            us = rank_locations(q.options, d.relocation_cities)
            return us or NeedsHuman("no U.S. location among the options")
        return NeedsHuman("relocation is not Yes in profile.yaml")
    # "First / Second / Third location preference" (Stripe, 2026-10-06: all three got New York): the nth choice
    # takes the nth city in his order
    if q.type == "select" and q.options and d.relocation_cities and re.search(r"prefer|first choice|top choice|rank", label) \
            and re.search(r"location|office|cit(y|ies)|site", label):
        ranked = rank_locations(q.options, d.relocation_cities)
        nth = 2 if re.search(r"\bthird\b|\b3rd\b", label) else 1 if re.search(r"\bsecond\b|\b2nd\b", label) else 0
        if len(ranked) > nth:
            return ranked[nth]
    # "Will you require housing if hired?" (Alex, 2026-10-16): always No on the form; he raises housing himself if he
    # gets an interview and it is offered. A Yes reads as a cost before anyone has met him.
    if is_choice and re.search(r"(require|need|requesting|interested in)\b.{0,40}\bhousing|housing (assistance|support|stipend)", label):
        no = match_option(q.options, "No")
        if no:
            return no
    # "willing to relocate" is his; "do you require relocation assistance / housing" is not (S&P, 2026-10-15: Yes)
    if re.search(r"relocat", label) and is_choice and not re.search(r"assist|package|stipend|reimburs|housing|support",
                                                                    label):
        return d.relocation
    # "Are you able to live in the country where this role is located for the full duration?" (Adobe, 2026-10-13): he
    # lives in the U.S. and is authorized to work there, so Yes for a U.S. posting; any other country stays his
    if re.search(r"(able|willing|eligible) to (live|reside|stay|be (located|based)) in the country", label) and is_choice \
            and wa.authorized_us and _job_locations(job or {}) and all(_us_location(x) for x in _job_locations(job or {})) \
            and match_option(q.options, "Yes"):
        return "Yes"
    # "Do you possess a valid work permit / residency permit in the country for which the position resides?" (HPE,
    # 2026-10-14): his green card is both, for a U.S. posting; any other country stays his
    if re.search(r"valid (work|residen\w*) permit", label) and is_choice and wa.authorized_us \
            and _job_locations(job or {}) and all(_us_location(x) for x in _job_locations(job or {})) \
            and match_option(q.options, "Yes"):
        return "Yes"
    # "Do you prefer a winter or summer internship?" (Verkada, 2026-10-14): he is applying for summer internships
    if re.search(r"(winter|fall|spring) or summer|summer or (winter|fall|spring)", label) and is_choice:
        summer = [o for o in q.options if re.search(r"summer", o, re.I)]
        if len(summer) == 1:
            return summer[0]
    logistics =_logistics(q, label, profile, job or {}, is_choice, is_text)
    if logistics is not None:
        return logistics

    # ---- school
    if re.search(r"\b(sat|act)\b", label) and re.search(r"score|composite|test|result", label):
        sat, act = re.search(r"\bsat\b", label), re.search(r"\bact\b", label)
        val = s.sat if sat else s.act          # both asked together: the SAT is the one he took
        if not val:
            return NeedsHuman(f"{'sat' if sat else 'act'} not set in profile.yaml")
        if is_choice:
            return _score_option(q.options, val) or NeedsHuman(f"no option fits {val!r}")
        took = re.fullmatch(r"\d+", val.strip())
        if sat and act and took:
            return f"SAT: {val}"
        return val if took else "Did not take"
    if re.search(r"\bgpa\b", label):
        if is_choice and match_option(q.options, "Yes") and match_option(q.options, "No"):
            m = re.search(r"(\d\.\d+)", label)
            if m:
                return "Yes" if float(s.gpa) >= float(m.group(1)) else "No"
            return NeedsHuman("GPA yes/no question without a stated threshold")
        if is_choice:
            if re.search(r"\bgraduate\b|master|phd|doctor", label) and not re.search(r"undergrad", label):
                for o in q.options:
                    if re.match(r"\s*(not applicable|n/?a|none)", o, re.I):
                        return o
                return NeedsHuman("graduate GPA asked; no N/A option")
            return gpa_option(q.options, float(s.gpa)) or NeedsHuman(f"no GPA option contains {s.gpa}")
        return s.gpa
    # "What year in college are you?" (Intel, 2026-10-13): the class standing that follows from the graduation date
    if re.search(r"(what|current) (year|class standing|academic (year|standing))[^?]{0,30}(college|school|university|study|program)|"
                 r"year of study|class standing|academic standing", label) and is_choice and s.graduation:
        today = date.today()
        left = int(s.graduation[:4]) - (today.year + 1 if today.month >= 8 else today.year)
        names = {0: r"senior|fourth|4th", 1: r"junior|third|3rd", 2: r"sophomore|second|2nd", 3: r"freshman|first|1st"}
        if left in names:
            hit = [o for o in q.options if re.search(rf"^\s*({names[left]})\b", o, re.I)
                   and not re.search(r"master|continuing|graduate|doctor", o, re.I)]
            if hit:
                return hit[0]
    if re.search(r"graduat", label) and q.type == "date":
        return s.graduation
    if re.search(r"active student|currently (an? )?(active |full[- ]time )?student|actively enrolled|enrolled (full|part)[- ]time", label) and is_choice:
        return "Yes"
    if re.search(r"high school (diploma|degree)|\bged\b", label) and is_choice:
        return "Yes" if s.high_school_completed else "No"
    if re.search(r"school location|location of (your|the) (school|university|college)|(school|university|college) (city|location)", label) and is_text:
        return f"{s.city}, {s.state}"
    if re.search(r"graduat", label) and is_choice and not re.search(r"postgraduate|intend(?!ed (graduation|to graduate))|plan to|after graduat", label) \
            and not (match_option(q.options, "Yes") and match_option(q.options, "No")):
        if re.search(r"master|graduate degree|phd", label) and not re.search(r"bachelor|undergrad", label):
            for o in q.options:
                if re.match(r"\s*(not applicable|n/?a|none)", o, re.I):
                    return o
            return NeedsHuman("graduate-degree graduation date asked")
        return grad_option(q.options, s.graduation) or NeedsHuman(f"no graduation option matches {s.graduation}")
    if re.search(r"graduat", label) and is_text:
        # a stated format wins (Adobe, 2026-10-13: "please enter in this format MM/YYYY" got "May 2028")
        if re.search(r"mm\s*/\s*yyyy", label):
            return f"{s.graduation[5:7]}/{s.graduation[:4]}"
        if re.search(r"mm\s*/\s*dd\s*/\s*yyyy", label):
            return f"{s.graduation[5:7]}/01/{s.graduation[:4]}"
        return f"{MONTHS[int(s.graduation[5:7]) - 1]} {s.graduation[:4]}"
    # A question whose premise is a master's/PhD program he is not in ("If you are currently completing your
    # master's degree, did you have a full time position between ...?") is answered No (N/A when offered), never by
    # the "currently completing a degree" rule below (Capstone, 2026-10-10: it answered Yes; Alex: "should be no").
    premise = re.split(r"\bdid you\b|\?", label, maxsplit=1)[0]
    if is_choice and GRAD_PREMISE.search(premise) and not re.search(r"bachelor|undergrad", premise, re.I) \
            and not re.search(r"master|ph\.?d|doctor", s.degree, re.I):
        for o in q.options:
            if re.match(r"\s*(not applicable|n/?a)\b", o, re.I):
                return o
        no = match_option(q.options, "No")
        if no:
            return no
    if re.search(r"currently (in|enrolled in|attending) (school|college|university|a)|currently a student|currently enrolled|"
                 r"enrolled in a|are you (a )?student|full[- ]time student", label) and is_choice:
        return "Yes"
    if SCHOOL_ASK.search(label) and is_choice:
        picked = match_option(q.options, s.name)
        if picked:
            return picked
        other = _other_option(q.options)
        return other or NeedsHuman(f"{s.name} is not in the school list")
    if SCHOOL_ASK.search(label) and is_text:
        return s.name
    if re.search(r"(in (the )?process of|currently) (completing|pursuing|earning|working toward|obtaining)|"
                 r"currently (a )?(candidate|enrolled)", label) and is_choice and match_option(q.options, "Yes"):
        return "Yes"
    if re.search(r"degree|level of education|highest (level of )?education", label) and is_choice \
            and not (match_option(q.options, "Yes") and match_option(q.options, "No")):
        return match_option(q.options, s.degree_select) or match_option(q.options, "Bachelor") \
            or NeedsHuman("no degree option matches")
    # "What kind of opportunities are you looking for? Describe your area of interest ... or degree focus area(s)"
    # (Intel, 2026-10-13) asks what he wants, not his degree: the writer answers it from the resume
    if re.search(r"degree", label) and is_text             and not re.search(r"opportunit|interest|looking for|type of (position|role)|describe", label) \
            and not ESSAY_TAIL.search(label):
        return f"{s.degree} in {s.major}"
    # \bmajor\b: "majority owned by Tencent" once matched (Riot, 2026-10-10)
    # "What is your current major, and what inspired you to choose this field of study?" (Khan Academy, 2026-10-14)
    # got just "Computer Science": a question that also asks why is the writer's
    if re.search(r"\bmajor\b|field of study|discipline|area of study", label) and not re.search(r"experience|years|demonstrated", label) \
            and not (is_text and ESSAY_TAIL.search(label)):
        if is_choice:
            picked = match_option(q.options, s.major)
            return ([picked] if q.type == "multiselect" else picked) if picked else NeedsHuman("major not in list")
        return s.major
    if re.search(r"how many .*(internship|co-?op)", label) and is_choice:
        n = sum(1 for x in profile.experiences if x.kind == "internship")
        return match_option(q.options, str(n)) or NeedsHuman("internship count option missing")

    # ---- residence
    if re.search(r"country.*(reside|live|located|residence|currently in)|(reside|live) in.*country|country of residence", label) and is_choice:
        c = re.sub(r"\s+of America$", "", profile.address.country, flags=re.I)
        return match_option(q.options, c) or match_option(q.options, profile.address.country) or NeedsHuman("country not in list")
    if re.search(r"(us|u\.s\.|which) state|state (do you|of residence|you reside)", label) and is_choice:
        if not profile.address.state_name:
            return NeedsHuman("address not set")
        return match_option(q.options, profile.address.state_name) or match_option(q.options, profile.address.state) \
            or NeedsHuman("state not in list")

    # ---- internship season / cohort (Stripe, 2026-10-06: "Winter (January - April)" / "Summer (May - September)").
    # His stated availability is the summer; a winter term would be a semester out of school, which the profile
    # never offers, so the second-cohort question takes its "not pursuing another" option.
    if is_choice and q.options and re.search(r"cohort|session|which (term|season)", label) and not _is_coop(job or {}) \
            and re.search(r"summer|\b(may|june|july|august)\b", d.availability or "", re.I):
        if re.search(r"second|another|additional|other cohort|flexib", label):
            hit = [o for o in q.options if re.search(r"\bnot\b|^no\b|none", o, re.I)]
        else:
            hit = [o for o in q.options if re.search(r"summer", o, re.I)]
        if len(hit) == 1:
            return hit if q.type == "multiselect" else hit[0]
    # "Are you able to start between January 2027 and September 2027?" (Pinterest, 2026-10-06): Yes when his summer
    # start falls inside the stated window.
    win = re.findall(r"([a-z]{3,9})\.?\s+(\d{4})", label)
    if is_choice and d.summer_start and len(win) >= 2 and re.search(r"start", label) and re.search(r"between|from", label) \
            and match_option(q.options, "Yes") and match_option(q.options, "No"):
        yms = [(int(y), _MONTH_IDX[m[:3]]) for m, y in win if m[:3] in _MONTH_IDX]
        if len(yms) >= 2:
            start = (int(d.summer_start[:4]), int(d.summer_start[5:7]))
            return match_option(q.options, "Yes" if yms[0] <= start <= yms[1] else "No")

    # ---- availability wording (the profile sentence; the dates are Alex's, not Claude's)
    if re.search(r"available to start|start date|availability|when (can|could|would) you (start|be able to start)|earliest (date|start)|"
                 r"able to start", label):
        coop = _is_coop(job or {})
        if is_choice:
            if d.summer_start:
                picked = _earliest_option(q.options, None if coop else d.summer_start[:7])
                if picked:
                    return picked
            ym = _ym_from_text(d.start_date_text)
            picked = grad_option(q.options, ym) if ym else None
            return picked or NeedsHuman("start-date option must be chosen by Alex")
        if q.type == "date" and d.summer_start and not coop:
            return d.summer_start
        if is_text:
            if d.summer_start and "availab" not in label:
                return ("Flexible: available from the earliest start date in the posting" if coop
                        else _long_date(d.summer_start))
            return d.availability if q.type == "textarea" or "availab" in label else d.start_date_text

    # ---- consents
    if re.search(r"agree|certify|acknowledge|consent|confirm that|\bi confirm\b|true and (correct|accurate)|terms|privacy", label) and is_choice:
        for o in q.options:
            if re.match(r"\s*(yes|i (agree|accept|certify|acknowledge|consent|have read|understand))", o, re.I):
                return o
        return None
    return None


# "How did you hear": never a referral or a relationship (2026-10-11, Alex: not just "Job board" or "Other"; take the
# best listed option that is not a referral, like an event). Ranked best first.
# named organizations and programs are claims of membership (Adobe, 2026-10-13: "Thurgood Marshall College Fund (TMCF)"
# ranked as a campus option because of "College"); so are affinity groups and named diversity programs
_HEARD_NEVER = re.compile(r"referr|employee|recruiter|agency|staffing|contractor|headhunt|friend|family|former|alumni of|"
                          r"current(ly)? work|internal|boomerang|word of mouth|contacted by|ambassador|"
                          r"\bfund\b|societ|foundation|association|consortium|council|alliance|scholars?\b|fellows|corps\b|"
                          r"institute|\bnsbe\b|\bshpe\b|\bswe\b|\bmlt\b|\bgem\b|\btmcf\b|black|hispanic|latin|women|veteran|"
                          r"lgbt|disabilit|minorit|divers|wall street bound|\b\d[\d,]* \w+ interns\b|\([A-Z]{2,}\)", re.I)
_HEARD_RANK = [re.compile(x, re.I) for x in (
    r"career (fair|expo|day)|job fair|recruiting event|campus|universit|college|school|\bevents?\b|conference|hackathon|"
    r"info(rmation)? session|meetup|webinar|workshop|symposium|competition",
    r"\bcareers?\b|company (web)?site|corporate (web)?site|our (web)?site|\bwebsite\b|web site",
    r"linkedin",
    r"handshake",
    r"job board|online|internet|indeed|glassdoor|simplify|github|job (posting|search)|ziprecruiter|wellfound|built ?in",
    r"social|twitter|facebook|instagram|youtube|news|article|blog|podcast",
)]


def best_how_heard(options: list[str]) -> str | None:
    """The strongest listed "how did you hear" option that is not a referral: events and campus first, then the
    company's own careers site, LinkedIn, Handshake, job boards, social media. None when nothing ranks
    (the caller then falls back to how_heard_candidates, which ends in "Other")."""
    opts = [o for o in options if o and o.strip() and not _HEARD_NEVER.search(o)
            and not re.match(r"^\s*(select|choose|please|--|none\b)", o, re.I)]
    ranked = sorted((r, i, o) for i, o in enumerate(opts) if (r := heard_rank(o)) is not None)
    return ranked[0][2] if ranked else None


def heard_rank(option: str) -> int | None:
    """0 = event/campus (best) ... 5 = social media; None for a referral or an option that does not rank."""
    if not option or _HEARD_NEVER.search(option):
        return None
    return next((i for i, rx in enumerate(_HEARD_RANK) if rx.search(option)), None)


def how_heard_candidates(job: dict) -> list[str]:
    """Where the pipeline really found the job, as the options forms use, never a referral. Jobs from the
    Simplify / SpeedyApply internship lists came from an online job board; `watch:<Company>` jobs from the company's
    own careers page (2026-10-01: the old default "LinkedIn" was not true for any of them)."""
    import json as _json
    raw = job.get("sources") or "[]"
    try:
        sources = _json.loads(raw) if isinstance(raw, str) else list(raw)
    except ValueError:
        sources = []
    board = ["Job Board", "Job Board - Other", "Online Job Board", "Internet", "Online", "Job Posting", "Internet Job Board",
             "Website"]
    site = ["Company Website", "Company Career Site", "Career Site", "Careers Page", "Company Careers Page",
            "Corporate Website", "Website"]
    if any(str(s).startswith("watch:") for s in sources) and not any(s in ("simplify", "speedyapply") for s in sources):
        return site + board + ["Other"]
    # a list job did not come from the company's site: "Other" is truer than "Career Site" (Affirm, 2026-10-06)
    return board + ["Other"] + site


_MONTH_IDX = {m.lower()[:3]: i + 1 for i, m in enumerate(MONTHS)}
_SEASON_MONTH = {"spring": 1, "summer": 5, "fall": 8, "autumn": 8, "winter": 12}


def _option_ym(option: str) -> tuple[int, int] | None:
    """'May 2027', 'May 1, 2027', '05/2027', '2027-05', 'Summer 2027' -> (2027, 5)."""
    o = option.strip()
    m = re.search(r"\b(\d{4})-(\d{1,2})\b", o) or re.search(r"\b(\d{1,2})/(?:\d{1,2}/)?(\d{4})\b", o)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return (a, b) if a > 1900 else (b, a)
    m = re.search(r"([A-Za-z]{3,9})\.?\s+(?:\d{1,2},?\s+)?(\d{4})", o)
    if m:
        word = m.group(1).lower()
        if word[:3] in _MONTH_IDX:
            return int(m.group(2)), _MONTH_IDX[word[:3]]
        if word in _SEASON_MONTH:
            return int(m.group(2)), _SEASON_MONTH[word]
    return None


def _earliest_option(options: list[str], not_before: str | None) -> str | None:
    """The earliest dated option on or after YYYY-MM (or the earliest of all when None)."""
    dated = [(ym, o) for o in options if (ym := _option_ym(o))]
    if not_before:
        lo = (int(not_before[:4]), int(not_before[5:7]))
        dated = [(ym, o) for ym, o in dated if ym >= lo]
    return min(dated)[1] if dated else None


def _family_works_there(at, label: str, job: dict | None) -> bool:
    text = f"{(job or {}).get('company') or ''} {label}"
    return any(re.search(rf"\b{re.escape(c)}\b", text, re.I) for c in at.relatives_employers)


def _long_date(iso: str) -> str:
    y, m, dd = iso[:4], int(iso[5:7]), int(iso[8:10] or 1)
    return f"{MONTHS[m - 1]} {dd}, {y}"


def _is_coop(job: dict) -> bool:
    text = " ".join(str(job.get(k) or "") for k in ("title", "terms"))
    return bool(re.search(r"co-?op|spring|fall|winter|semester|year at", text, re.I)) and not re.search(r"summer", text, re.I)


def _job_locations(job: dict) -> list[str]:
    raw = job.get("locations") or "[]"
    try:
        import json as _json
        locs = _json.loads(raw) if isinstance(raw, str) else list(raw)
    except ValueError:
        locs = [str(raw)]
    return [str(x) for x in locs]


def _yes_no(q: Question, value: str) -> Any:
    hit = match_option(q.options, value) if q.options else None
    return hit or (value if not q.options else None)


def _logistics(q: Question, label: str, profile: Profile, job: dict, is_choice: bool, is_text: bool) -> Any:
    """Driver's licence, essential functions, travel, on-site, commute, pay, salary, full-time availability and
    offer deadlines, answered from what Alex stated once (profile.defaults). None = not one of these."""
    d, s = profile.defaults, profile.school
    yes_no = is_choice and match_option(q.options, "Yes") and match_option(q.options, "No")
    if re.search(r"driver'?s? licen[cs]e|driving licen[cs]e", label) and d.driver_license:
        return _yes_no(q, d.driver_license) if is_choice else d.driver_license
    if re.search(r"with or without (a )?reasonable accommodation|essential (functions|duties)|perform the (activities|duties|functions)", label) \
            and d.essential_functions and (yes_no or is_text):
        return _yes_no(q, d.essential_functions) if is_choice else d.essential_functions
    if re.search(r"\btravel", label) and d.travel:
        if yes_no:
            return _yes_no(q, d.travel)
        if is_choice and re.search(r"how much|amount|percent|%|what (amount|portion)", label):
            pct = [(int(m.group(1)), o) for o in q.options if (m := re.search(r"(\d{1,3})\s*%", o))]
            return max(pct)[1] if pct else None
        if is_text and re.search(r"where|location|destination", label):
            return "Anywhere"
        if is_text and re.search(r"how much|amount|percent", label):
            return "Any amount"
        if is_choice:
            return _yes_no(q, d.travel)
    # "Are you currently based in this location?" with statement options (Harvey, 2026-10-05: "Yes, I'm based in this
    # location and able to work from the office" was picked by the on-site rule, a false claim of living in SF).
    # Based = the posting's city is his commute city; otherwise the "willing to relocate" statement when relocation is Yes.
    if is_choice and q.options and re.search(r"(currently )?based in|(currently )?(live|reside|located) in (this|the|that)", label):
        city = (d.commute_city or profile.address.city or "").lower()
        near = bool(city) and (city in label or city in " ".join(_job_locations(job)).lower())
        if near:
            hit = [o for o in q.options if re.match(r"\s*yes", o, re.I)]
        elif str(d.relocation).lower().startswith("y"):
            hit = [o for o in q.options if re.search(r"relocat", o, re.I) and not re.search(r"not (willing|able|open)", o, re.I)]
        else:
            hit = []
        return hit[0] if len(hit) == 1 else NeedsHuman("based-in-location question: options do not map to the profile")
    if is_text and re.search(r"(city|town),? (and|&|/) state", label) and re.search(r"reside|live|located|based|current", label) \
            and profile.address.city and profile.address.state:
        return f"{profile.address.city}, {profile.address.state}"
    if yes_no and re.search(r"(located|based|live|reside|residing) in (the )?(us|u\.s\.?|usa|united states|america)\b", label):
        return "Yes" if re.search(r"united states|^us$|^usa$", profile.address.country, re.I) else "No"
    # "Are you able to commute to this office?" (Alex, 2026-10-16): Yes. He relocates for the internship, so he can
    # commute to wherever it is; a No here was a needless knock-out. Questions about where he lives NOW stay below.
    if yes_no and re.search(r"(able|willing) to (reliably )?commut|commute (to|into)|can you commute", label) \
            and not re.search(r"currently (live|reside)|do you (currently )?(live|reside)", label):
        return _yes_no(q, "Yes")
    if re.search(r"commut|within (a )?(reasonable |commutable )?distance|live (in|near)|located (in|within|near)|reside (in|near)", label) \
            and yes_no and d.commute_city:
        city = d.commute_city.lower()
        if re.search(r"\b(in|at|of|near)\s+[A-Z][A-Za-z .'-]+,\s*[A-Z]{2}\b", q.label):
            near = city in label          # the question names the office: judge that place only
        else:
            near = city in label or city in " ".join(_job_locations(job)).lower()
        return "Yes" if near else "No"
    if re.search(r"on-?site|in[- ]office|in person|in-person|days (per|a) week|hybrid|headquarters|report to (the|our) office", label) \
            and yes_no and d.onsite:
        return _yes_no(q, d.onsite)
    if re.search(r"\bpays?\b|pay range|pay rate|hourly (range|rate)|salary range|compensation|per hour|/hour|comfortable with (the|this|that)|within your expectations", label) \
            and yes_no and d.pay_range_ok:
        return _yes_no(q, d.pay_range_ok)
    # "Provide your salary/hourly range expectation" (Intel, 2026-10-13) was missed; a scraped pay of "00-95,702"
    # is not an answer, only a value that reads as money is
    # Alex, 2026-10-13: a posted range is quoted back; without one, Claude names a figure at the low-to-middle of what
    # the company usually pays for the role (build_packet), and "Open / negotiable" is only the no-Claude fallback
    if SALARY_ASK.search(label) and is_text:
        pay = str(job.get("pay") or "").strip()
        if re.search(r"\$\s?\d", pay):
            low = re.search(r"\$\s?([\d,]+(?:\.\d+)?)", pay).group(1).replace(",", "")
            return low if q.type == "number" else f"In line with the posted range ({pay})"
    if re.search(r"full[- ]time|40 hours|hours per week|entire duration|consecutive weeks", label) and d.full_time_availability:
        if yes_no:
            return _yes_no(q, d.full_time_availability)
        if is_choice:
            weeks = [o for o in q.options if re.search(r"\b12\b", o)]
            return weeks[0] if weeks and not _is_coop(job) else None
    # HP, 2026-10-05: "What is your availability?" with Full-time / Part-time options (the label never says full time),
    # and "How many months are you available?" as text, both from the summer facts above
    if is_choice and re.search(r"availab", label) and str(d.full_time_availability).lower().startswith("y"):
        ft = [o for o in q.options if re.fullmatch(r"\s*full[- ]time\s*", o, re.I)]
        if ft:
            return ft[0]
    if is_text and not _is_coop(job) and re.search(r"how (many|long)\b.{0,20}\b(months|weeks)\b", label) \
            and re.search(r"availab|intern", label) and (m := re.search(r"(\d+)\s*weeks", d.availability)):
        w = int(m.group(1))
        return f"{round(w / 4.33)} months ({w} weeks), {re.sub(r'^Available ', '', d.availability.split(',')[0])}"
    further = re.search(r"further education|graduate school|another degree|additional degree|continue (your|my) (education|studies)", label) \
        and re.search(r"pursu|consider|plan|commit|intend", label)
    if further and is_choice and d.further_education:
        # statement options: "No" / "I am considering further education ..." (IMC, Five Rings, 2026-10-06)
        return match_option(q.options, d.further_education) or _statement_option(q.options, d.further_education)
    if yes_no and d.current_offers and re.search(r"\b(have|hold|holding|received)\b[^?]{0,20}\boffers?\b", label) \
            and not re.search(r"deadline", label):
        return _yes_no(q, d.current_offers)
    if re.search(r"offer deadline|competing offer|other offers? (with|that have) deadline", label) and d.offer_deadlines:
        return _yes_no(q, d.offer_deadlines) if is_choice else d.offer_deadlines
    if re.search(r"high school", label) and s.high_school_name:
        if re.search(r"name|which high school|attended", label) and is_text:
            return s.high_school_name
        if re.search(r"graduat|year", label) and s.high_school_graduation:
            yr = s.high_school_graduation[:4]
            return (match_option(q.options, yr) or grad_option(q.options, s.high_school_graduation)) if is_choice else yr
    return None


GRAD_PREMISE = re.compile(r"(if (you are|you're)\s+|are you\s+|in (the )?process of\s+|currently\s+)"
                          r"(currently\s+)?(completing|pursuing|enrolled in|earning|working toward|obtaining|in)\s+"
                          r"(a |an |your )?(master|graduate (degree|program|school)|ph\.?d|doctora)", re.I)


def deterministic_answers(questions: list[Question], profile: Profile, memory: dict[str, str], job: dict | None = None) -> dict[str, Answer]:
    out: dict[str, Answer] = {}
    prev_label = ""
    for q in questions:
        if q.type == "hidden":
            continue
        # Follow-ups that only make sense after the question before them (TEL, 2026-10-01: "Are you willing to
        # travel?" then "What amount of time?", "Where for short term?") are read as part of it.
        if re.search(r"travel", prev_label, re.I) and not re.search(r"travel", q.label, re.I) \
                and re.search(r"amount of time|how much|percent|where (for|to)|short[- ]term|long[- ]term", q.label, re.I):
            q = Question.from_dict({**q.to_dict(), "label": "Travel: " + q.label})
        else:
            prev_label = q.label
        v = _profile_value_for(q, profile, job)
        src = "profile"
        if isinstance(v, NeedsHuman) and q.label.strip().lower() in memory and not KNOCKOUT.search(q.label):
            v = None            # the profile has no answer, but Alex confirmed one on an earlier application
        if isinstance(v, NeedsHuman):
            if q.required:
                out[q.id] = Answer(q.id, None, "needs_human", needs_human=v.reason, confidence=0.0)
            else:
                out[q.id] = Answer(q.id, None, "skip", needs_human=None, confidence=0.0)
            continue
        if v is None and q.label.strip().lower() in memory:
            v, src = memory[q.label.strip().lower()], "memory"
        if v is None:
            if not q.required and OPTIONAL_SKIP.search(q.label):
                out[q.id] = Answer(q.id, None, "skip", needs_human=None, confidence=0.0)
            continue
        if q.type in ("select", "multiselect") and v != "__resume__" and q.options:
            vals = v if isinstance(v, list) else [v]
            picked = [match_option(q.options, x) for x in vals]
            if any(p is None for p in picked):
                if q.section in ("eeoc", "demographic"):
                    picked = [match_option(q.options, DECLINE)]
                if any(p is None for p in picked):
                    out[q.id] = Answer(q.id, None, "needs_human", needs_human=f"no option matches {v!r}", confidence=0.0)
                    continue
            v = picked if q.type == "multiselect" else picked[0]
        out[q.id] = Answer(q.id, v, src, confidence=1.0)
    # "If yes, please specify ..." right after a question answered No (IMC, Five Rings, 2026-10-06: the job-title and
    # degree rules filled them): left blank, or "N/A" when a text box is required. A required choice keeps its own
    # answer (Waymo's "If yes, what kind?" takes "Not applicable"). After an unanswered question it waits for Alex.
    prev = None
    for q in questions:
        if q.type == "hidden":
            continue
        if prev is not None and FOLLOW_UP.search(q.label):
            pa = out.get(prev.id)
            pv = pa.value if pa is not None else None
            pv = pv[0] if isinstance(pv, list) and pv else pv
            mine = q.id in out and out[q.id].value not in (None, "", [])
            if pv not in (None, "", []) and _norm(str(pv)).split(" ")[0] == "no":
                if q.type in ("text", "textarea") and q.required:
                    out[q.id] = Answer(q.id, "N/A", "profile", confidence=1.0)
                elif not q.required:
                    out[q.id] = Answer(q.id, None, "skip", needs_human=None, confidence=1.0)
            elif pv in (None, "", []) and mine and out[q.id].source == "profile":
                out[q.id] = (Answer(q.id, None, "needs_human", needs_human="follow-up to a question left for you", confidence=0.0)
                             if q.required else Answer(q.id, None, "skip", needs_human=None, confidence=0.0))
        prev = q
    # Conditional follow-ups (Workday branching): skip when the parent's answer does not reveal them.
    for q in questions:
        parent, when = q.extra.get("branch_of"), q.extra.get("branch_when")
        pa = out.get(parent) if parent else None
        if pa is not None and pa.value not in (None, "", []) and when is not None:
            vals = pa.value if isinstance(pa.value, list) else [pa.value]
            if not any(_norm(str(v)) == _norm(str(when)) for v in vals):
                out[q.id] = Answer(q.id, None, "skip", needs_human=None, confidence=1.0)
    return out


# ---------------------------------------------------------------- Claude for the rest

CLAUDE_SCHEMA = {
    "type": "object", "required": ["answers"],
    "properties": {"answers": {"type": "array", "items": {
        "type": "object", "required": ["question_id", "value", "needs_human", "confidence", "rationale"],
        "properties": {"question_id": {"type": "string"},
                       "value": {"type": ["string", "array", "null"], "items": {"type": "string"}},
                       "needs_human": {"type": ["string", "null"]},
                       "confidence": {"type": "number"},
                       "rationale": {"type": "string"}}}}},
}


def _read(path: Path | None, limit: int = 24000) -> str:
    if not path or not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[:limit]


def build_claude_prompt(profile: Profile, questions: list[Question], role: intake.RoleFolder, company: str, title: str) -> str:
    playbook = _read(PLAYBOOKS_DIR / "ANSWERS.md", 20000)
    qs = [{"id": q.id, "label": q.label, "type": q.type, "required": q.required, "options": q.options,
           "description": q.extra.get("description", "")} for q in questions]
    parts = [playbook, "\n---\n",
             f"# JOB\n{company}: {title}\n",
             "# PROFILE\n```json\n" + json.dumps(profile.model_dump(mode="json"), indent=1) + "\n```\n",
             "# QUESTIONS\n```json\n" + json.dumps(qs, indent=1, ensure_ascii=False) + "\n```\n",
             "# EXPERIENCE\n" + _read(role.experience_md, 16000),
             "\n# NO_BRAINER\n" + _read(role.no_brainer_md, 22000),
             "\n# BREAKDOWN\n" + _read(role.realistic_breakdown_md, 22000),
             "\n# RECONCILIATION\n" + _read(role.reconciliation_md, 12000),
             "\n# JD\n" + _read(role.jd_txt, 12000),
             "\nReturn only the JSON object described in section 3 of the playbook."]
    return "\n".join(parts)


def validate_claude_answers(raw: dict, questions: list[Question]) -> dict[str, Answer]:
    by_id = {q.id: q for q in questions}
    out: dict[str, Answer] = {}
    for a in (raw or {}).get("answers", []):
        qid = str(a.get("question_id", ""))
        q = by_id.get(qid)
        if q is None:
            continue
        nh = a.get("needs_human")
        val = a.get("value")
        conf = float(a.get("confidence") or 0)
        if KNOCKOUT.search(q.label):
            out[qid] = Answer(qid, None, "needs_human", needs_human="knock-out question left to Alex", confidence=0.0)
            continue
        if nh or val in (None, "", []) or conf < 0.5:
            # source stays "claude" so a re-draft reuses the verdict instead of paying to ask again
            out[qid] = Answer(qid, None, "claude", needs_human=str(nh or "no confident answer"), confidence=conf)
            continue
        if q.type in ("select", "multiselect"):
            vals = val if isinstance(val, list) else [val]
            picked = [match_option(q.options, v) for v in vals]
            if any(p is None for p in picked):
                out[qid] = Answer(qid, None, "needs_human", needs_human=f"option not in list: {vals}", confidence=conf)
                continue
            out[qid] = Answer(qid, picked if q.type == "multiselect" else picked[0], "claude", confidence=conf)
            continue
        text = str(val).strip()
        if scan_em_dashes(text):
            text = text.replace("—", ", ").replace(" --- ", ", ").replace("---", ", ")
        limit = 1500 if q.type == "textarea" else 250
        if len(text) > limit:
            text = text[:limit].rsplit(" ", 1)[0]
        out[qid] = Answer(qid, text, "claude", confidence=conf)
    return out


def claude_answers(cfg: Config, conn, app: Application, profile: Profile, questions: list[Question],
                   role: intake.RoleFolder) -> tuple[dict[str, Answer], float]:
    if not questions:
        return {}, 0.0
    prompt = build_claude_prompt(profile, questions, role, app.company, app.title)
    log_path = app_log_path(cfg.logs_dir, app.id, "answers")
    res = run_claude(cfg, prompt=prompt, cwd=app.workdir, log_path=log_path, model=str(cfg.claude.answers_model),
                     timeout_s=int(cfg.claude.timeout_min) * 60, max_turns=int(cfg.claude.answers_max_turns),
                     max_budget_usd=cfg.claude.get("max_budget_usd"), json_schema=CLAUDE_SCHEMA, tools=[],
                     permission_mode="dontAsk", setting_sources="")
    db.record_cost(conn, app.id, purpose="answers", model=str(cfg.claude.answers_model), cost_usd=res.cost_usd,
                   num_turns=res.num_turns, session_id=res.session_id, subtype=res.subtype, duration_s=res.duration_s)
    if not res.ok:
        log.warning("answer writer failed for app %s: %s (%s)", app.id, res.subtype, res.stderr_tail[-300:])
        return {q.id: Answer(q.id, None, "needs_human", needs_human=f"answer writer failed: {res.subtype}", confidence=0.0)
                for q in questions}, res.cost_usd
    return validate_claude_answers(res.structured_output, questions), res.cost_usd


# ---------------------------------------------------------------- packet

def _prior_claude_answers(app: Application, questions: list[Question]) -> dict[str, Answer]:
    """Claude answers from an earlier answers.json, kept only for questions that still exist and are not knock-outs."""
    if not app.answers_path.exists():
        return {}
    try:
        prior = AnswerPacket.load(app.answers_path)
    except Exception:  # noqa: BLE001
        return {}
    by_id = {q.id: q for q in questions}
    out: dict[str, Answer] = {}
    for qid, a in prior.answers.items():
        q = by_id.get(qid)
        if q is None or a.source != "claude":
            continue
        if KNOCKOUT.search(q.label) or qid in LOCATION_IDS:
            continue
        if a.needs_human:
            out[qid] = a          # Claude's "leave this to Alex" verdict is worth keeping too
            continue
        if a.value in (None, "", []):
            continue
        if q.type in ("select", "multiselect"):
            vals = a.value if isinstance(a.value, list) else [a.value]
            if any(match_option(q.options, v) is None for v in vals):
                continue
        out[qid] = a
    return out


def build_packet(cfg: Config, conn, app: Application, profile: Profile, questions: list[Question], *,
                 use_claude: bool = True) -> AnswerPacket:
    role = intake.resolve_role_folder(app.role_folder)
    from .memory import memory_map
    memory = memory_map(conn)
    job = read_json(app.workdir / "job.json", {}) or {}
    answers = deterministic_answers(questions, profile, memory, job)
    remaining = [q for q in questions if q.id not in answers and q.type not in ("hidden", "file")
                 and q.id not in MOOT_IDS]
    cost = 0.0
    if remaining:
        salary = [q for q in remaining if SALARY_ASK.search(q.label) and q.type in ("text", "textarea", "number")]
        knock = [q for q in remaining if KNOCKOUT.search(q.label) and q not in salary]
        for q in knock:
            answers[q.id] = Answer(q.id, None, "needs_human", needs_human="knock-out question: answer it yourself", confidence=0.0)
        remaining = [q for q in remaining if q.id not in answers]
        # Reuse earlier Claude answers for unchanged questions so rule fixes and refills cost nothing.
        prior = _prior_claude_answers(app, questions)
        for q in list(remaining):
            if q.id in prior:
                answers[q.id] = prior[q.id]
                remaining.remove(q)
        if remaining and use_claude:
            extra, cost = claude_answers(cfg, conn, app, profile, remaining, role)
            answers.update(extra)
        for q in salary:
            if (q.id not in answers or answers[q.id].needs_human) and profile.defaults.salary_text:
                answers[q.id] = Answer(q.id, profile.defaults.salary_text, "profile", confidence=0.6)
        for q in remaining:
            answers.setdefault(q.id, Answer(q.id, None, "needs_human", needs_human="not answered", confidence=0.0))
    unanswered = [q.id for q in questions if q.required and q.type not in ("hidden",) and q.id not in MOOT_IDS
                  and not q.extra.get("dom_optional")
                  and (q.id not in answers or answers[q.id].needs_human)]
    packet = AnswerPacket(answers=answers, unanswered_required=unanswered, claude_cost_usd=cost)
    write_json(app.answers_path, packet.to_dict())
    return packet

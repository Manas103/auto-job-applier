"""The candidate profile: fixed facts every application form asks for.

Seeded from the pipeline's playbooks/ASSETS.md (single source of truth about Alex), then the holes that
ASSETS.md does not carry (address, preferred name, portfolio, EEO answers, defaults) are filled once by
`smartapply setup`. profile.yaml is gitignored.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

DECLINE = "Decline to self-identify"


class Address(BaseModel):
    street: str = ""
    street2: str = ""
    city: str = ""
    state: str = ""          # two-letter
    state_name: str = ""     # "Illinois"
    zip: str = ""
    country: str = "United States of America"
    country_code: str = "US"


class School(BaseModel):
    name: str = "State University"
    city: str = "College Town"
    state: str = "IL"
    degree: str = "Bachelor of Science"
    degree_short: str = "BS"
    degree_select: str = "Bachelor's Degree"   # the label ATS dropdowns use
    major: str = "Computer Science"
    gpa: str = "3.70"
    gpa_scale: str = "4.0"
    start: str = "2024-08"       # YYYY-MM
    graduation: str = "2028-05"  # YYYY-MM
    level: str = "Bachelor's"
    high_school_completed: bool = True
    high_school_name: str = ""
    high_school_graduation: str = ""   # YYYY-MM
    sat: str = ""                # total score, or "did not take"; empty = left for Alex
    act: str = ""                # composite, or "did not take"


class WorkAuth(BaseModel):
    authorized_us: bool = True
    sponsorship_required: bool = False
    status: str = "U.S. permanent resident (green card)"
    citizen: bool = False
    itar_us_person: bool = True   # permanent residents are U.S. persons under ITAR
    statement: str = "Authorized to work in the U.S. without sponsorship"
    # countries of citizenship; answers "are you a citizen or permanent resident of <embargoed list>" when none of
    # them is listed. Empty = those questions are left for Alex.
    citizenship: list[str] = Field(default_factory=list)


class Experience(BaseModel):
    company: str
    title: str
    location: str = ""
    start: str        # YYYY-MM
    end: str | None   # YYYY-MM or None for present
    kind: str = "internship"   # internship | research | open_source


class EEO(BaseModel):
    gender: str = DECLINE
    hispanic_latino: str = DECLINE      # Yes | No | Decline to self-identify
    ethnicity: str = DECLINE
    veteran: str = DECLINE
    disability: str = DECLINE
    sexual_orientation: str = DECLINE
    transgender: str = DECLINE          # Yes | No | Decline to self-identify
    signature_name: str = ""            # CC-305 form asks for a typed name


class Defaults(BaseModel):
    availability: str = "Available May to August 2027, full 12 weeks"
    start_date_text: str = "May 2027"
    how_heard: str = "Job Board"
    how_heard_detail: str = "LinkedIn"
    salary_policy: str = "match posting"   # match posting | leave blank | needs_human
    relocation: str = "Yes"
    remote_ok: bool = True
    over_18: bool = True
    previous_employee_default: bool = False
    previously_interviewed: str = "No"     # "Have you previously interviewed/applied at X?"
    background_check: str = "Yes"          # "Are you willing to undergo a background check?"
    text_consent: str = "Yes"              # "May we text you about your application?"
    met_recruiter_event: str = "No"        # "Have you met a company representative at a recruiting event?"
    languages: list[str] = Field(default_factory=lambda: ["English"])
    marketing_consent: str = "No"          # "share my data with advertising partners", newsletters
    future_consideration: str = "Yes"      # "may we consider you for other/future openings"
    employment_types: list[str] = Field(default_factory=lambda: ["Internship", "Full-Time"])   # "type of employment desired"
    # Logistics Alex answered once for every application (2026-10-01). Empty = left for him.
    summer_start: str = ""            # YYYY-MM-DD earliest start for summer roles; co-ops take the posting's earliest
    driver_license: str = ""          # "Do you have a valid driver's license?"
    essential_functions: str = ""     # "Can you perform the job's activities with or without accommodation?"
    travel: str = ""                  # willing to travel (and where: anywhere)
    onsite: str = ""                  # willing to work on-site / in office, any number of days, any U.S. location
    commute_city: str = ""            # within commuting distance only of this city; elsewhere "No" (relocation Yes)
    pay_range_ok: str = ""            # "The pay is X. Is that within your expectations?"
    salary_text: str = ""             # open salary question when the posting lists no pay
    full_time_availability: str = ""  # full time for the whole internship / 40 hours per week
    offer_deadlines: str = ""         # "Do you have upcoming offer deadlines?"
    current_offers: str = ""          # "Do you currently have any offers?"
    further_education: str = ""       # "pursuing further education right after your current studies?"
    # U.S. cities best first, for "which locations would you relocate to" (every U.S. option is ticked) and
    # "which location do you prefer" (the best listed one). Empty = left for him.
    relocation_cities: list[str] = Field(default_factory=list)


class Attestations(BaseModel):
    """Compliance yes/no facts only Alex can assert. None = not set: the field is left for him (never guessed).
    Set them once in profile.yaml (`setup` asks) and every Workday questionnaire stops asking."""
    government_employment_5y: str | None = None    # government official/employee (self) in the past 5 years
    relatives_at_employer: str | None = None       # family members employed by the company
    friends_at_employer: str | None = None         # close friends / close personal relationships employed by the company
    relatives_in_government: str | None = None     # relatives who are government officials
    conflict_of_interest: str | None = None        # financial or personal ties with the employer
    criminal_conviction: str | None = None         # convictions, pretrial diversion
    debarred: str | None = None                    # debarred / suspended from government contracting
    non_compete_bound: str | None = None           # bound by non-compete / NDA restricting employment
    other_employment: str | None = None            # will you hold other employment while working here
    owns_intellectual_property: str | None = None  # owns or controls patents, trademarks or copyrights
    relatives_employers: list[str] = []            # companies where family works (a relatives question there is his)


class Profile(BaseModel):
    first_name: str = "Alex"
    last_name: str = "Doe"
    preferred_name: str = ""
    email: str = "alex@example.com"
    phone: str = "+1-555-010-0142"
    phone_digits: str = "5550100142"
    phone_country_code: str = "+1"
    phone_type: str = "Mobile"
    linkedin: str = "https://www.linkedin.com/in/alex-doe/"
    github: str = "https://github.com/alexdoe-dev"
    portfolio: str = ""
    pronouns: str = ""                     # left blank on forms until Alex sets it; never inferred
    current_organization: str = "State University"   # Lever's required "Current company"
    address: Address = Field(default_factory=Address)
    school: School = Field(default_factory=School)
    work_auth: WorkAuth = Field(default_factory=WorkAuth)
    experiences: list[Experience] = Field(default_factory=list)
    eeo: EEO = Field(default_factory=EEO)
    defaults: Defaults = Field(default_factory=Defaults)
    attestations: Attestations = Field(default_factory=Attestations)
    resume_filename: str = "Alex_Doe_Resume.pdf"
    native_name: str = ""                  # legal name in native script; the English name when that is the answer
    transcript_path: str = ""              # absolute path of the cleaned unofficial transcript PDF (uploaded when asked)
    never_claim: list[str] = Field(default_factory=lambda: [
        "U.S. citizen", "security clearance", "fluent French", "Present at Acme Labs"])

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    def missing(self) -> list[str]:
        """Fields `setup` must ask for. Empty means the profile is complete enough to fill forms."""
        out = []
        a = self.address
        if not (a.street and a.city and a.state and a.zip):
            out.append("address")
        if self.eeo.gender == DECLINE and self.eeo.ethnicity == DECLINE and self.eeo.veteran == DECLINE \
                and self.eeo.disability == DECLINE and not self.eeo.signature_name:
            out.append("eeo (or confirm decline on all)")
        return out


# ---------------------------------------------------------------- seed from ASSETS.md

_DEFAULT_EXPERIENCES = [
    Experience(company="Acme Labs", title="Software Engineer Intern", location="Austin, TX", start="2026-02", end="2026-06"),
    Experience(company="State University, Generative AI Lab", title="Undergraduate Research Assistant",
               location="College Town, IL", start="2025-01", end="2025-05", kind="research"),
    Experience(company="State University, Vision Lab", title="Undergraduate Research Assistant",
               location="College Town, IL", start="2025-01", end="2025-05", kind="research"),
    Experience(company="OpenProjectA (Example Foundation)", title="Open Source Contributor", location="Remote", start="2025-02", end=None,
               kind="open_source"),
    Experience(company="OpenProjectB (Example Foundation)", title="Open Source Contributor", location="Remote", start="2026-05", end=None,
               kind="open_source"),
]


def seed_from_assets(assets_md: Path | None) -> Profile:
    """Read the fixed-facts table in ASSETS.md; fall back to the known values when the file is unavailable."""
    p = Profile(experiences=list(_DEFAULT_EXPERIENCES))
    if assets_md is None or not assets_md.exists():
        return p
    text = assets_md.read_text(encoding="utf-8", errors="replace")
    row = _table_rows(text)
    if "email" in row:
        p.email = row["email"]
    if "phone" in row:
        p.phone = row["phone"]
        p.phone_digits = re.sub(r"\D", "", row["phone"])[-10:]
    if "linkedin" in row:
        li = row["linkedin"].strip()
        p.linkedin = li if li.startswith("http") else f"https://www.{li.lstrip('/')}/"
    if "name" in row:
        parts = row["name"].split()
        if len(parts) >= 2:
            p.first_name, p.last_name = parts[0], " ".join(parts[1:])
    m = re.search(r"GPA\s*([0-9.]+)", row.get("degree", ""))
    if m:
        p.school.gpa = m.group(1)
    p.resume_filename = f"{p.first_name}_{p.last_name}_Resume.pdf"
    return p


def _table_rows(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*$", line)
        if m:
            key = m.group(1).strip().lower()
            if key in ("field", "---") or set(key) <= {"-", ":"}:
                continue
            out[key] = re.sub(r"`", "", m.group(2)).strip()
    return out


# ---------------------------------------------------------------- load / save

def load_profile(path: Path) -> Profile:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Profile.model_validate(data)


def save_profile(profile: Profile, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(profile.model_dump(mode="json"), f, sort_keys=False, allow_unicode=True)


def public_view(profile: Profile) -> dict[str, Any]:
    """What the answer writer may see: everything (the profile carries no secrets)."""
    return profile.model_dump(mode="json")

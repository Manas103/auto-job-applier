"""roles/INDEX.md: parse rows, pick nearest-domain exemplars, append and update rows."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config

COLUMNS = ["Folder", "Company", "Role", "Term", "Domain tags", "Full-ideal name", "Full-ideal school",
           "Realistic projects claimed", "Repos"]


@dataclass
class IndexRow:
    folder: str
    company: str
    role: str
    term: str
    tags: list[str]
    name: str
    school: str
    projects: str
    repos: str

    def to_line(self) -> str:
        cells = [self.folder, self.company, self.role, self.term, ", ".join(self.tags), self.name, self.school,
                 self.projects, self.repos]
        return "| " + " | ".join(c.replace("|", "/").replace("\n", " ") for c in cells) + " |"


def index_path(cfg: Config) -> Path:
    return cfg.roles_dir / "INDEX.md"


def parse_rows(text: str) -> list[IndexRow]:
    rows: list[IndexRow] = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != len(COLUMNS) or cells[0] in ("Folder", "") or set(cells[0]) <= set("-: "):
            continue
        rows.append(IndexRow(cells[0], cells[1], cells[2], cells[3],
                             [t.strip() for t in cells[4].split(",") if t.strip()], cells[5], cells[6], cells[7], cells[8]))
    return rows


def read_rows(cfg: Config) -> list[IndexRow]:
    p = index_path(cfg)
    return parse_rows(p.read_text(encoding="utf-8")) if p.exists() else []


def nearest_exemplars(cfg: Config, tags: list[str], *, exclude_folder: str | None = None, k: int = 2) -> list[IndexRow]:
    rows = [r for r in read_rows(cfg) if r.folder != exclude_folder]
    want = {t.lower() for t in tags}
    scored = []
    for r in rows:
        overlap = len(want & {t.lower() for t in r.tags})
        scored.append((overlap, r))
    scored.sort(key=lambda x: (-x[0], x[1].folder))
    return [r for s, r in scored[:k] if s > 0] or [r for _, r in scored[:k]]


def has_row(cfg: Config, folder: str) -> bool:
    return any(r.folder == folder for r in read_rows(cfg))


def append_row(cfg: Config, row: IndexRow) -> bool:
    """Append after the last table row. Returns False if a row for the folder already exists."""
    p = index_path(cfg)
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    if has_row(cfg, row.folder):
        return False
    lines = text.splitlines()
    last = max((i for i, ln in enumerate(lines) if ln.startswith("|")), default=None)
    if last is None:
        header = "| " + " | ".join(COLUMNS) + " |\n|" + "---|" * len(COLUMNS)
        text = text.rstrip("\n") + "\n\n" + header + "\n" + row.to_line() + "\n"
    else:
        lines.insert(last + 1, row.to_line())
        text = "\n".join(lines) + "\n"
    p.write_text(text, encoding="utf-8")
    return True


def update_repos(cfg: Config, folder: str, repos: list[str]) -> bool:
    p = index_path(cfg)
    if not p.exists():
        return False
    lines = p.read_text(encoding="utf-8").splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("| " + folder + " |") or ln.startswith("|" + folder + "|"):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) == len(COLUMNS):
                cells[8] = "; ".join(repos) if repos else "(not built yet)"
                lines[i] = "| " + " | ".join(cells) + " |"
                p.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return True
    return False


def row_from_meta(folder: str, meta: dict, *, term_default: str = "Summer 2027") -> IndexRow:
    projects = "; ".join(f"{pr.get('title', '?')} ({pr.get('month', '?')})" for pr in meta.get("projects", []))
    repos = [pr.get("existing_repo") for pr in meta.get("projects", []) if pr.get("existing_repo")]
    fi = meta.get("full_ideal") or {}
    return IndexRow(folder=folder, company=str(meta.get("company", "")), role=str(meta.get("title", "")),
                    term=str(meta.get("term", term_default)), tags=[str(t) for t in meta.get("domain_tags", [])],
                    name=str(fi.get("name", "")), school=str(fi.get("school", "")), projects=projects,
                    repos="; ".join(repos) if repos else "(not built yet)")


def load_meta(folder: Path) -> dict:
    p = folder / "meta.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _short_school(school: str) -> str:
    """'University of Pennsylvania, B.S.E. ... (chosen because ...)' -> 'University of Pennsylvania'."""
    head = re.split(r"\s*\(|;", school, maxsplit=1)[0]
    return re.split(r",\s*(?:B\.|M\.|Bachelor|Master|BS|BA|MS|Ph\.?D|Honours|Honors|Candidate)", head, maxsplit=1)[0].strip()


def used_identities_path(cfg: Config) -> Path:
    return cfg.roles_dir / "USED_IDENTITIES.md"


def write_used_identities(cfg: Config) -> Path:
    """Generate roles/USED_IDENTITIES.md, the compact view of INDEX.md that the ideamaker and critic need (names and
    schools already used, and which company took each). INDEX.md has grown past 300 KB and every run grepped it."""
    out = used_identities_path(cfg)
    src = index_path(cfg)
    if out.exists() and src.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    rows = read_rows(cfg)
    names = sorted({r.name for r in rows if r.name})
    schools: dict[str, list[str]] = {}
    for r in rows:
        if r.school:
            schools.setdefault(_short_school(r.school), []).append(r.company)
    lines = ["# Names and schools already used", "",
             "Generated from roles/INDEX.md by the pipeline; do not edit. A full-ideal name must not appear here. A school "
             "may be reused only when the pipeline argument genuinely points there again; the companies that used it are listed.",
             "", f"## Names ({len(names)})", "", ", ".join(names), "", f"## Schools ({len(schools)})", ""]
    for s in sorted(schools, key=lambda k: (-len(schools[k]), k)):
        cos = sorted(set(schools[s]))
        lines.append(f"- {s} ({len(schools[s])}): {', '.join(cos)}")
    lines += ["", "## Prior roles (folder: domain tags)", ""]
    lines += [f"- {r.folder}: {', '.join(r.tags)}" for r in rows]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def used_names(cfg: Config) -> list[str]:
    return [r.name for r in read_rows(cfg) if r.name]


_TAG_HINTS = [
    (r"quant|trading|market|hedge|prop", ["trading", "low-latency", "cpp"]),
    (r"machine learning|\bml\b|\bai\b|inference|training|llm", ["ml-systems", "ml-infra", "cuda"]),
    (r"data scien|analytics|forecast", ["data-science", "analytics"]),
    (r"embedded|firmware|iot|hardware", ["embedded", "firmware", "iot", "c"]),
    (r"aerospace|space|rocket|flight|defense|clearance", ["aerospace", "flight-software", "defense"]),
    (r"game|roblox|unreal|unity", ["games", "real-time"]),
    (r"trust|safety|abuse|moderation|fraud", ["trust-safety", "streaming"]),
    (r"cloud|backend|platform|infrastructure|distributed", ["backend", "cloud", "platform"]),
    (r"video|media|streaming", ["video", "streaming"]),
    (r"network|security|cyber", ["networking", "cyber"]),
]


def guess_tags(title: str, jd_text: str | None) -> list[str]:
    text = f"{title}\n{(jd_text or '')[:4000]}".lower()
    tags: list[str] = []
    for pat, ts in _TAG_HINTS:
        if re.search(pat, text):
            for t in ts:
                if t not in tags:
                    tags.append(t)
    return tags or ["backend"]


# ----------------------------------------------------------------------------- archetypes (lite lanes)

ARCHETYPES = ("backend", "ml", "data", "quant", "systems")

# Word-bounded on purpose: "market" must not hit "marketing", "ai" must not hit "maintain".
_ARCHETYPE_HINTS: list[tuple[str, str]] = [
    ("quant", r"\b(quant\w*|trading|trader|hedge fund|market making|market maker|prop(rietary)? trading|"
              r"low[- ]latency|derivatives|order book)\b"),
    ("ml", r"\b(machine learning|ml|ai|artificial intelligence|deep learning|llms?|inference|model training|"
           r"computer vision|nlp|pytorch|tensorflow|genai|generative ai)\b"),
    ("data", r"\b(data scien\w*|data analy\w*|analytics|analyst|forecast\w*|business intelligence|bi|tableau|"
             r"power bi|statistic\w*|data engineer\w*|etl|dashboards?)\b"),
    ("systems", r"\b(embedded|firmware|iot|hardware|fpga|rtos|aerospace|flight software|avionics|robotics|"
                r"spacecraft|satellite|networking|kernel|drivers?)\b"),
    ("backend", r"\b(backend|back[- ]end|cloud|platform|infrastructure|distributed|microservices?|apis?|"
                r"full[- ]stack|devops|kubernetes|web)\b"),
]


def classify_archetype(title: str, jd_text: str | None) -> str:
    """Which archetype resume fits a role best. Deterministic: hint hits in the title count 3x, in the JD 1x
    (first 6000 characters). Ties go to the order of ARCHETYPES' specificity (quant, ml, data, systems, backend);
    no hits at all means backend."""
    t = (title or "").lower()
    jd = (jd_text or "")[:6000].lower()
    scores: dict[str, int] = {}
    for name, pat in _ARCHETYPE_HINTS:
        rx = re.compile(pat)
        scores[name] = 3 * len(rx.findall(t)) + len(rx.findall(jd))
    order = [n for n, _ in _ARCHETYPE_HINTS]
    best = max(order, key=lambda n: (scores[n], -order.index(n)))
    return best if scores[best] > 0 else "backend"

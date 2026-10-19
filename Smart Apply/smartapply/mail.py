"""Gmail over IMAP, read-only, for the mails an application flow needs: account verification links (Workday,
Eightfold), PINs (Oracle), post-submit security codes (Greenhouse) and confirmations.

Copied in shape from Archon v2's mail.py. Read-only is enforced, not promised: the mailbox is selected with
`readonly=True` and every fetch uses `BODY.PEEK`, which never sets the \\Seen flag. Nothing here appends, flags,
deletes or sends. Credentials are Archon's `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` read from its `.env` at call time,
never copied anywhere.

Two passes on purpose: headers first for everything since `days` ago, then the body only for messages the caller's
`want(from, subject)` predicate accepts, so polling for one verification mail does not download the whole inbox.
"""

from __future__ import annotations

import email
import email.utils
import html as html_mod
import imaplib
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.header import decode_header, make_header
from typing import Callable

from dotenv import dotenv_values

from .config import Config
from .log import get_logger

log = get_logger("mail")

IMAP_HOST = "imap.gmail.com"
MAX_BODY_CHARS = 20000
HEADERS = "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT DATE)])"


@dataclass(frozen=True)
class RawMail:
    uid: str                     # Message-ID when present, else "imapuid:<uid>" (sequence numbers are never used)
    imap_uid: str
    from_addr: str
    subject: str
    received_at: datetime
    body: str = ""               # text/plain (or tag-stripped HTML)
    html: str = ""               # raw text/html part, for links whose text form is missing
    links: list[str] = field(default_factory=list)


class MailNotConfigured(Exception):
    pass


# ---------------------------------------------------------------- credentials

def credentials(cfg: Config) -> tuple[str, str]:
    env = dotenv_values(cfg.archon_env_path) if cfg.archon_env_path.exists() else {}
    user = (env.get("GMAIL_ADDRESS") or "").strip()
    # Google shows App Passwords in four spaced groups; IMAP LOGIN rejects the spaces.
    password = (env.get("GMAIL_APP_PASSWORD") or "").replace(" ", "").strip()
    if not user or not password:
        raise MailNotConfigured(f"GMAIL_ADDRESS / GMAIL_APP_PASSWORD missing in {cfg.archon_env_path}")
    return user, password


# ---------------------------------------------------------------- parsing

def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (UnicodeDecodeError, LookupError, ValueError):
        return value


def _parts(msg: email.message.Message) -> tuple[str, str]:
    plain: list[str] = []
    htmls: list[str] = []
    walker = msg.walk() if msg.is_multipart() else [msg]
    for part in walker:
        ctype = part.get_content_type()
        if "attachment" in str(part.get("Content-Disposition", "")):
            continue
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            payload = part.get_payload(decode=True)
            text = payload.decode(part.get_content_charset() or "utf-8", "replace") if payload is not None else str(part.get_payload())
        except (AttributeError, LookupError, ValueError):
            continue
        (plain if ctype == "text/plain" else htmls).append(text)
    html = "\n".join(htmls)
    if plain:
        text = "\n".join(plain)
    else:
        text = html_mod.unescape(re.sub(r"<[^>]+>", " ", html))
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", re.sub(r"[ \t]{2,}", " ", text)).strip()
    return text[:MAX_BODY_CHARS], html[:MAX_BODY_CHARS * 4]


_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
_HREF_RE = re.compile(r"href\s*=\s*[\"']([^\"']+)[\"']", re.I)


def extract_links(text: str, html: str = "") -> list[str]:
    out: list[str] = []
    for m in _HREF_RE.finditer(html or ""):
        u = html_mod.unescape(m.group(1)).strip()
        if u.startswith("http") and u not in out:
            out.append(u)
    for m in _URL_RE.finditer(text or ""):
        u = html_mod.unescape(m.group(0)).rstrip(".,;:")
        if u not in out:
            out.append(u)
    return out


def dedupe_key(message_id: str | None, imap_uid: str) -> str:
    mid = (message_id or "").strip()
    return mid if mid else f"imapuid:{imap_uid}"


# ---------------------------------------------------------------- IMAP

def fetch_recent(cfg: Config, *, days: int = 2, limit: int = 60, mailbox: str = "INBOX",
                 want: Callable[[str, str], bool] | None = None) -> list[RawMail]:
    """Headers for everything since `days` ago (newest `limit`); bodies only where `want(from, subject)` is true."""
    user, password = credentials(cfg)
    since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
    out: list[RawMail] = []
    conn = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        conn.login(user, password)
        conn.select(mailbox, readonly=True)
        typ, data = conn.uid("SEARCH", None, f"(SINCE {since})")
        if typ != "OK":
            return []
        uids = (data[0] or b"").split()[-limit:]
        for raw_uid in uids:
            uid = raw_uid.decode()
            typ, payload = conn.uid("FETCH", uid, HEADERS)
            if typ != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            head = email.message_from_bytes(payload[0][1])
            from_addr = _decode(head.get("From"))
            subject = _decode(head.get("Subject"))
            try:
                received = email.utils.parsedate_to_datetime(head.get("Date"))
            except (TypeError, ValueError):
                received = datetime.now().astimezone()
            if received.tzinfo is None:
                received = received.astimezone()
            body = html = ""
            links: list[str] = []
            if want is None or want(from_addr, subject):
                typ, payload = conn.uid("FETCH", uid, "(BODY.PEEK[])")
                if typ == "OK" and payload and isinstance(payload[0], tuple):
                    body, html = _parts(email.message_from_bytes(payload[0][1]))
                    links = extract_links(body, html)
            out.append(RawMail(uid=dedupe_key(head.get("Message-ID"), uid), imap_uid=uid, from_addr=from_addr,
                               subject=subject, received_at=received, body=body, html=html, links=links))
    finally:
        for fn in (conn.close, conn.logout):
            try:
                fn()
            except (imaplib.IMAP4.error, OSError):
                pass
    return out


def wait_for(cfg: Config, *, sender: str | re.Pattern | None = None, subject: str | re.Pattern | None = None,
             since: datetime | None = None, timeout_s: float | None = None, poll_s: float | None = None,
             body_match: Callable[[RawMail], bool] | None = None,
             fetch: Callable[..., list[RawMail]] | None = None) -> RawMail | None:
    """Poll until a mail matching sender/subject (regex or substring, case-insensitive) that arrived after `since`
    exists; returns the newest one with its body and links, or None on timeout."""
    timeout_s = float(cfg.mail.wait_timeout_s if timeout_s is None else timeout_s)
    poll_s = float(cfg.mail.poll_s if poll_s is None else poll_s)
    fetch = fetch or fetch_recent
    since = since or (datetime.now().astimezone() - timedelta(minutes=10))

    def _rx(x):
        if x is None:
            return None
        return x if isinstance(x, re.Pattern) else re.compile(re.escape(x), re.I)

    s_rx, j_rx = _rx(sender), _rx(subject)

    def want(from_addr: str, subj: str) -> bool:
        return (s_rx is None or bool(s_rx.search(from_addr))) and (j_rx is None or bool(j_rx.search(subj)))

    deadline = time.time() + timeout_s
    while True:
        try:
            mails = fetch(cfg, days=int(cfg.mail.days), limit=int(cfg.mail.limit), want=want)
        except MailNotConfigured:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("mail poll failed: %s", e)
            mails = []
        hits = [m for m in mails if want(m.from_addr, m.subject) and m.received_at >= since - timedelta(minutes=2)
                and (body_match is None or body_match(m))]
        if hits:
            hits.sort(key=lambda m: m.received_at)
            return hits[-1]
        if time.time() >= deadline:
            return None
        time.sleep(min(poll_s, max(0.0, deadline - time.time())))


def pick_link(mail: RawMail, *patterns: str | re.Pattern) -> str | None:
    """First link matching any pattern (regex or substring), in the mail's link order."""
    for p in patterns:
        rx = p if isinstance(p, re.Pattern) else re.compile(re.escape(p), re.I)
        for u in mail.links:
            if rx.search(u):
                return u
    return None

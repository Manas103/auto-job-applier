"""Account passwords live in the Windows Credential Manager (keyring's WinVaultKeyring), never in SQLite, logs or
files. One credential per ATS tenant: service "SmartApply", username "<ats>:<tenant>".

`generate_password` honours the tenant's own rules (Workday `account/configuration` `passwordRules`: MUST_SPECIAL,
MUST_UPPERCASE, MUST_LOWERCASE, MUST_ALPHABET, MUST_NUMERIC, MIN_LENGTH, MAX_LENGTH) so a created account never
fails on the password. Tests call `use_memory()` to swap in a dict instead of the OS vault.
"""

from __future__ import annotations

import secrets
import string
from typing import Any

SERVICE = "SmartApply"
SPECIALS = "!@#$%^&*"          # accepted everywhere; no quotes, backslashes or spaces
_MEMORY: dict[str, str] | None = None


def use_memory() -> dict[str, str]:
    """Tests: keep credentials in a dict for the life of the process."""
    global _MEMORY
    _MEMORY = {}
    return _MEMORY


def key(ats: str, tenant: str) -> str:
    return f"{ats}:{tenant}".lower()


def get_password(ats: str, tenant: str) -> str | None:
    k = key(ats, tenant)
    if _MEMORY is not None:
        return _MEMORY.get(k)
    import keyring
    return keyring.get_password(SERVICE, k)


def set_password(ats: str, tenant: str, password: str) -> None:
    k = key(ats, tenant)
    if _MEMORY is not None:
        _MEMORY[k] = password
        return
    import keyring
    keyring.set_password(SERVICE, k, password)


def delete_password(ats: str, tenant: str) -> None:
    k = key(ats, tenant)
    if _MEMORY is not None:
        _MEMORY.pop(k, None)
        return
    import keyring
    try:
        keyring.delete_password(SERVICE, k)
    except keyring.errors.PasswordDeleteError:
        pass


DEFAULT_KEY = ("default", "new_account")


def get_default_password() -> str | None:
    """The password Alex wants on every new account (set with `smartapply account default`)."""
    return get_password(*DEFAULT_KEY)


def set_default_password(password: str) -> None:
    set_password(*DEFAULT_KEY, password)


def new_account_password(rules: list[dict[str, Any]] | None = None) -> str:
    """Alex's default password when one is stored and the site's rules accept it; a generated one otherwise."""
    default = get_default_password()
    if default and not violations(default, rules):
        return default
    return generate_password(rules)


def backend_name() -> str:
    if _MEMORY is not None:
        return "memory"
    import keyring
    return str(keyring.get_keyring())


# ---------------------------------------------------------------- password rules

def _rule_map(rules: list[dict[str, Any]] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for r in rules or []:
        name = str(r.get("name") or "").upper()
        if name:
            out[name] = r.get("value")
    return out


def violations(password: str, rules: list[dict[str, Any]] | None) -> list[str]:
    """Rule names the password breaks (empty list = acceptable)."""
    rm = _rule_map(rules)
    bad: list[str] = []
    if rm.get("MUST_SPECIAL") and not any(c in SPECIALS or (not c.isalnum() and not c.isspace()) for c in password):
        bad.append("MUST_SPECIAL")
    if rm.get("MUST_UPPERCASE") and not any(c.isupper() for c in password):
        bad.append("MUST_UPPERCASE")
    if rm.get("MUST_LOWERCASE") and not any(c.islower() for c in password):
        bad.append("MUST_LOWERCASE")
    if rm.get("MUST_ALPHABET") and not any(c.isalpha() for c in password):
        bad.append("MUST_ALPHABET")
    if rm.get("MUST_NUMERIC") and not any(c.isdigit() for c in password):
        bad.append("MUST_NUMERIC")
    try:
        if rm.get("MIN_LENGTH") and len(password) < int(rm["MIN_LENGTH"]):
            bad.append("MIN_LENGTH")
        if rm.get("MAX_LENGTH") and len(password) > int(rm["MAX_LENGTH"]):
            bad.append("MAX_LENGTH")
    except (TypeError, ValueError):
        pass
    return bad


def generate_password(rules: list[dict[str, Any]] | None = None, *, length: int = 16) -> str:
    """A random password with every character class, sized to the tenant's bounds."""
    rm = _rule_map(rules)
    try:
        lo = int(rm.get("MIN_LENGTH") or 0)
    except (TypeError, ValueError):
        lo = 0
    try:
        hi = int(rm.get("MAX_LENGTH") or 0)
    except (TypeError, ValueError):
        hi = 0
    n = max(length, lo, 12)
    if hi:
        n = min(n, hi)
    n = max(n, 8)
    rng = secrets.SystemRandom()
    pool = string.ascii_letters + string.digits + SPECIALS
    for _ in range(50):
        chars = [rng.choice(string.ascii_uppercase), rng.choice(string.ascii_lowercase), rng.choice(string.digits),
                 rng.choice(SPECIALS)]
        chars += [rng.choice(pool) for _ in range(n - len(chars))]
        rng.shuffle(chars)
        pw = "".join(chars)
        if not violations(pw, rules):
            return pw
    raise ValueError(f"cannot satisfy password rules {rules!r}")

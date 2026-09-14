"""Password policy for Media Studio Enterprise accounts.

``validate_password`` returns None when a password is acceptable, otherwise a
short message explaining what is wrong (shown to the user as-is). It is the
single source of truth: the API rejects a failing password with HTTP 400 and
the frontend shows the message — nothing is duplicated client-side.

The policy is CONFIGURABLE. Admins edit it in Settings › Password policy
(``GET``/``PUT /api/settings/password-policy``) and it persists as
``password_policy`` in the app config (``data/config.json``); ``load_policy``
falls back to ``DEFAULT_POLICY`` field by field, so a hand-edited or partial
entry can never break sign-in. Two rules are not options because they protect
the mechanism rather than express a preference: a password is never blank or
padded with spaces, and never longer than bcrypt hashes (72 bytes — bcrypt
silently ignores the rest, so a longer password would be weaker than it looks).
"""

from dataclasses import asdict, dataclass, fields

from utils.config import config

CONFIG_KEY = "password_policy"
BCRYPT_MAX_BYTES = 72
MIN_LENGTH_FLOOR = 4
MIN_LENGTH_CEILING = 64

# Passwords no policy should accept: the handful that top every breach list,
# plus this product's own seeded and brand words. Compared case-insensitively.
COMMON_PASSWORDS = frozenset({
    "password", "passw0rd", "password1", "password123", "123456", "12345678",
    "123456789", "1234567890", "qwerty", "qwerty123", "letmein", "welcome",
    "welcome1", "admin", "admin123", "administrator", "changeme", "iloveyou",
    "monkey", "dragon", "abc123", "pentaho", "mediastudio", "media studio",
})


@dataclass(frozen=True)
class PasswordPolicy:
    """The adjustable rules. The defaults favour length over complexity — the
    guidance most security teams give today — and keep the two cheap checks
    that stop the worst choices."""

    min_length: int = 12
    require_upper: bool = False
    require_digit: bool = False
    require_symbol: bool = False
    forbid_username: bool = True
    forbid_common: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_POLICY = PasswordPolicy()


def policy_from_dict(raw: dict | None) -> PasswordPolicy:
    """A policy from stored data, tolerant of junk: unknown keys are ignored,
    missing or malformed values take the default, and the minimum length is
    clamped to a sane range."""
    raw = raw if isinstance(raw, dict) else {}
    values = {}
    for f in fields(PasswordPolicy):
        default = getattr(DEFAULT_POLICY, f.name)
        value = raw.get(f.name, default)
        if isinstance(default, bool):
            values[f.name] = bool(value)
        else:
            try:
                number = int(value)
            except (TypeError, ValueError):
                number = default
            values[f.name] = max(MIN_LENGTH_FLOOR, min(MIN_LENGTH_CEILING, number))
    return PasswordPolicy(**values)


def load_policy() -> PasswordPolicy:
    """The active policy from the app config (the defaults when none is stored)."""
    return policy_from_dict(config._config.get(CONFIG_KEY))


def save_policy(policy: PasswordPolicy) -> PasswordPolicy:
    """Persist ``policy`` as the active one."""
    config._config[CONFIG_KEY] = policy.to_dict()
    config.save()
    return policy


def describe_policy(policy: PasswordPolicy | None = None) -> str:
    """The policy in one sentence, for the hint beside a password field."""
    p = policy or load_policy()
    parts = [f"at least {p.min_length} characters"]
    if p.require_upper:
        parts.append("an upper-case letter")
    if p.require_digit:
        parts.append("a digit")
    if p.require_symbol:
        parts.append("a symbol")
    if p.forbid_username:
        parts.append("not your username")
    if p.forbid_common:
        parts.append("not a common password")
    text = "; ".join(parts)
    return text[0].upper() + text[1:] + "."


def validate_password(password: str, username: str | None = None,
                      policy: PasswordPolicy | None = None) -> str | None:
    """None when ``password`` passes ``policy`` (the active one by default),
    otherwise the message the user should read."""
    p = policy or load_policy()
    if not password or not password.strip():
        return "Enter a password — it cannot be blank."
    if password != password.strip():
        return "The password must not start or end with a space."
    if len(password) < p.min_length:
        return f"The password must be at least {p.min_length} characters (this one has {len(password)})."
    if len(password.encode("utf-8")) > BCRYPT_MAX_BYTES:
        return f"The password is too long — keep it under {BCRYPT_MAX_BYTES} characters."
    if p.require_upper and not any(c.isupper() for c in password):
        return "Add at least one upper-case letter."
    if p.require_digit and not any(c.isdigit() for c in password):
        return "Add at least one digit."
    if p.require_symbol and not any(not c.isalnum() and not c.isspace() for c in password):
        return "Add at least one symbol (for example - _ ! ? #)."
    if p.forbid_username and username:
        u = username.strip().casefold()
        if u and (password.casefold() == u or (len(u) >= 4 and u in password.casefold())):
            return "The password must not contain your username."
    if p.forbid_common and password.casefold() in COMMON_PASSWORDS:
        return "That password is on every attacker's list — choose something less common."
    return None

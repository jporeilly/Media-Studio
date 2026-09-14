"""The password policy: the adjustable rules, the always-on rules, the config
round-trip and the one-sentence description shown beside password fields."""

import pytest

from api.passwords import (
    DEFAULT_POLICY,
    PasswordPolicy,
    describe_policy,
    load_policy,
    policy_from_dict,
    save_policy,
    validate_password,
)
from utils.config import config


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    """Never touch data/config.json from a test."""
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)


# A policy with every adjustable rule relaxed, to test the always-on rules alone.
LAX = PasswordPolicy(min_length=4, forbid_username=False, forbid_common=False)


def test_defaults_favour_length_over_complexity():
    assert DEFAULT_POLICY == PasswordPolicy(
        min_length=12, require_upper=False, require_digit=False, require_symbol=False,
        forbid_username=True, forbid_common=True,
    )


@pytest.mark.parametrize("pw", ["", "   ", "\t\n"])
def test_blank_is_always_rejected(pw):
    assert "blank" in validate_password(pw, policy=LAX)


def test_padding_spaces_are_always_rejected():
    assert "start or end" in validate_password(" abcd", policy=LAX)
    assert "start or end" in validate_password("abcd ", policy=LAX)


def test_longer_than_bcrypt_hashes_is_always_rejected():
    assert validate_password("x" * 72, policy=LAX) is None
    assert "too long" in validate_password("x" * 73, policy=LAX)


def test_minimum_length_message_says_how_far_off():
    p = PasswordPolicy(min_length=8, forbid_username=False, forbid_common=False)
    msg = validate_password("short", policy=p)
    assert "at least 8" in msg and "has 5" in msg
    assert validate_password("longenough", policy=p) is None


@pytest.mark.parametrize(
    "rule, good, bad, word",
    [
        ("require_upper", "abcDefgh", "abcdefgh", "upper-case"),
        ("require_digit", "abcdefg1", "abcdefgh", "digit"),
        ("require_symbol", "abcdefg!", "abcdefgh", "symbol"),
    ],
)
def test_complexity_toggles(rule, good, bad, word):
    p = PasswordPolicy(min_length=4, forbid_username=False, forbid_common=False, **{rule: True})
    assert validate_password(good, policy=p) is None
    assert word in validate_password(bad, policy=p)


def test_username_rule_is_case_insensitive_and_catches_containment():
    p = PasswordPolicy(min_length=2, forbid_common=False)
    assert "username" in validate_password("Jane.Doe", "jane.doe", policy=p)
    assert "username" in validate_password("xxJANE.DOExx", "jane.doe", policy=p)
    assert validate_password("abcdefgh", "jane.doe", policy=p) is None
    # Very short usernames are only rejected on equality, never on containment.
    assert validate_password("ab12cdef", "ab", policy=p) is None
    assert "username" in validate_password("ab", "ab", policy=p)
    assert validate_password("ab", None, policy=p) is None


def test_username_rule_can_be_switched_off():
    p = PasswordPolicy(min_length=4, forbid_username=False, forbid_common=False)
    assert validate_password("jane.doe", "jane.doe", policy=p) is None


def test_common_passwords_are_rejected_unless_switched_off():
    assert "common" in validate_password("Password", policy=PasswordPolicy(min_length=4, forbid_username=False))
    assert "common" in validate_password("ADMIN", policy=PasswordPolicy(min_length=4, forbid_username=False))
    assert validate_password("Password", policy=LAX) is None


def test_a_good_password_passes_the_defaults():
    assert validate_password("correct-horse-battery", "jane") is None


def test_describe_default_policy():
    assert describe_policy(DEFAULT_POLICY) == "At least 12 characters; not your username; not a common password."


def test_describe_everything_on():
    p = PasswordPolicy(min_length=16, require_upper=True, require_digit=True, require_symbol=True)
    assert describe_policy(p) == (
        "At least 16 characters; an upper-case letter; a digit; a symbol; "
        "not your username; not a common password."
    )


def test_policy_from_dict_tolerates_junk():
    assert policy_from_dict(None) == DEFAULT_POLICY
    assert policy_from_dict("nonsense") == DEFAULT_POLICY
    assert policy_from_dict({"min_length": "abc", "require_digit": 1, "bogus": True}) == PasswordPolicy(require_digit=True)
    assert policy_from_dict({"min_length": 1}).min_length == 4
    assert policy_from_dict({"min_length": 999}).min_length == 64


def test_saved_policy_becomes_the_active_one():
    assert load_policy() == DEFAULT_POLICY
    saved = save_policy(PasswordPolicy(min_length=16, require_digit=True))
    assert config._config["password_policy"] == saved.to_dict()
    assert load_policy() == saved
    # The active policy now applies wherever no explicit policy is passed.
    assert "digit" in validate_password("no-digits-in-here-at-all", "jane")
    assert validate_password("digits-in-here-2026", "jane") is None

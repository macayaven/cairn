"""The sentences the security guide and the run guide say about cookies, sessions that are not
used, a second factor at sign-in, who may stay signed in, and what `serve` says at start. Each
test finds its sentence, then asks the code. The behaviour itself is pinned beside it:
test_host_cookies_unit.py, test_idle_sessions_unit.py, test_require_mfa_unit.py,
test_allowed_every_request_unit.py and test_serve_start_lines_unit.py."""

import re
from importlib.resources import files

from cairn import identity
from cairn.identity import OIDC, GitHub, Google
from cairn.store.credentials import USE_EVERY
from cairn.web import cli

SECURITY = files("cairn.guides").joinpath("security.md").read_text(encoding="utf-8")
RUN = files("cairn.guides").joinpath("run.md").read_text(encoding="utf-8")


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def says(text: str, sentence: str) -> None:
    assert flat(sentence) in flat(text), sentence


def test_the_security_guide_says_the_cookies_are_host_only_on_https():
    """Behaviour: test_host_cookies_unit.py (both names prefixed on https, the old name
    cleared and never read)."""
    says(
        SECURITY,
        "On an https address the session cookie, and the cookie that carries a sign-in to the"
        " provider and back, have names that start with `__Host-`, so no other host under the"
        " same domain can set either. A cookie a browser still sends under the name it had"
        " before is not read, and the answer clears it.",
    )


def test_the_security_guide_says_a_session_not_used_ends():
    """Behaviour: test_idle_sessions_unit.py."""
    says(
        SECURITY,
        "With `serve --idle-minutes N`, a session that is not used for N minutes ends, whatever"
        " its length, and the next request is answered as after any other end. It ends up to a"
        " minute late, never early: a use is recorded at most once a minute.",
    )
    assert USE_EVERY == 60


def test_the_security_guide_says_which_second_factor_counts_and_where():
    """Behaviour: test_require_mfa_unit.py."""
    says(
        SECURITY,
        "With `serve --require-mfa`, a sign-in goes through only when the ID token of the"
        " organisation's OpenID Connect provider shows a second factor: `amr` lists `mfa`, or"
        " `acr` is one of the two multi-factor policies of the OpenID Provider Authentication"
        " Policy Extension. Anything else is refused, with no session made. GitHub and Google,"
        " as {{name}} reads them, do not say how a person signed in, so the server does not"
        " start with the option and either of them.",
    )
    says(
        SECURITY,
        "Providers that report only method-specific `amr` values (such as `pwd` and `otp` or"
        " `hwk` without `mfa`) or other `acr` values (such as NIST `aal:2`) are refused unless"
        ' the provider maps them to `amr: ["mfa"]` or one of those two PAPE policies.',
    )
    assert identity.second_factor({"amr": ["pwd", "mfa"]})
    assert identity.second_factor({"amr": ["pwd", "otp"]}) is False
    assert identity.second_factor({"amr": ["pwd", "hwk"]}) is False
    assert identity.second_factor({"acr": "urn:nist:800-63-3:aal:2"}) is False
    assert len(identity.SECOND_FACTOR_ACR) == 2
    assert all(acr.startswith("http://schemas.openid.net/pape/policies/2007/06/multi-factor")
               for acr in identity.SECOND_FACTOR_ACR)  # fmt: skip
    assert OIDC.states_factors and not GitHub.states_factors and not Google.states_factors


def test_the_security_guide_says_who_is_allowed_is_checked_on_every_request():
    """Behaviour: test_allowed_every_request_unit.py."""
    says(
        SECURITY,
        "When the operator limits who may sign in, to listed addresses or email domains, the"
        " limit holds on every request, not only at sign-in. Once the server runs with a"
        " person's address no longer allowed, their sessions, the keys they made and the agents"
        " they approved are refused as ended ones. Nothing is deleted, so allowing the address"
        " again restores them.",
    )
    says(
        SECURITY,
        "A session is checked by the address its sign-in was vouched with; a key or an agent by"
        " the address the person first signed in with. A provider's groups and a Google"
        " account's Workspace domain are known only at sign-in, so a change to them takes"
        " effect at the person's next sign-in.",
    )


def test_the_run_guide_says_what_serve_says_at_start():
    """Behaviour: test_serve_start_lines_unit.py."""
    says(
        RUN,
        "With `--public-url https://...` and no `--trusted-proxy`, `serve` says at start, in one"
        " line, that every caller shares one count in the per-client limits. With sign-in or a"
        " public URL and no `--operator-notes`, it says in another that the privacy guide shows"
        " no operator, country or contact. Neither stops it.",
    )
    assert "one count" in cli.NO_TRUSTED_PROXY and "--trusted-proxy" in cli.NO_TRUSTED_PROXY
    assert all(w in cli.NO_OPERATOR_NOTES for w in ("operator", "country", "contact"))


def test_the_run_guide_names_the_two_options():
    """Behaviour: test_idle_sessions_unit.py (the first start with the option, too) and
    test_require_mfa_unit.py."""
    says(
        RUN,
        "`--idle-minutes N` (1 to 1440) ends a sign-in that is not used for N minutes, up to a"
        " minute late, whatever its length. No use is recorded without it, so the first start"
        " with it counts each sign-in made before from when it was made: one older than N"
        " minutes ends then, used or not, and the person signs in again.",
    )
    says(
        RUN,
        "`--require-mfa` admits only a sign-in whose ID token shows a second factor; only"
        " `--sign-in oidc` can say so, and `serve` does not start with it and `github` or"
        " `google` (the security guide says which values count).",
    )
    assert OIDC.states_factors and not GitHub.states_factors and not Google.states_factors

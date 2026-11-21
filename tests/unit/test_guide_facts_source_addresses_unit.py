"""Where the guides tell an operator how to reach a self-hosted source."""

from guide_source import guide_source

from cairn.sources.url import PRIVATE_ADDRESSES_SETTING


def test_the_sources_guide_tells_the_operator_how_to_connect_a_self_hosted_tool():
    text = guide_source("sources").split("## A connector of your own", 1)[1]
    text = text.split("\n## ", 1)[0]
    sentence = (
        f"For a self-hosted tool on this machine or your network, set "
        f"`{PRIVATE_ADDRESSES_SETTING}=allow` in the server's environment."
    )
    assert " ".join(text.split()).count(sentence) == 1


def test_the_run_guide_names_the_setting_default_and_the_upgrade_behavior():
    text = guide_source("run").split("## Sources the server reads", 1)[1]
    text = " ".join(text.split("\n## ", 1)[0].split())
    sentences = (
        f"`{PRIVATE_ADDRESSES_SETTING}` is `refuse` by default.",
        "Set it to `allow` to read a source on a non-global address.",
        "Any other value stops `serve` and commands that add, check or run a source.",
        "An existing source on such an address keeps its configuration and switch setting."
        " Its next run fails and the Sources page says how to allow it.",
    )
    for sentence in sentences:
        assert text.count(sentence) == 1, sentence

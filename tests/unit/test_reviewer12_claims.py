from cairn.compiler import own_claims


def test_own_claims_accounts_for_a_writer_open_across_the_upgrade():
    words = " ".join(own_claims.__doc__.split())
    assert "older writer" in words and "across the upgrade" in words

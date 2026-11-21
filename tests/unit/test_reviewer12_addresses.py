from pathlib import Path


def test_the_guide_distinguishes_the_current_page_from_a_removed_version():
    guides = Path(__file__).resolve().parents[2] / "src/cairn/guides"
    text = (guides / "run.md").read_text()
    assert "current address of every page" in text and "removed version" in text

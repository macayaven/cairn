"""RT1 against the rule that a page is held to its own project's notes (schema step 32): what
it does not yet do. Marked as failing on purpose, strictly, so the file passes as committed.
The builder is test_rt1_own_project_held's. Made-up notes."""

from contextlib import closing

import pytest
from test_rt1_own_project_held import clean_and_hit

from cairn.store import erasure


def work(clean: int, hit: int) -> int:
    """SQLite's work, in units of 100 virtual-machine steps, of one repair of `hit` pages that
    cite another project's note among `clean` pages that cite none."""
    with closing(clean_and_hit(clean, hit)) as store:
        steps = [0]

        def tick() -> int:
            steps[0] += 1
            return 0

        with store._write() as con:
            con.set_progress_handler(tick, 100)
            try:
                erasure.hold_to_own_projects(con, None, store._stamp())
            finally:
                con.set_progress_handler(None, 0)
        return steps[0]


@pytest.mark.xfail(
    strict=True,
    reason="the repair searches every page version again for each page it repairs",
)
def test_the_repair_reads_the_page_versions_a_bounded_number_of_times():
    """One search over the clean pages costs `alone`. Repairing 40 pages among them should
    cost about the 40 pages and a few such searches; it costs one search more for each page
    repaired (seen: 691 alone, 1,981 for the 40 pages with no clean page, 28,244 for both: 40
    searches), since each search restarts from the last page found with no index for its
    order. Measured: 400 pages among 0, 12,000 and 48,000 clean versions took 0.24, 1.43 and
    5.82 s inside the upgrade's one transaction. Memory stays flat (held file). Only a file
    with pages written behind the store's back, or by an earlier build's direct save, pays."""
    alone, pages, both = work(1500, 0), work(0, 40), work(1500, 40)
    assert both - pages <= 4 * alone, (alone, pages, both)

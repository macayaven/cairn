"""Store.forget_preview says how many pages of other projects a forget would change only to a
view that may forget the note (report 8's remark: it told anyone who reached the note, before
any erasure). Memory.forget_preview asks may_erase first; the store's own read now holds the
count to the same rule: anyone else is told the pages of the note's project and 0. Made-up
notes."""

from contextlib import closing

from test_rt1_forget_workspace_held import GONE, ISSUER, borrowed, citing, filed, passage

from cairn.store import Store


def test_only_a_view_that_may_forget_the_note_is_told_how_many_pages_elsewhere_change():
    with closing(Store(":memory:")) as store:
        ada, brook, cara = (
            store.sign_in("sso", f"sub-{n}", f"{n}@acme.example", n, issuer=ISSUER, join="default")
            for n in ("ada", "brook", "cara")
        )
        as_ada, as_brook, as_cara = (
            store.for_account(s.account_id, s.user.id) for s in (ada, brook, cara)
        )
        desk = as_brook.create_project("desk")
        note = filed(as_brook, desk.id, f"# Dispatch\n\n{GONE}\n")
        vault = as_ada.create_project("vault", everyone="none")
        borrowed(store, vault.id, "embargo", citing(1, GONE, passage(store, note)))
        told = {
            name: (found.rebuilt, found.removed, found.elsewhere)
            for name, view in (
                ("operator", store),
                ("maker", as_brook),
                ("owner", as_ada),
                ("member", as_cara),
                ("narrowed", store.narrow([desk.id])),
            )
            for found in [view.forget_preview(note.id)]
        }
        own = ((), ("dispatch",))
        assert told == {
            "operator": (*own, 1),
            "maker": (*own, 1),
            "owner": (*own, 1),
            "member": (*own, 0),
            "narrowed": (*own, 0),
        }

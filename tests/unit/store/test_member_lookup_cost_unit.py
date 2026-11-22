from contextlib import closing

from cairn.store.memory import MemoryMethods
from support.store.index_page_cost import SIZES, counted, workspace


def test_a_signed_in_members_lookup_counts_the_workspaces_projects():
    costs = []
    for size in SIZES:
        store, desk = workspace(size, "projects")
        with closing(store):
            for name in ("owner", "member"):
                signed = store.sign_in(
                    "sso",
                    name,
                    f"{name}@example.test",
                    name,
                    issuer="https://sso.example.test",
                    join="default",
                )
            view = store.for_account(signed.account_id, signed.user.id)
            version = view.get_page(desk, "drift").version

            def lookup(view=view, desk=desk, version=version):
                return view.passages_of_other_projects(desk, "drift", version)

            costs.append(counted(store, lookup))
    assert all(abs(n - ref) <= 2 for n, ref in zip(costs, (0, 390, 1560), strict=True)), costs
    assert "390 and 1,560" in " ".join(MemoryMethods.save_page.__doc__.split())

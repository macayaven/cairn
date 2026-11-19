"""Store is one class made of many mixins that share one namespace (and a view is a copy of
it): when two of them define the same name, the one earlier in Store's bases hides the other,
with no error. This release met it once: two mixins each defined `_room`, the second's was
never called, and connecting a Google account broke where only the whole suite saw it.

So every name (a method or a class attribute) defined by two of Store's direct bases fails
here, unless the pair is below with its reason. ReachMethods is the one class-wide exception:
it sits first on purpose and answers every public method before the area that serves it
(cairn.store.reach), and tests/unit/test_reach_unit.py classifies each of those names."""

from collections import defaultdict

from cairn.store import Store
from cairn.store.reach import ReachMethods

# (name, the earlier class, the later class): why the earlier one may define it too.
ALLOWED = {
    ("_bound", "ReachMethods", "UsageMethods"): (
        "a chain: each adds its own reason to be held to the workspace and asks super()"
    ),
    ("_reach", "ReachMethods", "AccessMethods"): (
        "the seam StoreBase._reach: each narrows what super() answers"
    ),
    ("_require_project", "AccessMethods", "MemoryMethods"): (
        "a chain: AccessMethods asks super() (the project is the view's), then checks writing"
    ),
    ("_require_owner", "PeopleMethods", "PortableMethods"): (
        "a clash kept as it is in this release: PeopleMethods' hides PortableMethods', so an"
        " export or import by a person who is not an owner is refused in PeopleMethods' words"
        " (NOT_AN_OWNER), not PortableMethods' (NOT_AN_EXPORTER); reported, to be fixed"
    ),
}


def shared_names(bases: tuple[type, ...]) -> dict[str, list[str]]:
    """Each name two or more of `bases` define in their own namespace, with those classes in
    order; dunder names aside."""
    found = defaultdict(list)
    for base in bases:
        for name in vars(base):
            if not (name.startswith("__") and name.endswith("__")):
                found[name].append(base.__name__)
    return {name: owners for name, owners in found.items() if len(owners) > 1}


def clashes(bases: tuple[type, ...]) -> list[tuple[str, str, str]]:
    """(name, earlier, later) for each pair of `bases` that define one name, ReachMethods'
    public names aside."""
    found = []
    for name, owners in shared_names(bases).items():
        for i, earlier in enumerate(owners):
            for later in owners[i + 1 :]:
                if earlier == ReachMethods.__name__ and not name.startswith("_"):
                    continue
                found.append((name, earlier, later))
    return sorted(found)


def test_no_two_of_the_stores_mixins_define_one_name_but_the_pairs_allowed():
    assert clashes(Store.__bases__) == sorted(ALLOWED)


def test_the_check_sees_two_mixins_that_define_one_name():
    """The clash of this release, made again: two areas each with a `_room` of their own."""

    class Ledger:
        def _room(self) -> str:
            return "ledger"

    class Accounts:
        _room = "a class attribute counts as well"

    class Stand(Ledger, Accounts):
        pass

    assert clashes(Stand.__bases__) == [("_room", "Ledger", "Accounts")]
    assert Stand()._room() == "ledger"  # the second one is hidden, with no error


def test_reachmethods_is_first_and_its_private_names_are_not_exempt():
    assert Store.__bases__[0] is ReachMethods

    class Other:
        def _bound(self) -> bool:
            return True

    assert clashes((ReachMethods, Other)) == [("_bound", "ReachMethods", "Other")]

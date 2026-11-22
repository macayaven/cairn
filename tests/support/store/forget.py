"""Shared test helpers extracted from test_forget_unit."""

from cairn.store import (
    Store,
)


class Interleaved:
    """A view that runs `before_save` once, just before its first save_page: what another
    request does while a compile or an arrangement is between its read and its save."""

    def __init__(self, view: Store, before_save) -> None:
        self._view, self._before = view, before_save

    def save_page(self, *args, **kwargs):
        if self._before is not None:
            run, self._before = self._before, None
            run()
        return self._view.save_page(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._view, name)

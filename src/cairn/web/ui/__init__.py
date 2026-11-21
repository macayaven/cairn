"""The web UI: its routes, the page view of a compiled page, keys, the onboarding pages, and
login with a key and logout. The forms that write (notes and projects) are in
cairn.web.compose, and the guided setup (/start) in cairn.web.start.

The routes are registered here (ui_routes), in one order; their handlers and what they show
are in a module per subject: signing_in (login, logout, /setup and /welcome), memory_pages
(projects, pages, notes and search), workspace_pages (agents, keys, sets, members' and
invitations' endings, the audit log, usage), source_pages and source_forms (the Sources page,
whose contract is source_pages' docstring), and common (the page every handler answers
with). Every name ui.py held is importable from here."""
# Every name ui.py held is imported here to be importable from the package.
# ruff: noqa: F401

from starlette.routing import Route

from cairn import oauth
from cairn.web.compose import compose_routes
from cairn.web.rest import _logged
from cairn.web.start import start_routes
from cairn.web.turns import RELATED_READS
from cairn.web.ui.common import _ended, _ui
from cairn.web.ui.memory_pages import (
    CHECKS,
    RELATED_SHOWN,
    TERM_WORDS,
    UI_HITS,
    WEB_NOTES_AT_ONCE,
    WEB_PAGES_AT_ONCE,
    _lines,
    _metric,
    _sees_rule,
    memory_handlers,
    page_view,
    related_entry,
    related_href,
    related_left_out,
    related_view,
    search_hits,
    writer_words,
)
from cairn.web.ui.signing_in import _local_path, session_routes, signing_in_handlers, welcome_path
from cairn.web.ui.source_forms import (
    _GITHUB_ITEMS,
    _LIST_FIELDS,
    _NOT_SOURCES,
    ACCOUNT_FIELD,
    CHANGE_SAYS,
    CHECKED_MEANS,
    CHOOSE_FIELDS,
    DISCONNECT_SAYS,
    DISCONNECTED_LINE,
    FORGET_SAYS,
    GITHUB_ITEMS,
    HELD_SAID,
    HIDDEN_LINK,
    HIDDEN_REASON,
    MORE_FIELD,
    NAME_FIELD,
    NO_ACCOUNT,
    NOTHING_READ,
    OPTION_FIELDS,
    PREVIEW_SAYS,
    RANGES,
    REMOVE_SAYS,
    REMOVED_THERE,
    SERVICES,
    SOURCE_COUNTS,
    STOPPED,
    SWITCH_OFF_SAYS,
    WHAT_FIELD,
    WHERE_FIELD,
    _account_rows,
    _chosen,
    _days_typed,
    _declined,
    _listed,
    _new_source_form,
    _options,
    _other_accounts,
    _proposed,
    _reads,
    _reasons,
    _refused_field,
    _refused_setting,
    _selected,
    _source_rows,
    _switching,
    _web_link,
    _whose,
)
from cairn.web.ui.source_pages import source_handlers
from cairn.web.ui.workspace_pages import (
    _FORMULA_STARTS,
    _NOT_AUDITOR,
    _NOT_KEYS_MANAGER,
    AGENT_ENDS,
    AUDIT_COLUMNS,
    AUDIT_DOWNLOADED,
    AUDIT_FILTER_MAX,
    AUDIT_FILTERS,
    AUDIT_FORMATS,
    AUDIT_SHOWN,
    INVITE_ENDS,
    KEY_ENDS,
    KEY_LABEL_MAX,
    MEMBER_ENDS,
    MEMBER_KEEPS,
    NOT_A_FORMAT,
    SET_ENDS,
    SET_KEEPS,
    USAGE_NAMES,
    _as_csv,
    _as_json,
    _audit_cursor,
    _audit_filters,
    _cell,
    _event_row,
    workspace_handlers,
)


def ui_routes(public_url: str | None, *, sign_in: bool = False) -> list[Route]:
    """`sign_in`: people sign in through a provider, so the pages speak to them, not to the
    operator of a homelab."""
    app_url = public_url.rstrip("/") if public_url else "https://<your-host>"
    mcp_url = f"{app_url}/mcp"
    by_url = oauth.issuer(public_url) is not None
    home, project, notes, page, related, search = memory_handlers()
    setup, welcome = signing_in_handlers(app_url, by_url, mcp_url, public_url, sign_in)
    (
        agents,
        keys,
        create_key,
        ask_revoke_key,
        revoke_key,
        sets,
        create_set,
        update_set,
        ask_delete_set,
        delete_set,
        audit,
        audit_download,
        usage,
        ask_disconnect,
        ask_remove_member,
        ask_revoke_invite,
        disconnect,
    ) = workspace_handlers(app_url)
    sources, new_source, switch_on, remove, disconnect_account, switch_off, preview = (
        source_handlers()
    )

    # The pages that show memory are each one record on the access log, named as the MCP
    # tool that reads the same (cairn.web.rest._logged); the others show the workspace's
    # settings, people and credentials, whose changes are in its audit log.
    return [
        Route("/", _logged("projects", home)),
        Route("/b/{project}", _logged("overview", project)),
        Route("/b/{project}/notes", _logged("notes", notes), methods=["GET"]),
        Route("/b/{project}/p/{slug}", _logged("page", page)),
        Route("/b/{project}/p/{slug}/related", _logged("related", related, RELATED_READS)),
        Route("/search", _logged("recall", search)),
        Route("/setup", setup),
        Route("/welcome", welcome),
        Route("/agents", agents),
        Route("/agents/{grant}/revoke", ask_disconnect, methods=["GET"]),
        Route("/agents/{grant}/revoke", disconnect, methods=["POST"]),
        Route("/keys", keys, methods=["GET"]),
        Route("/keys", create_key, methods=["POST"]),
        Route("/keys/{label:path}/revoke", ask_revoke_key, methods=["GET"]),
        Route("/keys/{label:path}/revoke", revoke_key, methods=["POST"]),
        Route("/sets", sets, methods=["GET"]),
        Route("/sets", create_set, methods=["POST"]),
        Route("/sets/{set}", update_set, methods=["POST"]),
        Route("/sets/{set}/delete", ask_delete_set, methods=["GET"]),
        Route("/sets/{set}/delete", delete_set, methods=["POST"]),
        # The pages before removing a person and revoking an invitation; the POSTs that do
        # them are cairn.web.members'.
        Route("/members/{user}/remove", ask_remove_member, methods=["GET"]),
        Route("/members/invites/{invite}/revoke", ask_revoke_invite, methods=["GET"]),
        Route("/audit", audit, methods=["GET"]),
        Route("/audit/download", audit_download, methods=["GET"]),
        *compose_routes(),
        *start_routes(
            mcp_url,
            oauth=by_url,
            app_url=app_url,
            public_url=public_url,
            sign_in=sign_in,
        ),
        Route("/sources", sources, methods=["GET"]),
        Route("/integrations/new", new_source, methods=["POST"]),
        Route("/integrations/{name}/remove", remove, methods=["POST"]),
        Route("/integrations/accounts/{name}/disconnect", disconnect_account, methods=["POST"]),
        Route("/integrations/{name}/on", switch_on, methods=["POST"]),
        Route("/integrations/{name}/off", switch_off, methods=["POST"]),
        Route("/integrations/{name}/preview", _logged("source_preview", preview), methods=["POST"]),
        Route("/usage", usage, methods=["GET"]),
    ]

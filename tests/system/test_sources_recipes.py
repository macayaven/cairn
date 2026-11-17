"""C1, against a stand-in: an agent files a source's items by the recipe in the sources guide.

The guide (src/cairn/guides/sources.md) gives an agent that already reads a source one
instruction per kind of source. This test plays that agent for the meetings recipe: it
reads a stand-in notes tool, writes one note per meeting over MCP with a key that reaches
one project, runs again, and then files a change. It checks what the guide promises: one
note per item under the item's address, a second run that writes nothing, a change that
is added and never taken back, and a connection that cannot see another project.

A run against a real meetings tool, mailbox, calendar, document folder or to-do list is
not a test: it is recorded by the maintainer under private/c1/.
"""

import asyncio
import re
import sqlite3

import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from test_journey import (  # noqa: F401  (install_without_tidy is a fixture)
    SAME_ORIGIN,
    install_without_tidy,
    text_of,
)

MEETINGS = [
    {
        "id": "m-41",
        "series": "Payments weekly",
        "name": "Payments weekly",
        "date": "3 November 2026",
        "who": "Ada, Brook and Cy",
        "decided": ["Quill ships the retry fix on Thursday."],
        "owners": ["Brook owns the rollback plan, due Wednesday."],
        "open": ["Does Lumen need the same fix?"],
    },
    {
        "id": "m-42",
        "series": "Payments weekly",
        "name": "Payments weekly",
        "date": "10 November 2026",
        "who": "Ada and Brook",
        "decided": ["Lumen keeps its own retry rule."],
        "owners": [],
        "open": [],
    },
    {  # nothing decided, promised or open: the recipe writes no note
        "id": "m-43",
        "series": "Coffee",
        "name": "Coffee",
        "date": "11 November 2026",
        "who": "Ada and Dee",
        "decided": [],
        "owners": [],
        "open": [],
    },
]


def note_for(meeting: dict) -> dict | None:
    """The meetings recipe, as the guide words it: one note per meeting that settled
    something, addressed by the meeting's id."""
    if not (meeting["decided"] or meeting["owners"] or meeting["open"]):
        return None
    parts = [f"# {meeting['name']}, {meeting['date']}", f"{meeting['who']} met."]
    for heading, lines in (
        ("Decided", meeting["decided"]),
        ("Who does what", meeting["owners"]),
        ("Open", meeting["open"]),
    ):
        if lines:
            parts.append(f"## {heading}\n\n" + "\n".join(f"- {line}" for line in lines))
    return {
        "project": "meetings",
        "title": f"{meeting['name']}, {meeting['date']}",
        "topic": meeting["series"],
        "label": "meeting",
        "source_ref": f"meeting:{meeting['id']}",
        "text": "\n\n".join(parts) + "\n",
    }


def test_an_agent_files_meetings_by_the_recipe_and_reaches_nothing_else(
    install_without_tidy,  # noqa: F811
):
    # serve --no-tidy: the change note lands under a heading the page already shows, so the
    # default tidy would save another version of payments-weekly a moment later, while this
    # test, which is about the recipe and not the tidy, reads the page's versions.
    server, key = install_without_tidy
    url = server.url
    secrets_seen = [key]

    async def journey() -> None:
        async with httpx2.AsyncClient(base_url=url, timeout=30) as person:
            # The person makes one project per kind of source and a key for the agent that
            # reaches the meetings project only.
            await person.post("/login", data={"key": key}, headers=SAME_ORIGIN)
            owner = {"x-api-key": key}
            made = {}
            for name, kind in (("meetings", "meetings"), ("specs", "documents")):
                r = await person.post(
                    "/api/projects", headers=owner, json={"name": name, "kind": kind}
                )
                assert r.status_code == 201, r.text
                made[name] = r.json()["id"]
            form = {"label": "meetings-agent", "scope": "selected", "access": "write"}
            page = await person.post(
                "/keys", data={**form, "projects": [made["meetings"]]}, headers=SAME_ORIGIN
            )
            assert page.status_code == 200, page.text
            agent_key = re.search(r"<pre>([^<]+)</pre>", page.text).group(1).strip()
            secrets_seen.append(agent_key)

            http = httpx2.AsyncClient(headers={"x-api-key": agent_key}, timeout=30)
            async with (
                http,
                Client(streamable_http_client(f"{url}/mcp", http_client=http)) as agent,
            ):
                # What the agent can see is the one project, with the kind's guidance.
                projects = text_of(await agent.call_tool("projects", {}))["projects"]
                assert [(b["name"], b["kind"]) for b in projects] == [("meetings", "meetings")]

                async def run() -> list[dict]:
                    answers = []
                    for meeting in MEETINGS:
                        note = note_for(meeting)
                        if note is not None:
                            answers.append(text_of(await agent.call_tool("note", note)))
                    return answers

                first = await run()
                assert [(a["created"], a["status"]) for a in first] == [(True, "saved")] * 2
                assert {a["page"] for a in first} == {"payments-weekly"}  # one page per series

                # Running the recipe again writes nothing.
                again = await run()
                assert [(a["created"], a["status"]) for a in again] == [(False, "unchanged")] * 2
                assert [a["version"] for a in again] == [first[-1]["version"]] * 2

                # Every claim opens the meeting it came from, by the meeting's address.
                weekly = text_of(
                    await agent.call_tool(
                        "page", {"project": "meetings", "slug": "payments-weekly"}
                    )
                )
                texts = [claim["text"] for claim in weekly["claims"]]
                assert "- Quill ships the retry fix on Thursday." in texts
                thursday = weekly["claims"][texts.index("- Quill ships the retry fix on Thursday.")]
                source = text_of(
                    await agent.call_tool(
                        "evidence",
                        {
                            "project": "meetings",
                            "slug": "payments-weekly",
                            "claim_id": thursday["id"],
                        },
                    )
                )
                (span,) = source["passages"]
                assert span["text"] == "- Quill ships the retry fix on Thursday."
                assert span["source_ref"] == "meeting:m-41"
                assert span["heading_path"][-1] == "Decided"

                # The item changes at the source: the agent files the change as a statement.
                # cairn adds it; what was said before stays, each with its own source.
                change = {
                    **note_for(MEETINGS[0]),
                    "text": "# Payments weekly, 3 November 2026\n\n"
                    "The retry fix moved from Thursday to Friday.\n",
                }
                changed = text_of(await agent.call_tool("note", change))
                assert (changed["created"], changed["status"]) == (True, "saved")
                assert changed["version"] == first[-1]["version"] + 1
                after = text_of(
                    await agent.call_tool(
                        "page", {"project": "meetings", "slug": "payments-weekly"}
                    )
                )
                now = [claim["text"] for claim in after["claims"]]
                assert "- Quill ships the retry fix on Thursday." in now
                assert "The retry fix moved from Thursday to Friday." in now

                # The agent's connection reaches nothing else: the other project does not exist
                # for it, to write or to search, and it cannot make one.
                elsewhere = await agent.call_tool(
                    "note", {"project": "specs", "text": "# Spec\n\nDrift needs Go 1.22.\n"}
                )
                assert elsewhere.is_error
                more = await agent.call_tool("new_project", {"name": "mail"})
                assert more.is_error

            # Another reader of the same project finds what the agent filed; the owner's key
            # reaches both projects and the specs project is still empty.
            hits = (await person.get("/api/search?q=retry+fix+Friday", headers=owner)).json()[
                "hits"
            ]
            assert hits and hits[0]["project"] == "meetings"
            specs = (await person.get("/api/projects/specs/pages", headers=owner)).json()
            assert specs["pages"] == []

            # The audit log names the agent's key for each note, and holds none of the text.
            audit = (await person.get("/audit")).text
            assert "meetings-agent" in audit and "retry fix" not in audit

    asyncio.run(journey())
    output = server.stop()
    con = sqlite3.connect(server.db)
    try:
        refs = con.execute(
            "SELECT source_ref, COUNT(*) FROM notes GROUP BY 1 ORDER BY 1"
        ).fetchall()
        stored = "\n".join(con.iterdump())
    finally:
        con.close()
    assert refs == [("meeting:m-41", 2), ("meeting:m-42", 1)]  # the item, then its change
    for secret in secrets_seen:
        assert secret not in output and secret not in stored

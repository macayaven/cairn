"""Project kinds: what a project is for, as data.

Contract (frozen by tests/acceptance/test_u1_setup.py):
- `KINDS` maps an id to a `Kind`, in the order people are shown them: "general", "repo",
  "team", "feature", "meetings", "documents" and "personal".
- A Kind has `id`; `title` (unique among kinds); `summary` (one sentence, at most 160
  characters, ending with "."); `belongs` and `avoid` (short phrases, shown to people; only
  "general" may leave them empty); `guidance` (what an agent using such a project should write
  and leave out, at most 900 characters, always saying never to store secrets); `name_hint`
  (a suggested project name, valid as one); and `connects`: "agents" for a project used by
  agents a person signs in from (they connect with the MCP URL), "app" for one used by a
  headless service (it gets a key that reaches only that project); and `private`: True when a
  project of this kind is made private (`everyone` "none": its maker and the owners) unless
  whoever makes it says otherwise. Only "personal" is.
- Kinds are data: adding one here needs no other change. The store refuses a kind that is
  not here; a project whose kind was later removed reads as "general" (`kind_of`).

The words are read by people choosing a use case on /start and by agents deciding what to
write, so they are plain and specific. Titles, summaries and phrases hold no quote marks,
ampersands or angle brackets: they are shown in HTML as they are.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

Connects = Literal["agents", "app"]
GENERAL = "general"
_SECRETS = "Never store secrets: no keys, tokens, passwords or connection strings."


@dataclass(frozen=True, slots=True)
class Kind:
    id: str
    title: str
    summary: str
    belongs: tuple[str, ...]
    avoid: tuple[str, ...]
    guidance: str
    name_hint: str
    connects: Connects = "agents"
    private: bool = False


_ALL = (
    Kind(
        id=GENERAL,
        title="General",
        summary="Anything that does not fit another kind.",
        belongs=("facts and decisions worth keeping that fit no other kind",),
        avoid=("secrets", "what belongs in a more specific project"),
        guidance=(
            "This project holds what fits no more specific project. Write one note per durable "
            "fact or decision, each able to stand on its own, with source_ref saying where it "
            "came from. If a note is about one repository, team, AI feature or meeting series "
            f"that has its own project, write it there instead. {_SECRETS}"
        ),
        name_hint="core",
    ),
    Kind(
        id="repo",
        title="Code repository",
        summary=(
            "The engineering memory of one repository: what was decided and why, "
            "what broke and how it was fixed."
        ),
        belongs=(
            "decisions with their reasons and the options rejected",
            "errors with their root causes",
            "set-up steps and commands that worked",
            "tech debt",
            "where work stopped",
        ),
        avoid=("secrets", "progress chatter", "what the commit history already records"),
        guidance=(
            "This project is one repository's engineering memory. At the end of a task, note "
            "each decision with its reason and the options you rejected, each error with its "
            "root cause and fix, set-up steps and commands that worked, tech debt you found, "
            "and where work stopped, so the next session can pick it up. Name the files, "
            "functions and commands a note is about, and set source_ref to the commit, branch "
            "or session. Leave out progress updates and anything the commit history already "
            f"records. {_SECRETS}"
        ),
        name_hint="my-repo",
    ),
    Kind(
        id="team",
        title="Team decisions and runbooks",
        summary=(
            "What a whole team relies on: decisions, conventions, runbooks and how releases work."
        ),
        belongs=(
            "decisions with their reasons",
            "conventions the team follows",
            "runbooks, step by step",
            "how releases and on-call work",
        ),
        avoid=("secrets", "personal to-do lists", "details of a single repository"),
        guidance=(
            "This project holds what a whole team relies on. Note decisions with their reasons "
            "and who made them, the conventions the team follows, runbooks written as steps "
            "someone else can follow, and how releases and on-call work. When a decision "
            "replaces an earlier one, say which, so a reader can tell what holds now. Keep a "
            f"single repository's details in that repository's project. {_SECRETS}"
        ),
        name_hint="handbook",
    ),
    Kind(
        id="feature",
        title="Memory for an AI feature",
        summary=(
            "The facts an AI feature must keep consistent from run to run, "
            "read and written by the app itself."
        ),
        belongs=(
            "character and story bibles",
            "style rules",
            "glossaries",
            "policies the feature must follow",
        ),
        avoid=("anything about end users", "secrets", "drafts and one-off outputs"),
        guidance=(
            "This project holds the facts an AI feature must keep consistent from run to run: "
            "character and story bibles, style rules, glossaries and policies. Before you "
            "generate, recall the facts that apply. Note a fact only once it is settled, one "
            "fact per note, with source_ref naming where it was decided. Never note anything "
            f"about an end user: no names, messages or personal data. {_SECRETS}"
        ),
        name_hint="my-feature",
        connects="app",
    ),
    Kind(
        id="meetings",
        title="Meetings",
        summary=(
            "What meetings decide: decisions, action items with an owner and a date, "
            "and open questions."
        ),
        belongs=(
            "decisions and who made them",
            "action items with an owner and a date",
            "open questions",
        ),
        avoid=("secrets", "word-for-word transcripts", "small talk and personal remarks"),
        guidance=(
            "This project holds what meetings decide. Write one note per meeting, titled with "
            "its name and date: the decisions and who made them, each action item with an "
            "owner and a due date, and the questions left open. Set source_ref to the meeting "
            "or its recording. Leave out transcripts, small talk and anything personal said "
            f"in passing. {_SECRETS}"
        ),
        name_hint="meetings",
    ),
    Kind(
        id="documents",
        title="Business documents",
        summary=(
            "Policies, specifications and proposals, with the passages that matter quoted exactly."
        ),
        belongs=(
            "policies",
            "specifications",
            "proposals",
            "the passages that matter, quoted exactly",
        ),
        avoid=("secrets", "whole documents pasted in", "drafts that were never agreed"),
        guidance=(
            "This project holds a business's documents: policies, specifications and proposals. "
            "Note the passages that matter, quoted exactly, under a heading that names the "
            "document and its section, with source_ref set to the document's link or path "
            "and its version. Mark anything that is not a quotation as a summary. When a "
            f"document is replaced, say which version now holds. {_SECRETS}"
        ),
        name_hint="documents",
    ),
    Kind(
        id="personal",
        title="Personal notes",
        summary=(
            "Your own notes: what you want to remember, kept apart from the projects you share."
        ),
        belongs=(
            "things you want to remember",
            "ideas and reading notes",
            "your preferences and routines",
        ),
        avoid=("secrets", "work that others need to read", "private details about other people"),
        guidance=(
            "This project holds one person's own notes: what they want to remember, ideas, "
            "reading notes, preferences and routines. Write one note per thing to remember, "
            "in their words where you can. Keep work that others need to read in a shared "
            f"project, and keep private details about other people out. {_SECRETS}"
        ),
        name_hint="journal",
        private=True,
    ),
)

KINDS: MappingProxyType[str, Kind] = MappingProxyType({kind.id: kind for kind in _ALL})


def kind_of(kind_id: str) -> Kind:
    """The kind with this id; "general" for one that is no longer shipped."""
    return KINDS.get(kind_id) or KINDS[GENERAL]

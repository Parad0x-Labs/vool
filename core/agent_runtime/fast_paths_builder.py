from __future__ import annotations

import re
from typing import Any

from core.agent_runtime import build_request_intent
from core.agent_runtime.builder import named_file_build


def looks_like_builder_request(lowered: str) -> bool:
    text = " ".join(str(lowered or "").split()).strip().lower()
    if not text:
        return False
    # Discussing a build is not requesting one. This was the widest of the four detectors — a bare
    # `"build" in text` — so "lets discuss whether we should build an api", "how would you build a
    # bot like that" and "why did you create that file earlier" all claimed a build. The
    # instruction/deliberation decision now lives in one place for all four.
    if build_request_intent.is_deliberation(text) or build_request_intent.is_opted_out(text):
        return False
    # The build arm was a list of bare substrings headed by `"build"` and `"create"`, so every
    # sentence that DESCRIBED building claimed one: "the app we build every night is fine", "our api
    # creates a new file per request", "the script that generates reports lives in tools". The
    # instruction/deliberation decision is now the shared one; only the design+source arm below is
    # particular to this detector.
    if build_request_intent.is_build_instruction(text):
        return True
    design_markers = (
        "design",
        "architecture",
        "best practice",
        "best practices",
        "framework",
        "stack",
    )
    source_markers = (
        "github",
        "repo",
        "repos",
        "docs",
        "documentation",
        "official docs",
    )
    return any(marker in text for marker in design_markers) and any(
        marker in text for marker in source_markers
    )


def looks_like_generic_workspace_bootstrap_request(agent: Any, lowered: str) -> bool:
    text = " ".join(str(lowered or "").split()).strip().lower()
    if not text:
        return False
    bootstrap_markers = (
        "start coding",
        "start putting code",
        "start building",
        "start creating",
        "put code",
        "putting code",
        "building the code",
        "build the code",
        "initial files",
        "starter files",
        "bootstrap",
        "set up",
        "setup",
        "write the files",
        "create the files",
        "generate the files",
        "generate the code",
        "start working",
        "launch local",
        "launch localhost",
        "run locally",
    )
    target_markers = (
        "folder",
        "directory",
        "dir",
        "src/",
        "/src",
        "api/",
    )
    return bool(
        any(marker in text for marker in bootstrap_markers)
        and (any(marker in text for marker in target_markers) or bool(agent._extract_requested_builder_root(text)))
    )


def looks_like_explicit_workspace_file_request(query_text: str) -> bool:
    """Whether this turn names an explicit workspace FILE request: a write demand, a read, or a listing.

    The WRITE half is a delegate to the one typed write-demand authority
    (`core.execution.write_demand.resolve_write_demand`): a claim is only made when a target,
    content and operation actually resolve out of the sentence. The marker list this half used
    to be — bare `" create "` plus a file noun — claimed "create notes.txt containing hello"
    and then produced no typed demand, which is exactly how a literal one-file write ended up
    on the model-driven build lane (P0 simple-file-write, audit of 59aa5ee7). The READ and
    listing half keeps its markers: those admissions were measured separately and do not
    produce writes.
    """
    text = f" {' '.join(str(query_text or '').split()).strip().lower()} "
    if not text.strip():
        return False
    # A question about a past turn is not a request; decided once, with the other detectors.
    if build_request_intent.is_deliberation(text) or build_request_intent.is_opted_out(text):
        return False
    from core.execution.write_demand import resolve_write_demand

    if resolve_write_demand(text) is not None:
        return True
    # A mutation request that NAMES its file but supplies no literal content ("Edit
    # package.json and change the version to 0.5.1.") is builder-owned, not a write demand:
    # the named-file reader plus the shared mutation-intent reader restore the admission the
    # marker list used to give, without the false claims ("why did you create that file
    # earlier" is neither an instruction nor a named file).
    from core.agent_runtime.builder.named_file_build import named_build_files
    from core.agent_runtime.fast_paths_utility import _WORKSPACE_MUTATION_INTENT_RE

    if named_build_files(text) and _WORKSPACE_MUTATION_INTENT_RE.search(text):
        return True
    read_list_markers = (
        " read the whole file",
        " read the file",
        " readback",
        " read it back",
        " read it exactly",
        " list the folder contents",
        " list the directory contents",
        " list folder contents",
    )
    return any(marker in text for marker in read_list_markers)


def looks_like_exact_workspace_readback_request(query_text: str) -> bool:
    text = f" {' '.join(str(query_text or '').split()).strip().lower()} "
    text = re.sub(r"[.!?]+", " ", text)
    if not text.strip():
        return False
    return any(
        marker in text
        for marker in (
            " read the whole file back exactly ",
            " read the file back exactly ",
            " read back exactly ",
            " readback exactly ",
            " read the whole file exactly ",
        )
    )


def extract_requested_builder_root(query_text: str, *, workspace_root: str = "") -> str:
    # Drop backticks (input normalization spaces them out -- "called ` vool-todo-test `" -- which
    # would otherwise sit between the optional-quote and the path capture and defeat the patterns).
    text = " ".join(str(query_text or "").replace("`", " ").split()).strip()
    if not text:
        return ""
    stop_words = {
        "a",
        "an",
        "the",
        "and",
        "folder",
        "directory",
        "dir",
        "path",
        "workspace",
        "repo",
        "repository",
        "this",
        "that",
        "there",
        "here",
        "code",
        "files",
        # Pronouns the `\b(?:in|under|inside)\s+<path>` pattern happily swallowed: "...config.yaml
        # with three sample keys in it." resolved to a build root literally named `it`.
        "it",
        "them",
        "one",
        "ones",
        "mine",
        "yours",
    }
    patterns = (
        re.compile(
            r"\b(?:in|under|inside)\s+[`\"']?(?P<path>[A-Za-z]:[^\r\n]+?)(?=\s+(?:create|make|write|add|put|place|save|read|list|with)\b|$)",
            re.IGNORECASE,
        ),
        re.compile(r"\bnam(?:e|ed)\s+it\s+[`\"']?(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)", re.IGNORECASE),
        re.compile(r"\b(?:folder|directory|dir|path)\s+(?:called|named)\s+[`\"']?(?P<path>[A-Za-z0-9_./-]+)", re.IGNORECASE),
        re.compile(r"\b(?:called|named)\s+[`\"']?(?P<path>[A-Za-z0-9_][A-Za-z0-9_./-]*(?:/[A-Za-z0-9_./-]+)*)", re.IGNORECASE),
        re.compile(
            r"\b(?:create|make|setup|set up|bootstrap|mkdir)\s+(?:a|an|the)?\s*(?:folder|directory|dir|path)\s+(?:called|named)?\s*[`\"']?(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)",
            re.IGNORECASE,
        ),
        re.compile(r"\b(?:in|under|inside)\s+[`\"']?(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)[`\"']?", re.IGNORECASE),
    )
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        candidate = str(match.group("path") or "").strip().strip("`\"'").rstrip(".,!?")
        if not candidate:
            continue
        # A rooted path is NOT a workspace-relative one. This used to `lstrip("/")`, which turned
        # "scaffold a rust crate in /tmp/vool_qa_build5" into the workspace-relative root
        # "tmp/vool_qa_build5" and built the crate somewhere the user never named. The caller
        # treats whatever comes back as workspace-relative, so a rooted path OUTSIDE the workspace
        # has no honest answer here -- `write_root_honesty` intercepts those upstream and says why.
        #
        # Inside the workspace it does have one. A QA drive asked for a script "in
        # <workspace>/qa3"; that path is reachable, dropping it sent the build to a folder named
        # after the file instead, and the reply named that folder as though it were the one asked
        # for. Rebasing is the difference between honouring the destination and relocating silently.
        if candidate.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", candidate):
            rebased = named_file_build.workspace_relative_path(candidate, workspace_root=workspace_root)
            if not rebased:
                continue
            candidate = rebased
        candidate = candidate.lstrip("./")
        if not candidate or candidate.lower() in stop_words:
            continue
        if ".." in candidate.split("/"):
            continue
        # "called reverse.py" names the FILE, not the folder to put it in. Taken as a build root it
        # produced `reverse.py/reverse.py`, so a candidate that is a source filename is not a
        # directory and the next pattern gets its turn -- which is where the real folder was.
        if named_file_build.is_source_filename(candidate):
            continue
        return candidate
    return ""

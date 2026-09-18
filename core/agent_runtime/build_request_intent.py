"""One answer to "is this an instruction to build, or a conversation about building?"

Five separate implementations of that question existed, each with its own vocabulary, each matching
by bare substring containment, and they disagreed with each other constantly. Measured by calling
the real functions on 17 discussion sentences and 12 real build requests before this module:

    looks_like_builder_request                        fast_paths_builder.py:7
    looks_like_agentic_build_request                  fast_paths_utility.py:612
    the inline gate in _should_run_builder_controller builder_facade.py:355
    _looks_like_write_intent_request                  builder_facade.py:434
    looks_like_explicit_workspace_file_request        fast_paths_builder.py:99

    -> 31 wrong verdicts of 116. Afterwards: 0 of 116.

The fifth was not in the original list of four; it surfaced when the matrix was re-measured and one
sentence still claimed a build. `" create "` and `" file"` both appear in "why did you create that
file earlier", so a question about a past turn reached the file-writing builder.

Substring matching also produced collisions that only need a build verb elsewhere in the sentence to
fire: `app` sits inside "happens", `site` inside "opposite", `api` inside "rapid", `cli` inside
"client". So "what happens when we generate the report" carries a build noun it never mentioned.

The distinction this module draws is **instruction versus deliberation**, because that is the one
that decides whether files get written on the operator's disk:

    "build me a telegram bot"                    -> an instruction. Build.
    "lets discuss whether we should build an api" -> a deliberation. Answer, do not build.

Two conditions, both required. The sentence must not be framed as deliberation — a question, a
request to explain, a comparison, or a report of something already done. And the build verb must sit
in **imperative position**: at the start of a clause, after nothing more than a discourse or
politeness marker. "Create a cli tool" is imperative; "the tool creation process" is not, and a verb
buried mid-clause is a noun phrase far more often than a command.

Deliberately biased toward NOT building. A missed build costs one clarifying exchange; a build the
operator did not ask for writes files they did not want.
"""
from __future__ import annotations

import re
from functools import lru_cache

# Verbs whose imperative form is an instruction to produce code or files. Matched as whole words
# with explicit inflections rather than stems: `\bcreate\b` must not match "creation", and
# `\bbuild\b` must not match "buildings".
_BUILD_VERBS = (
    "build", "builds", "building", "rebuild",
    "create", "creates", "creating",
    "scaffold", "scaffolds", "scaffolding",
    "implement", "implements", "implementing",
    "generate", "generates", "generating",
    "bootstrap", "bootstraps", "bootstrapping",
    "write", "writes", "writing",
    "make", "makes", "making",
    "add", "adds", "adding",
    "set up", "setup", "setting up",
)
_BUILD_VERB_RE = re.compile(
    r"\b(?:" + "|".join(v.replace(" ", r"\s+") for v in _BUILD_VERBS) + r")\b"
)

# Things that get built. Whole words only — this is where substring matching collided with English.
#
# Two scopes, because the callers ask different questions and conflating them routed turns into the
# wrong lane. PROJECT nouns are whole deliverables: "build me a telegram bot" is a multi-step,
# model-driven build. ARTIFACT nouns add the file-and-folder work that is still a write intent but
# is NOT a project build — "create a folder called tools" belongs to the workspace tool lane, and
# sending it down the model-build lane is what broke three continuity tests here.
_PROJECT_NOUNS = (
    "app", "application",
    "api",
    "bot",
    "cli",
    "script",
    "service",
    "server",
    "site", "website",
    "tool",
    "webhook",
    "program",
    "project",
    "dashboard",
    "agent",
    "skill",
    "plugin",
)
_ARTIFACT_ONLY_NOUNS = (
    "file", "files",
    "folder", "folders", "directory", "directories",
    "module", "modules",
    "package", "packages",
    "endpoint", "endpoints",
    "code", "codebase",
    "scaffold", "scaffolding",
    "repo", "repository",
    "workspace",
    # A regression test IS an artifact this runtime can write and run -- `workspace.write_file` and
    # `workspace.run_tests` both exist. Its absence from this vocabulary meant "create the smallest
    # failing test that reproduces the corruption. Run it." matched a build VERB in imperative
    # position but no build NOUN, so no lane claimed the turn and it answered "I couldn't map that
    # cleanly to a real action" three times in a row. The one artifact a debugging turn always asks
    # for was the one missing.
    "test", "tests", "testcase", "testcases", "regression test", "unit test", "unit tests",
    "apps", "apis", "bots", "scripts", "services", "servers", "sites", "websites",
    "tools", "webhooks", "programs", "projects", "dashboards", "agents", "skills", "plugins",
)
_PROJECT_NOUN_RE = re.compile(r"\b(?:" + "|".join(_PROJECT_NOUNS) + r")\b")
_ARTIFACT_NOUN_RE = re.compile(
    r"\b(?:" + "|".join(_PROJECT_NOUNS + _ARTIFACT_ONLY_NOUNS) + r")\b"
)

# Phrases that are instructions on their own — no noun needed, and they are never questions.
_STRONG_INSTRUCTIONS = (
    "do the work yourself",
    "create all files",
    "write the files",
    "write the file",
    "create the files",
    "generate the code",
    "build the code",
    "build and verify",
    "start building",
    "start creating",
    "start coding",
    "start working",
    "start putting code",
    "put code",
    "putting code",
    "initial files",
    "starter files",
    "run the tests and fix",
    "fix any failures",
)
_STRONG_RE = re.compile(r"\b(?:" + "|".join(re.escape(p) for p in _STRONG_INSTRUCTIONS) + r")")

# The operator saying "talk, do not touch". Kept here so all five call sites honour the same words —
# previously two of them checked this list and three did not.
#
# Deliberately excludes "do not create" and "read-only". Both look like opt-outs and are not:
# "create exactly three files and do not create anything else" is a precise build instruction whose
# second clause bounds the first, and "make the field read-only" is a build instruction about a
# field. Adding them silently turned three real build requests into advice.
# The second group is an opt-out by CONSTRAINT rather than by prohibition: a turn that pins the
# whole reply to one artifact ("Output ONLY valid Python code", "Output ONLY the raw terminal
# command on one line", "Start directly with `import hashlib`") has already said the deliverable is
# the message. There is nothing left to put on disk, and scaffolding a project is not a smaller
# version of answering -- it is a different act with a side effect the turn never asked for.
#
# Measured across four consecutive blind sets on 2026-08-17: every codegen prompt of this shape was
# claimed by the builder, answered with a workspace FILE LISTING instead of the requested code, and
# wrote a directory into the repository. The project name came from an arbitrary noun in the prompt
# -- `logs/` from "a table named logs", `my-app/` from a Docker image name, and `markdown/` from the
# NEGATIVE constraint "Do NOT wrap it in markdown code fences". One of those runs also failed an
# unrelated `ops/verify.py` lint gate on its own leftover files.
_OPT_OUT = (
    "don't write", "do not write", "dont write",
    "don't edit", "do not edit", "do not edit files",
    "advice only", "just plan", "plan only", "no files", "without writing",
    "output only", "output just", "print only", "print just",
    "reply with only", "respond with only", "start directly with",
)
_OPT_OUT_RE = re.compile(r"(?:" + "|".join(re.escape(p) for p in _OPT_OUT) + r")")

# Deliberation: the operator is asking, comparing, or reporting — not instructing.
#
# `can/could/should/shall we` and `should i` are deliberative ("can we create a new skill?").
# `can/could/would you` is a polite imperative in chat ("can you build me a bot") and is NOT
# vetoed — it is listed among the imperative leads below instead. An interrogative lead word
# outranks both, so "how would you build a bot" is deliberation even though it contains "would you".
#
# Split into two tuples because one caller outside this module needs the JUDGEMENT half on its own
# (see `is_advice_question`). The union below is what deliberation has always meant, unchanged.
_ADVICE_SEEKING = (
    r"\bis\s+it\s+(?:worth|possible|better|a\s+good\s+idea|ok|okay|safe|wise|sensible)\b",
    r"\bwould\s+it\s+(?:be|make)\b",
    r"\bdoes\s+it\s+make\s+sense\b",
    r"\b(?:a|any)\s+good\s+idea\s+to\b",
    r"\bshould\s+(?:i|we)\s+(?:use|create|make|build|add|write|generate|set\s+up|bother)\b",
    r"\bshould\s+you\b",
    r"\bdo\s+you\s+think\b",
    r"\bany\s+(?:advice|thoughts|opinions?|ideas|suggestions)\b",
    r"\bpros\s+and\s+cons\b",
    r"\bworth\s+(?:it|doing|building|creating)\b",
    r"\bthinking\s+(?:about|of)\b",
    r"\bopinion\s+on\b",
)
_DELIBERATION = (
    r"^\s*(?:how|what|why|when|where|which|whether|who)\b",
    r"\blets?\s+(?:discuss|talk|think|consider|chat|brainstorm|figure|plan)\b",
    r"\blet['’]s\s+(?:discuss|talk|think|consider|chat|brainstorm|figure|plan)\b",
    r"\b(?:can|could|shall|should|would|might|do|does|did|will)\s+(?:we|i)\b",
    r"\b(?:explain|describe|clarify|summari[sz]e)\b",
    r"\btell\s+me\b",
    r"\bwalk\s+me\s+through\b",
    r"\b(?:vs\.?|versus)\b",
    r"\balready\s+(?:built|created|generated|implemented|scaffolded|made|wrote|written)\b",
    r"\bwhy\s+did\s+you\b",
    r"\bwhat\s+(?:do|would|should|does|is|are|if|happens)\b",
)
_ADVICE_SEEKING_RE = re.compile("|".join(_ADVICE_SEEKING))
_DELIBERATION_RE = re.compile("|".join(_DELIBERATION + _ADVICE_SEEKING))

# What may sit between the start of a clause and the verb without the verb ceasing to be imperative.
# "please build it", "ok now create the api", "go ahead and scaffold it", "can you build me a bot",
# "i want you to generate the code" are all instructions.
_IMPERATIVE_LEAD = (
    r"(?:"
    r"ok(?:ay)?|now|then|so|also|next|first|finally|please|pls|just|quickly|actually|"
    r"go\s+ahead(?:\s+and)?|(?:start|begin)(?:\s+by)?|"
    r"(?:can|could|would|will)\s+you(?:\s+please)?|"
    r"you\s+(?:can|should|must|need\s+to)|"
    r"i\s+(?:want|need|would\s+like)(?:\s+you)?\s+to|"
    r"we\s+(?:need|have)\s+to|"
    r"lets|let['’]s|"
    r"and|but"
    r")"
)
# A clause starts at the beginning of the text or after a separator; the verb may be preceded by any
# run of the leads above.
_IMPERATIVE_VERB_RE = re.compile(
    r"(?:^|[.;:,!?\n]|\bthen\b|\band\b)\s*"
    r"(?:" + _IMPERATIVE_LEAD + r"[\s,]+)*"
    + _BUILD_VERB_RE.pattern
)

# --------------------------------------------------------------------------------------
# What the verb is aimed at
# --------------------------------------------------------------------------------------
# The noun test above asks whether a build noun appears ANYWHERE in the sentence, which reads a
# topic as a deliverable. Measured live on 2026-08-11 against `qwen2.5:7b`:
#
#     "Write a one-sentence description of my app Lumen, a note-taking app."
#
# `write` is a build verb in imperative position and `app` is a PROJECT noun, so
# `is_build_instruction(scope="project")` said yes. `turn_frontdoor` then handed the turn to the
# builder (`looks_like_agentic_build_request`, turn_frontdoor.py:691) and
# `_should_run_builder_controller` bypassed the task-class gate on the same verdict -- even though
# `core/task_router.classify` had already called it `creative_ideation`. The operator got
# `workspace.write_file`, generated paths, a mutation approval prompt and finally "I can't build
# with qwen2.5:7b on this turn". Nothing about that sentence asked for a file.
#
# The word `app` is there, but it is inside "of my app Lumen" -- a prepositional phrase modifying
# "description". The deliverable is the DESCRIPTION. A build noun that sits in a modifier is what
# the request is ABOUT, not what it asks to be produced.
#
# So the object of the verb gets a vote. When the head of the direct object is a piece of prose --
# a description, a haiku, an email, three names -- the sentence is content generation and cannot
# claim a build lane, whatever nouns its modifiers carry. This is a VETO and nothing else: a
# non-prose object leaves every existing verdict exactly as it was, so a request that legitimately
# builds is untouched.
_CONTENT_HEAD_NOUNS = frozenset(
    {
        "description", "descriptions", "summary", "summaries", "synopsis", "abstract",
        "paragraph", "paragraphs", "sentence", "sentences", "line", "lines", "one-liner",
        "blurb", "blurbs", "tagline", "taglines", "slogan", "slogans", "motto", "mottos",
        "headline", "headlines", "caption", "captions", "title", "titles", "subject",
        "haiku", "haikus", "poem", "poems", "poetry", "limerick", "limericks", "sonnet",
        "rhyme", "rhymes", "riddle", "riddles", "joke", "jokes", "song", "songs", "lyrics",
        "story", "stories", "essay", "essays", "article", "articles", "speech", "speeches",
        "email", "emails", "e-mail", "e-mails", "letter", "letters", "reply", "replies",
        "message", "messages", "greeting", "greetings", "apology", "apologies",
        "pitch", "pitches", "bio", "bios", "intro", "introduction", "introductions",
        "name", "names", "idea", "ideas", "suggestion", "suggestions", "option", "options",
        "explanation", "explanations", "answer", "answers", "response", "responses",
        "review", "reviews", "testimonial", "testimonials", "quote", "quotes",
        "tweet", "tweets", "ad", "ads", "advert", "adverts", "recipe", "recipes",
        # Marketing prose asked for by its trade name. "write me copy for my startup" reached this
        # module with `copy` as its object head and no entry to match, and survived only because
        # `startup` happens to be absent from the build vocabulary -- an accident, not a verdict.
        "copy", "wording", "text", "byline", "bylines",
    }
)

# Where the direct object ends. Everything past one of these words modifies the object rather than
# being it: "a description OF my app", "three names FOR my app", "a haiku ABOUT rain", "an email
# THANKING alice". Closed lists, because a bare `\w+ing` boundary would cut "the smallest FAILING
# test" short -- and a wrongly-cut object simply produces no veto, which is the safe direction.
_OBJECT_BOUNDARY_WORDS = frozenset(
    {
        "of", "for", "about", "to", "into", "in", "on", "at", "from", "with", "without",
        "by", "as", "per", "regarding", "concerning", "around", "over", "under",
        "that", "which", "who", "whose", "where", "when", "while", "because", "so",
        "and", "or", "but", "then", "plus",
    }
)
_OBJECT_BOUNDARY_PARTICIPLES = frozenset(
    {
        "containing", "saying", "describing", "explaining", "summarising", "summarizing",
        "covering", "thanking", "introducing", "announcing", "listing", "showing",
        "telling", "asking", "inviting", "congratulating", "apologising", "apologizing",
        "using", "based", "written", "called", "named", "titled",
    }
)
# Between the verb and its object: an indirect object and the ordinary noun-phrase furniture.
_OBJECT_SKIPPABLE = frozenset({"me", "us", "him", "her", "them", "you", "myself", "please"})

# Words that can only OPEN a noun phrase. Meeting one after the object has already started means a
# second noun phrase began, and English does not put two determiners in one phrase -- so the object
# ended at the token before it.
#
# This is the boundary the reported defect needed and the preposition list could not supply.
# "write a one-sentence description of my app thunder" ends its object at `of`; drop the
# preposition, as an operator typing quickly does, and "write a one-sentence description my super
# app thunder" has no function word left to stop on. The run swallowed the appositive and the head
# came out `app`. `my` is the stop: "my super app Thunder" is a fresh phrase glossing the topic.
_OBJECT_PHRASE_STARTERS = frozenset(
    {"a", "an", "the", "my", "our", "your", "their", "his", "its", "this", "these", "those",
     "another", "every"}
)

# A finite verb opens a new clause and takes the noun immediately before it as its SUBJECT, so that
# noun belongs to the clause rather than to the object: "write product description app is thunder"
# asks for a product description and then states what the app is. Cutting at the verb alone would
# leave `app` as the head and put the defect straight back, so the subject goes with it.
_CLAUSE_VERBS = frozenset(
    {"is", "isnt", "are", "arent", "was", "were", "be", "will", "would", "can", "cant", "could",
     "should", "shall", "might", "must", "does", "do", "did", "has", "have", "had", "needs", "need"}
)


def _within_one_edit(token: str, word: str) -> bool:
    """True when ``token`` is ``word`` with one character inserted, deleted or substituted."""

    if abs(len(token) - len(word)) > 1:
        return False
    if len(token) == len(word):
        diffs = [i for i, (a, b) in enumerate(zip(token, word, strict=True)) if a != b]
        return len(diffs) <= 1
    shorter, longer = (token, word) if len(token) < len(word) else (word, token)
    i = 0
    for j, char in enumerate(longer):
        if i < len(shorter) and shorter[i] == char:
            i += 1
        elif j - i:  # a second skip
            return False
    return True


@lru_cache(maxsize=2048)
def _is_content_head(token: str) -> bool:
    """True when the object head is a piece of prose, spelling mistakes included.

    An operator typing fast produces `descrption` and `sentance`, and a vocabulary that only matches
    clean spellings hands those turns to the builder -- the same wrong lane, reached by a different
    accident. One edit is the whole allowance, and only from six characters up: `app`, `api`, `bot`
    and `cli` are three and four characters, so no build noun can drift into this set. The
    `test_no_build_noun_is_one_typo_from_prose` check holds that line against future vocabulary.
    """

    if token in _CONTENT_HEAD_NOUNS:
        return True
    if len(token) < 6:
        return False
    return any(_within_one_edit(token, word) for word in _CONTENT_HEAD_NOUNS if len(word) >= 6)


# A file the operator NAMED is the most concrete deliverable there is, and it outranks the prose
# reading: "write a description of my app into notes.txt" is a write however the object is phrased.
# The extension vocabulary lives here rather than in `builder/named_file_build.py` because that
# module already imports this one, and a second private copy of a shared vocabulary is the exact
# defect this module was created to remove.
SOURCE_EXTENSIONS = (
    "py", "pyi", "js", "mjs", "cjs", "ts", "tsx", "jsx", "rs", "go", "rb", "java", "kt", "swift",
    "c", "h", "cpp", "hpp", "cs", "php", "sh", "bash", "zsh", "sql", "css", "scss", "html",
    "md", "txt", "json", "yaml", "yml", "toml", "ini", "cfg", "env",
)
_FILENAME_RE = re.compile(
    r"(?<![\w`'\"])(?:~|[A-Za-z]:[\\/]|/)?[\w][\w./\\-]*\.(?:" + "|".join(SOURCE_EXTENSIONS) + r")\b",
    re.IGNORECASE,
)
# A destination stated in words rather than as a path. Deliberately narrow -- file, folder, disk --
# because "in the workspace" and "in this project" are how ordinary build requests are phrased, and
# widening this to them would hand the override to every sentence that mentions the repo.
_EXPLICIT_WRITE_TARGET_RE = re.compile(
    r"\b(?:to|into|in|inside|under)\s+(?:a|an|the|this|that|my|our)?\s*"
    r"(?:file|files|folder|folders|directory|directories|subfolder|subdirectory)\b"
    r"|\bsave\s+(?:it|this|that|them|these)?\s*(?:as|to|into|in)\b"
    r"|\b(?:on|to)\s+disk\b"
)


def names_a_file(text: str) -> bool:
    """True when the text names a concrete file by path or filename."""

    return bool(_FILENAME_RE.search(str(text or "")))


def _normalise(text: str) -> str:
    return " ".join(str(text or "").split()).strip().lower()


def _object_heads(sentence: str) -> list[str]:
    """The head noun of the direct object of each imperative build verb in ``sentence``.

    English noun phrases are head-final, so the head is the LAST word of the object rather than the
    first. That distinction is load-bearing: "write me an app description" has `app` in it as a
    modifier and `description` as the thing asked for, and taking the first noun would read it as a
    build.

    Head-final only holds inside ONE phrase, which is why the object has to be cut at the phrase it
    actually ends on. Three things end it, in the order English uses them: a preposition or
    participle handing over to a modifier, a determiner opening a second phrase, and a finite verb
    opening a clause. The first alone was the defect -- sloppy typing omits prepositions and nothing
    else stopped the run.
    """

    heads: list[str] = []
    for match in _IMPERATIVE_VERB_RE.finditer(sentence):
        tokens: list[str] = []
        for raw in sentence[match.end():].split():
            token = raw.strip(".,;:!?\"'()[]{}`").lower()
            if not token:
                continue
            if token in _OBJECT_BOUNDARY_WORDS or token in _OBJECT_BOUNDARY_PARTICIPLES:
                break
            if tokens and token in _CLAUSE_VERBS:
                # The noun before a finite verb is that verb's subject, not part of the object.
                tokens.pop()
                break
            if not tokens and token in _OBJECT_SKIPPABLE:
                continue
            if tokens and token in _OBJECT_PHRASE_STARTERS:
                break
            tokens.append(token)
        if tokens:
            heads.append(tokens[-1])
    return heads


def _is_content_generation(sentence: str, *, whole_text: str) -> bool:
    """True when every build verb in ``sentence`` is aimed at a piece of prose.

    "write a description and create notes.txt" is not content generation -- one of its two objects
    is a file -- so the veto needs ALL of them to be prose before it fires.
    """

    if names_a_file(whole_text) or _EXPLICIT_WRITE_TARGET_RE.search(whole_text):
        return False
    heads = _object_heads(sentence)
    return bool(heads) and all(_is_content_head(head) for head in heads)


def _sentences(text: str) -> list[str]:
    """Split so an instruction is not vetoed by a question sitting beside it.

    "build me a bot, then tell me how it works" is an instruction with a question attached, and the
    instruction is the part that decides whether files are written.
    """

    parts = [
        p.strip()
        for p in re.split(
            r"(?<=[.!?])\s+"                       # sentence end
            r"|;"                                   # hard clause break
            r"|,\s*(?:then|and\s+then|after\s+that|also|next)\b"  # "build a bot, then tell me..."
            # Contrast inside an object ("small but complete bot") is not a
            # clause boundary. Split only when the right side starts a clause.
            r"|\b(?:but|however)\b\s*,?\s*(?="
            r"(?:how|what|why|when|where|which|whether|who|do|don't|explain|describe|tell)\b|"
            r"(?:" + _IMPERATIVE_LEAD + r"[\s,]+)*" + _BUILD_VERB_RE.pattern + r")",
            text,
        )
        if p and p.strip()
    ]
    return parts or ([text] if text else [])


def is_deliberation(text: str) -> bool:
    """True when the operator is discussing building rather than asking for it."""

    return bool(_DELIBERATION_RE.search(_normalise(text)))


# A first-person INTENTION lead: the operator stating what they mean to build, not telling the
# runtime to build it. The imperative leads above accept "i want to build" because, standing alone,
# it is how people ask ("i want to build a telegram bot" -> build). Followed by a question about the
# design, the same words are the SUBJECT of a discussion, and that is decided over the whole message
# (`intention_under_deliberation`). "i want YOU to build" stays the instruction it is.
_INTENTION_LEAD_RE = re.compile(
    r"\b(?:i|we)\s+(?:want|need|would\s+like|have)\s+to\s+" + _BUILD_VERB_RE.pattern + r"$"
)


def is_intention_statement(sentence: str) -> bool:
    """True when every build verb in imperative position in ``sentence`` is led by a first-person
    intention ("i want to build ...", "we need to create ...") rather than by a request addressed
    to the runtime ("build ...", "please create ...", "i want you to build ...")."""

    matches = list(_IMPERATIVE_VERB_RE.finditer(_normalise(sentence)))
    return bool(matches) and all(_INTENTION_LEAD_RE.search(match.group(0).strip()) for match in matches)


def deliberates(text: str) -> bool:
    """Whether any sentence of the message is framed as deliberation (per sentence, the same law
    `imperative_build_sentences` applies, so a mid-message "How would you design this?" counts)."""

    return any(_DELIBERATION_RE.search(sentence) for sentence in _sentences(_normalise(text)))


def intention_under_deliberation(sentence: str, *, whole_text: str) -> bool:
    """A first-person intention in a message that deliberates about it is not an instruction.

    Measured 2026-09-16 on candidate 035dea9b: a design brief opened with "I want to build a small
    community bot system with a World of Warcraft theme." and closed with "How would you design this
    system? Explain the architecture ...". Deliberation was judged sentence by sentence, the opener
    was an imperative build on its own, and the builder took the turn -- files were planned, the
    pinned paid model was asked to generate them, and the reply was a build refusal naming a local
    workspace path. The opener is the subject of the question that follows it. Alone, or followed by
    an instruction ("do it"), the same opener is still the request it always was.
    """

    return is_intention_statement(sentence) and deliberates(whole_text)


def is_advice_question(text: str) -> bool:
    """True when the operator is asking whether something is a GOOD IDEA, not asking to see it.

    The judgement-seeking half of deliberation, exposed on its own because the whole list cannot be
    used as a veto by anything that also answers descriptive questions. The folder-overview fast
    path answered "is it a good idea to create a Makefile for this project?" with a directory
    listing of the project; vetoing on `is_deliberation` there would have taken "explain the local
    folder we are in" and "what should i look at in this repo" down with it, since `explain` and
    every interrogative lead are deliberation too. This subset leaves those alone.
    """

    return bool(_ADVICE_SEEKING_RE.search(_normalise(text)))


def is_opted_out(text: str) -> bool:
    """True when the operator explicitly said not to write anything."""

    return bool(_OPT_OUT_RE.search(_normalise(text)))


def imperative_build_sentences(text: str) -> list[str]:
    """The sentences carrying a build verb in imperative position, deliberation and opt-out removed.

    Exposed so a caller that supplies its OWN noun test can reuse this module's decision about what
    counts as an instruction, instead of re-deriving it. ``builder/named_file_build.py`` is the first
    such caller: a concrete filename is the noun there, and re-implementing the clause analysis is
    how this module came to have five disagreeing copies in the first place.
    """

    normalised = _normalise(text)
    if not normalised or is_opted_out(normalised):
        return []
    if _supplies_payload_for_analysis(text):
        # Quoted instructions, code, examples and requirements are DATA inside the request, not
        # independent commands: a pasted "build me an app" inside a review ask instructs nothing.
        return []
    return [
        sentence
        for sentence in _sentences(normalised)
        if not _DELIBERATION_RE.search(sentence)
        and not _is_content_generation(sentence, whole_text=normalised)
        and _IMPERATIVE_VERB_RE.search(sentence)
        and not intention_under_deliberation(sentence, whole_text=normalised)
    ]


def _supplies_payload_for_analysis(text: str) -> bool:
    """Whether this turn pastes the very content it asks about (``core.inline_payload``).

    One authority, reused: the same detector the file, audit and Hive lanes honour. A turn that
    supplies a payload and asks an analysis question is never a build instruction, whatever
    verbs its payload carries.
    """
    from core.inline_payload import turn_supplies_its_own_content

    return turn_supplies_its_own_content(text)


def is_build_instruction(text: str, *, scope: str = "artifact") -> bool:
    """True only for an instruction to produce code or files.

    A question about building, a comparison of approaches, a request to explain how something would
    be built, or a report that something was already built all return False.

    `scope="project"` narrows the noun to a whole deliverable — an app, a bot, a service — which is
    what the model-driven build lane is for. `scope="artifact"` also accepts file and folder work,
    which is a write intent but belongs to the workspace tool lane.
    """

    normalised = _normalise(text)
    if not normalised:
        return False
    if is_opted_out(normalised):
        return False
    if _supplies_payload_for_analysis(text):
        # The quoted/fenced payload is data under analysis, not a command to execute.
        return False
    if scope == "project":
        # A request that names its deliverable's FILE FORM ("a single HTML file", "one
        # complete index.html") names a whole deliverable, whatever product noun it uses: the
        # shared register recognizes the form, so the model-driven build lane claims "Build me
        # a task board as a single HTML file" (measured live 2026-09-17: it matched only the
        # artifact scope, the chat lane took it, and the one-shot ceiling could not hold the
        # artifact -- the lane designed for whole files is the builder).
        try:
            from core.agent_runtime.grounded_mode import (
                _FILE_FORM_ARTIFACT_RE,
                _FILE_FORM_REQUEST_RE,
            )

            if _FILE_FORM_REQUEST_RE.search(text) and _FILE_FORM_ARTIFACT_RE.search(text):
                return True
        except Exception:
            pass
    noun_re = _PROJECT_NOUN_RE if scope == "project" else _ARTIFACT_NOUN_RE

    project_scope = scope == "project"
    for sentence in _sentences(normalised):
        if _DELIBERATION_RE.search(sentence):
            continue
        # The verb is aimed at prose. "Write a one-sentence description of my app Lumen" carries a
        # project noun and asks for a sentence; honouring the noun over the object is what routed it
        # into `workspace.write_file`.
        if _is_content_generation(sentence, whole_text=normalised):
            continue
        # "I want to build X. How would you design it?" -- the opener names the subject of the
        # question, not a job (see `intention_under_deliberation`).
        if intention_under_deliberation(sentence, whole_text=normalised):
            continue
        # A strong phrase names the work without naming a deliverable, so it cannot decide PROJECT
        # scope: "start putting code in there" is bootstrap work in a folder, and letting it claim
        # the model-driven build lane routed "create a folder called tools and start putting code in
        # there" away from the workspace tools that handle it.
        if not project_scope and _STRONG_RE.search(sentence):
            return True
        if _IMPERATIVE_VERB_RE.search(sentence) and noun_re.search(sentence):
            return True
    return False


# A test is an artifact this runtime writes and runs, but it is not a PRODUCT — there is no app,
# bot or service to scaffold. Adding `test` to the artifact nouns made `is_build_instruction` say
# yes, which routed the turn to the builder, which then refused with "I do not have a real bounded
# builder path" because it only knows starters and bot scaffolds. That converted one refusal into
# a different one. Measured 2026-07-31 on "Prove the bug you identified before fixing it. Create
# the smallest deterministic regression test that reproduces the failure."
#
# The prove-it-before-you-fix-it loop is the single best defence against a model that invents bugs
# — twice this session a cloud model produced a confidently-cited defect that did not exist, and
# one of its "fixes" made every archive undecompressable. A fabricated bug cannot survive being
# asked to reproduce itself, so this request must reach a lane that can write a file and run it.
_TEST_ARTIFACT_RE = re.compile(
    r"\b(?:regression|failing|unit|smallest|deterministic|reproduc\w*)\s+\w*\s*tests?\b"
    r"|\btests?\s+(?:that|which)\s+reproduc\w*"
    r"|\breproduc\w+\s+(?:the\s+)?(?:bug|failure|corruption|defect|issue)\b",
    re.IGNORECASE,
)


def looks_like_test_artifact_request(text: str) -> bool:
    """A request to WRITE a test (and usually run it), rather than to scaffold a product.

    Deliberately narrow: it must be an imperative build instruction AND name a test-shaped
    artifact. "how do I write tests for this?" is a question and `is_build_instruction` already
    rejects it, so this cannot turn a discussion into a file write.
    """

    normalised = _normalise(text)
    if not normalised or not _TEST_ARTIFACT_RE.search(normalised):
        return False
    return is_build_instruction(text)


# A request that states its OWN command behavior. Measured across the owner's beta
# (finalbot, pocketbot, stashbot): every scaffold request spelled its commands
# ("/start -> `Ready`", "/save <word>", "/balance"), and the platform TEMPLATE answered
# each one with the same generic /start + /help + echo bot that implements none of them.
# A template cannot satisfy a stated spec; the detector below is what lets the routing
# hand such a request to the lane that generates from the request instead.
_COMMAND_ARROW_RE = re.compile(
    r"/[A-Za-z_][A-Za-z0-9_]*\s*(?:->|=>|\u2192|>\s|\u2014\s*>|-\s*>)",
    re.IGNORECASE,
)
_COMMAND_ARGUMENT_RE = re.compile(
    r"/[A-Za-z_][A-Za-z0-9_]*\s+<[a-zA-Z][^<>]{0,40}>",
    re.IGNORECASE,
)


def request_specifies_command_behavior(text: str) -> bool:
    """True when the text states concrete bot/command behavior a template cannot satisfy.

    Two shapes, both from real requests: a command with a reply or argument placeholder
    ("/start -> `Ready`", "/save <word>") and an argument-taking command named by itself
    ("/add <positive integer>"). A lone platform mention ("a telegram bot for reminders")
    names no command and keeps every existing route.
    """

    cleaned = str(text or "")
    if not cleaned:
        return False
    return bool(_COMMAND_ARROW_RE.search(cleaned) or _COMMAND_ARGUMENT_RE.search(cleaned))

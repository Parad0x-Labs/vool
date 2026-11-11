# Scenario library

One YAML file per lane. The runner (`ops/acceptance/run_acceptance.py`) loads every file here,
refuses any file that breaks the format below, and turns each scenario into one case per phrasing.
Rules for what a scenario must cover are in [../STANDARD.md](../STANDARD.md).

```yaml
lane: memory
scenarios:
  - id: MEM-CROSS-01                 # unique, never reused for a different scenario
    title: Saved code is found from a new chat
    from_bug: "sls 2026-11-11: answered 'not listed in the provided context'"   # when a person found it
    profile: any                     # any = safe on the person's real profile; default throwaway
    needs: [email_account]           # what the profile must have; otherwise the case is BLOCKED
    vars:                            # fresh value per case: # digit, @ letter, pick:a|b|c, date:+N
      code: "@@-####"
    min_phrasings: 3                 # default 3; lower only for a screen-only check, with a note
    steps:
      - chat: A                      # a label; the first use of a label opens a new chat
        project: "Trip ${code}"      # optional: open that chat inside a (throwaway) project
        say: "remember my hotel booking code is ${code}"
        grade: false                 # setup turn: recorded, not graded
      - chat: B
        say:                         # several phrasings: one case per phrasing
          - "whats my hotel booking code?"
          - "hotel code pls"
          - "what was the booking code for my hotel again"
        approve: deny                # approval cards: deny (default) or once
        allow_send: false            # an email send / post / payment is denied unless true; never on the real profile
        stop_after: 5                # press Stop after N seconds
        restart_after: 8             # stop and restart the throwaway VOOL after N seconds (needs --launch)
        plain_allow: [path]          # banned-word ids this answer may contain (see PLAIN_BANNED in the runner)
        expect:
          all: ['${code}']           # every regex must match the answer
          any: []                    # at least one must match
          none: ['(?i)provided context']   # none may match
          helpful_any: ['(?i)other chat']  # helpfulness: at least one must match
          clarify_ok: false          # true when a question back is the right answer
          max_seconds: 120           # slower than this fails "helpful"
          review: "says which chat it came from"   # a reviewer decides helpfulness (REVIEW until then)
          ui:                        # checks on the page after the turn
            - selector: "#sessions .session.active .s-title"
              text_none: ['^New chat$']
      - chat: A
        action: reload               # reload | open_chat | archive_chat | restart_daemon
        expect: { ui: [ { selector: "#log .msg.user", count_min: 1 } ] }
```

Inside regexes, `${var}` matches the value literally, and a `-` in it also matches a space or
nothing (people and models write codes both ways).

Every answer is also checked against the runner's plain-wording list ("provided context", tool
names, JSON, error traces, file paths, internal ids). A scenario does not need to repeat those.

## Adding a bug someone found by hand

1. Add a scenario with `from_bug` quoting what they saw and when.
2. Put their exact words in as one phrasing, plus at least two new ones (typos, short, follow-up).
3. Add the not-found or after-forget twin when the bug is about recall.
4. Run it on the old build (it must fail) and on the fix (it must pass). Keep both tables.

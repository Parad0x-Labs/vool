# Skill Codex signer

This repository runs the Skill Codex signer for VOOL. Every few minutes a GitHub Actions job checks new
skill uploads, signs the ones that pass and refuses the rest with the exact reasons, and keeps the
signed list fresh. Once a day it runs every listed skill through the checks again.

- Uploads are never stored here. Only the signed list and its history are.
- `authority/state.json` is the signed list, the marks and the VOOL builds it is signed for.
- `authority/private.bin` is sealed. It holds the review's verdicts, its counts and its spending.
- `team-steps.txt` holds the team's hand steps: the Tested mark, the Highlighted spot, suspending a
  creator and removing a skill.
- The checks themselves are VOOL's own, from the commit named in `authority/vool-commit`.

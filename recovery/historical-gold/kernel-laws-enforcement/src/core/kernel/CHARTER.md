# Experiment charter — kernel laws PoC

- **Owner:** Fable5 innovation lane (operator: sls_0x)
- **Purpose:** prove the four kernel invariants from docs/VOOL_TESSERACT_PROGRAM_2026-08-19.md
  Addendum 2 are implementable and adversarially testable as standalone modules:
  Law 1 answers commit (obligations) · Law 2 claims are typed (evidence) ·
  Law 3 forks hold capabilities (+ taint) · Law 4 everything replays (effects).
- **Parent SHA:** 618ea93468634d8f28ae3fee85098600ce801fce (frozen human-test build)
- **Retirement condition:** after the PoC verdict is recorded (WORTH CONTINUING report) —
  delete if NO; if YES, keep until the re-implementation lands on a real lane, then delete.
- **Rules in force:** no push / no rebase / no reset / no amend; local commits only;
  never touches wt-obligation-floor or VOOL.app.

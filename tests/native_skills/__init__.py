# Packaged so this directory conftest keeps a unique dotted module name (tests.<dir>.conftest)
# in single-process multi-directory runs; without it, conftests from directories lacking
# __init__.py compete for the bare module name "conftest" and the losers load silently
# without their fixtures (observed on pytest 9.1 / Linux CI shards, 2026-09-20).

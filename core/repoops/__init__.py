"""RepoOps: the complete repository-operations surface Git Ninja orchestrates.

Layering (target law: FORGE/WORKSPACE/GIT own semantics; PLATFORM owns
execution authority and effect truth):

    identity.py   RepositoryWorkspace — WHICH checkout/repo, exactly
    wsfs.py       WorkspaceFS — typed file ops behind path authority
    localgit.py   LocalGit — typed git operations (fetch is NOT pull)
    ci.py         CI truth bound to an exact SHA
    evidence.py   RepositoryEvidence — one frozen bundle all council shares

Every mutation is admitted by core.platform.broker.ExecutionBroker; nothing
here records effect truth itself.
"""

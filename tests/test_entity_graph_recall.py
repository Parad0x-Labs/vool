"""1-hop entity-graph expansion in recall.

After ranking, recall pulls in nodes LINKED from the top hits (downweighted) so
directly-connected context surfaces even if its own similarity fell below min_score.
Bounded to one hop with a capped fan-out + total and a seen-set — no cycles, no blowup.

Uses explicit orthogonal vectors (not the embedder) so the expansion behaviour is
isolated and deterministic: only the linker passes the cosine filter; the linked
node is pulled purely through the graph edge.
"""
from __future__ import annotations

from core.vool_memory import VoolMemory

Q = [1.0, 0.0, 0.0, 0.0]   # query: cosine 1 with e0, 0 with the others


def _store(mem, name, vec):
    return mem.node_store(content=name, keywords=[name], tags=["eg"],
                          context_description=name, embedding=[float(x) for x in vec],
                          linked_node_ids=[])


def test_linked_node_surfaces_just_below_its_linker(tmp_path):
    mem = VoolMemory(runtime_home=tmp_path, agent_id="eg1")
    a = _store(mem, "alpha", [1, 0, 0, 0])   # cosine(Q, a) = 1.0  -> passes
    b = _store(mem, "beta", [0, 1, 0, 0])    # cosine(Q, b) = 0.0  -> fails min_score
    mem.node_update_links(a.node_id, [b.node_id])  # A -> B
    try:
        hits = mem.node_search(Q, top_k=3, min_score=0.5)
        ids = [h[0].node_id for h in hits]
        assert a.node_id in ids, "the direct hit A ranks"
        assert b.node_id in ids, "B (linked from A) surfaces via 1-hop expansion despite cosine 0"
        assert ids.index(a.node_id) < ids.index(b.node_id), "the linked node rides in below its linker"
    finally:
        mem.close()


def test_unlinked_nonmatching_node_does_not_surface(tmp_path):
    mem = VoolMemory(runtime_home=tmp_path, agent_id="eg2")
    a = _store(mem, "alpha", [1, 0, 0, 0])
    b = _store(mem, "beta", [0, 1, 0, 0])
    c = _store(mem, "gamma", [0, 0, 1, 0])
    mem.node_update_links(a.node_id, [b.node_id])  # only A -> B; C is unlinked
    try:
        ids = [h[0].node_id for h in mem.node_search(Q, top_k=3, min_score=0.5)]
        assert a.node_id in ids and b.node_id in ids
        assert c.node_id not in ids, "expansion pulls LINKED nodes only, not unrelated ones"
    finally:
        mem.close()


def test_mutual_links_do_not_loop(tmp_path):
    mem = VoolMemory(runtime_home=tmp_path, agent_id="eg3")
    a = _store(mem, "alpha", [1, 0, 0, 0])
    b = _store(mem, "beta", [0, 1, 0, 0])
    mem.node_update_links(a.node_id, [b.node_id])
    mem.node_update_links(b.node_id, [a.node_id])  # mutual link
    try:
        ids = [h[0].node_id for h in mem.node_search(Q, top_k=3, min_score=0.5)]  # returns, no hang
        assert a.node_id in ids and b.node_id in ids
        assert len(ids) == len(set(ids)), "no duplicate nodes from the cycle"
    finally:
        mem.close()


def test_expansion_fanout_is_bounded(tmp_path):
    mem = VoolMemory(runtime_home=tmp_path, agent_id="eg4")
    a = _store(mem, "alpha", [1, 0, 0, 0])
    linked = [_store(mem, f"note{i}", [0, 1, 0, 0]) for i in range(10)]  # all cosine 0
    mem.node_update_links(a.node_id, [n.node_id for n in linked])  # A -> 10 nodes
    try:
        hits = mem.node_search(Q, top_k=3, min_score=0.5)
        # A is the only direct hit; expansion adds at most _LINK_FANOUT linked nodes
        assert len(hits) <= 1 + VoolMemory._LINK_FANOUT
    finally:
        mem.close()

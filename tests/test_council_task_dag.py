"""Persistent task DAG, writer leases and restart-safe task state.

The DAG is the dependency truth: a task is READY only when every dependency is
COMPLETE, and a completed task never re-runs. Writer leases enforce "one writer per
overlapping authority": two live tasks may not hold overlapping writable scope, and a
lease that outlived its holder (crash, restart, lost process) is STALE — it can be
broken, and the fencing counter makes the old holder's late commit impossible.
Everything survives a restart because everything is on disk, written atomically.
"""

from __future__ import annotations

import pytest

from core.council.task_dag import (
    DAGCycleRefused,
    DuplicateTaskRefused,
    OverlappingWriterRefused,
    StaleLeaseRefused,
    TaskDagError,
    TaskLedger,
    TaskStatus,
    WriterLease,
    paths_overlap,
    scopes_overlap,
)


@pytest.fixture()
def clock():
    return {"now": 1_000.0}


@pytest.fixture()
def ledger(tmp_path, clock):
    return TaskLedger(tmp_path, clock=lambda: clock["now"])


class TestScopeOverlap:
    def test_equal_paths_overlap(self):
        assert paths_overlap("core/council", "core/council")

    def test_prefix_directories_overlap(self):
        assert paths_overlap("core/council", "core/council/seats.py")
        assert paths_overlap("core/council/orchestrator.py", "core/council")

    def test_sibling_paths_do_not_overlap(self):
        assert not paths_overlap("core/council", "core/updater")
        assert not paths_overlap("core/council/a.py", "core/council/b.py")

    def test_path_prefixes_are_componentwise_not_stringwise(self):
        # "core/council-2" is a SIBLING of "core/council", not inside it.
        assert not paths_overlap("core/council", "core/council-2/x.py")

    def test_scope_sets_overlap_if_any_pair_does(self):
        assert scopes_overlap(("core/council", "docs/"), ("tests/x.py", "core/council/seats.py"))
        assert not scopes_overlap(("core/council",), ("core/updater",))


class TestDag:
    def test_a_task_lands_pending_with_its_dependencies(self, ledger):
        ledger.add_task("T1", dependencies=())
        ledger.add_task("T2", dependencies=("T1",))
        assert ledger.status("T1") is TaskStatus.PENDING
        assert ledger.dependencies("T2") == ("T1",)

    def test_duplicate_task_ids_are_refused(self, ledger):
        ledger.add_task("T1")
        with pytest.raises(DuplicateTaskRefused):
            ledger.add_task("T1")

    def test_cycles_are_refused_at_construction(self, ledger):
        ledger.add_task("T1", dependencies=("T3",))
        with pytest.raises(DAGCycleRefused):
            ledger.add_task("T3", dependencies=("T1",))

    def test_self_dependency_is_a_cycle(self, ledger):
        with pytest.raises(DAGCycleRefused):
            ledger.add_task("T1", dependencies=("T1",))

    def test_status_transitions_are_recorded(self, ledger):
        ledger.add_task("T1")
        ledger.set_status("T1", TaskStatus.RUNNING)
        assert ledger.status("T1") is TaskStatus.RUNNING
        ledger.set_status("T1", TaskStatus.COMPLETE)
        assert ledger.status("T1") is TaskStatus.COMPLETE

    def test_incomplete_dependencies_are_named(self, ledger):
        ledger.add_task("T0a")
        ledger.add_task("T0b")
        ledger.add_task("T1", dependencies=("T0a", "T0b"))
        ledger.set_status("T0a", TaskStatus.COMPLETE)
        assert ledger.incomplete_dependencies("T1") == ["T0b"]

    def test_a_task_with_all_dependencies_complete_is_not_blocked(self, ledger):
        ledger.add_task("T0")
        ledger.add_task("T1", dependencies=("T0",))
        ledger.set_status("T0", TaskStatus.COMPLETE)
        assert ledger.incomplete_dependencies("T1") == []

    def test_a_missing_dependency_blocks(self, ledger):
        ledger.add_task("T1", dependencies=("ghost",))
        assert ledger.incomplete_dependencies("T1") == ["ghost"]

    def test_unknown_task_status_is_none_not_a_guess(self, ledger):
        assert ledger.status("nope") is None


class TestRestartSafety:
    def test_state_survives_a_restart(self, tmp_path, clock):
        first = TaskLedger(tmp_path, clock=lambda: clock["now"])
        first.add_task("T0")
        first.add_task("T1", dependencies=("T0",))
        first.set_status("T0", TaskStatus.COMPLETE)

        second = TaskLedger(tmp_path, clock=lambda: clock["now"])
        assert second.status("T0") is TaskStatus.COMPLETE
        assert second.status("T1") is TaskStatus.PENDING
        assert second.incomplete_dependencies("T1") == []

    def test_a_lease_survives_a_restart_and_still_commits(self, tmp_path, clock):
        first = TaskLedger(tmp_path, clock=lambda: clock["now"])
        first.add_task("T1")
        lease = first.acquire_lease("T1", scope=("core/council",), holder="daemon-a", ttl_seconds=600)

        second = TaskLedger(tmp_path, clock=lambda: clock["now"])
        restarted = second.live_leases()
        assert [item.lease_id for item in restarted] == [lease.lease_id]
        assert restarted[0].fencing_counter == lease.fencing_counter
        second.commit_with_lease(lease.lease_id, fencing_counter=lease.fencing_counter,
                                 payload_sha256="a" * 64)

    def test_status_set_after_restart_lands_in_the_same_history(self, tmp_path, clock):
        first = TaskLedger(tmp_path, clock=lambda: clock["now"])
        first.add_task("T1")
        second = TaskLedger(tmp_path, clock=lambda: clock["now"])
        second.set_status("T1", TaskStatus.COMPLETE)
        third = TaskLedger(tmp_path, clock=lambda: clock["now"])
        assert third.status("T1") is TaskStatus.COMPLETE

    def test_the_event_log_is_append_only_across_restarts(self, tmp_path, clock):
        first = TaskLedger(tmp_path, clock=lambda: clock["now"])
        first.add_task("T1")
        before = first.events()
        second = TaskLedger(tmp_path, clock=lambda: clock["now"])
        second.add_task("T2")
        after = second.events()
        assert after[: len(before)] == before
        assert len(after) > len(before)


class TestWriterLeases:
    def test_acquire_grants_a_live_lease_with_a_fencing_counter(self, ledger):
        ledger.add_task("T1")
        lease = ledger.acquire_lease("T1", scope=("core/council",), holder="daemon-a", ttl_seconds=600)
        assert lease.task_id == "T1"
        assert lease.fencing_counter >= 1
        assert lease in ledger.live_leases()

    def test_one_writer_per_overlapping_authority(self, ledger):
        ledger.add_task("T1")
        ledger.add_task("T2")
        ledger.acquire_lease("T1", scope=("core/council",), holder="daemon-a", ttl_seconds=600)
        with pytest.raises(OverlappingWriterRefused) as caught:
            ledger.acquire_lease("T2", scope=("core/council/seats.py",), holder="daemon-b", ttl_seconds=600)
        assert caught.value.conflicting_lease.task_id == "T1"

    def test_disjoint_authorities_do_not_conflict(self, ledger):
        ledger.add_task("T1")
        ledger.add_task("T2")
        ledger.acquire_lease("T1", scope=("core/council",), holder="daemon-a", ttl_seconds=600)
        lease2 = ledger.acquire_lease("T2", scope=("core/updater",), holder="daemon-b", ttl_seconds=600)
        assert lease2.task_id == "T2"

    def test_a_second_holder_on_the_same_task_is_refused(self, ledger):
        ledger.add_task("T1")
        ledger.acquire_lease("T1", scope=("core/council",), holder="daemon-a", ttl_seconds=600)
        with pytest.raises(OverlappingWriterRefused):
            ledger.acquire_lease("T1", scope=("core/updater",), holder="daemon-b", ttl_seconds=600)

    def test_the_same_holder_renews_its_own_lease(self, ledger):
        ledger.add_task("T1")
        first = ledger.acquire_lease("T1", scope=("core/council",), holder="daemon-a", ttl_seconds=600)
        renewed = ledger.acquire_lease("T1", scope=("core/council",), holder="daemon-a", ttl_seconds=600)
        assert renewed.holder == "daemon-a"
        assert renewed.expires_at >= first.expires_at

    def test_commit_with_the_live_lease_succeeds(self, ledger):
        ledger.add_task("T1")
        lease = ledger.acquire_lease("T1", scope=("core/council",), holder="daemon-a", ttl_seconds=600)
        row = ledger.commit_with_lease(
            lease.lease_id, fencing_counter=lease.fencing_counter, payload_sha256="b" * 64
        )
        assert row["task_id"] == "T1"

    def test_commit_with_a_stale_fencing_counter_is_refused(self, ledger):
        ledger.add_task("T1")
        lease = ledger.acquire_lease("T1", scope=("core/council",), holder="daemon-a", ttl_seconds=600)
        with pytest.raises(StaleLeaseRefused):
            ledger.commit_with_lease(
                lease.lease_id, fencing_counter=lease.fencing_counter - 1, payload_sha256="b" * 64
            )

    def test_commit_on_an_expired_lease_is_refused(self, ledger, clock):
        ledger.add_task("T1")
        lease = ledger.acquire_lease("T1", scope=("core/council",), holder="daemon-a", ttl_seconds=600)
        clock["now"] += 601
        with pytest.raises(StaleLeaseRefused):
            ledger.commit_with_lease(
                lease.lease_id, fencing_counter=lease.fencing_counter, payload_sha256="b" * 64
            )

    def test_a_stale_lease_does_not_block_a_new_writer_forever(self, ledger, clock):
        ledger.add_task("T1")
        ledger.add_task("T2")
        dead = ledger.acquire_lease("T1", scope=("core/council",), holder="daemon-a", ttl_seconds=600)
        clock["now"] += 601  # daemon-a crashed; the lease expired

        # The overlapping authority is now claimable, and claiming it FENCES the
        # dead holder out of ever committing.
        fresh = ledger.acquire_lease("T2", scope=("core/council",), holder="daemon-b", ttl_seconds=600)
        assert fresh.fencing_counter > dead.fencing_counter
        with pytest.raises(StaleLeaseRefused):
            ledger.commit_with_lease(
                dead.lease_id, fencing_counter=dead.fencing_counter, payload_sha256="b" * 64
            )

    def test_break_stale_lease_fences_the_old_writer(self, ledger, clock):
        ledger.add_task("T1")
        lease = ledger.acquire_lease("T1", scope=("core/council",), holder="daemon-a", ttl_seconds=600)
        assert ledger.break_stale_lease(lease.lease_id) is False  # not stale yet
        clock["now"] += 601
        assert ledger.break_stale_lease(lease.lease_id) is True
        assert ledger.live_leases() == ()
        with pytest.raises(StaleLeaseRefused):
            ledger.commit_with_lease(
                lease.lease_id, fencing_counter=lease.fencing_counter, payload_sha256="b" * 64
            )

    def test_fencing_counters_only_ever_rise(self, ledger):
        ledger.add_task("T1")
        first = ledger.acquire_lease("T1", scope=("core/council",), holder="daemon-a", ttl_seconds=1)
        second = ledger.acquire_lease("T1", scope=("core/council",), holder="daemon-a", ttl_seconds=600)
        assert second.fencing_counter > first.fencing_counter

    def test_leases_expire_by_wall_clock(self, ledger, clock):
        ledger.add_task("T1")
        lease = ledger.acquire_lease("T1", scope=("core/council",), holder="daemon-a", ttl_seconds=30)
        assert lease.live_at(clock["now"])
        clock["now"] += 31
        assert not lease.live_at(clock["now"])
        assert ledger.live_leases() == ()


class TestLedgerRefusals:
    def test_lease_on_an_unknown_task_is_refused(self, ledger):
        with pytest.raises(TaskDagError):
            ledger.acquire_lease("ghost", scope=("core/council",), holder="daemon-a", ttl_seconds=600)

    def test_commit_with_an_unknown_lease_is_refused(self, ledger):
        with pytest.raises(StaleLeaseRefused):
            ledger.commit_with_lease("nope", fencing_counter=1, payload_sha256="b" * 64)

    def test_a_completed_task_is_never_re_leased_for_writing(self, ledger):
        ledger.add_task("T1")
        ledger.set_status("T1", TaskStatus.COMPLETE)
        with pytest.raises(TaskDagError):
            ledger.acquire_lease("T1", scope=("core/council",), holder="daemon-a", ttl_seconds=600)

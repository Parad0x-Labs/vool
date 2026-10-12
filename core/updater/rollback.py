"""Explicit previous-code rollback. Successful rollback never restores old user data."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import time
from pathlib import Path

from core.updater.migrations import SCHEMA_STATE_NAME
from core.updater.snapshot import restore_runtime, snapshot_runtime
from core.updater.state import _atomic_write_json, _read_json
from core.updater.status import UpdateFault, UpdatePhase
from core.updater.transaction import Step, UpdateJournal
from core.updater.versions import parse_semver

TERMINAL = frozenset({'completed', 'restored', 'aborted', 'rollback_failed'})


def _compatible_schema(data_dir: Path, previous: str) -> bool:
    path = data_dir / SCHEMA_STATE_NAME
    try:
        if not path.exists():
            return True
        state = _read_json(path)
        return not path.is_symlink() and isinstance(state, dict) and (
            parse_semver(str(state['schema_version'])) <= parse_semver(previous))
    except (KeyError, ValueError, TypeError):
        return False


def code_digest(root: Path) -> str:
    """Bounded digest of retained code, links and executable modes; no link following."""
    digest = hashlib.sha256()
    count = total = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in sorted([*dirs, *files]):
            path = Path(directory) / name
            mode = path.lstat().st_mode
            count += 1
            if count > 100000 or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
                raise ValueError('code backup has too many entries or an unsupported file')
            size = path.lstat().st_size if not stat.S_ISDIR(mode) else 0
            digest.update(json.dumps([path.relative_to(root).as_posix(), stat.S_IFMT(mode), mode & 0o777, size]).encode())
            if path.is_symlink():
                if not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError('code backup link leaves its directory')
                digest.update(os.readlink(path).encode())
            elif path.is_file():
                total += path.stat().st_size
                if total > 8 * 1024**3:
                    raise ValueError('code backup exceeds its size limit')
                with path.open('rb') as source:
                    for block in iter(lambda: source.read(1024 * 1024), b''):
                        digest.update(block)
        dirs.sort()
    return digest.hexdigest()


def expected_prior(subsystem, txid: str) -> Path:
    target = Path(subsystem.feed.app_path)
    if subsystem.platform.startswith('linux-'):
        return target / 'update_backups' / ('signed-' + txid)
    return target.parent / f'.{target.name}.prior-{txid}'


def rollback_candidate(subsystem) -> dict | None:
    """Only an intact backup of a completed, migration-free current install qualifies."""
    if not subsystem.feed.has_target:
        return None
    used = {state.get('source_txid') for path in (subsystem.paths.root / 'manual_rollbacks').glob('*/state.json')
            if isinstance(state := _read_json(path), dict) and state.get('phase') == 'completed'}
    candidates = []
    for directory in subsystem.paths.transactions.iterdir():
        if not directory.is_dir() or directory.name in used:
            continue
        journal = UpdateJournal(directory)
        decision = journal.payload_of(Step.DECIDED) or {}
        previous = str(decision.get('installed_version') or '')
        if not journal.has(Step.FINALIZED) or decision.get('target_version') != subsystem.installed_version:
            continue
        if (journal.payload_of(Step.MIGRATED) or {}).get('applied'):
            continue  # reversing a data migration could discard current history
        if decision.get('platform') != subsystem.platform or not _compatible_schema(subsystem.data_dir, previous):
            continue
        prior = expected_prior(subsystem, directory.name)
        actual_prior = (journal.payload_of(Step.SWAPPED) or {}).get('prior_path')
        expected = (journal.payload_of(Step.FINALIZED) or {}).get('prior_code_sha256')
        if not expected or not prior.is_dir() or prior.is_symlink() or str(prior) != actual_prior:
            continue
        # Status polling need not hash hundreds of MB. The detached worker always
        # rechecks the retained bytes before stopping the app.
        candidates.append((directory.stat().st_mtime, dict(source_txid=directory.name, prior_path=str(prior),
            version=previous, identity=decision.get('previous_identity') or previous, sha256=expected)))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def incomplete_rollbacks(subsystem) -> list[Path]:
    return [path for path in (subsystem.paths.root / 'manual_rollbacks').glob('*/state.json')
            if not isinstance(state := _read_json(path), dict) or state.get('phase') not in TERMINAL]


def _save(path: Path, state: dict, phase: str) -> None:
    state.update(phase=phase, updated_at=time.time())
    _atomic_write_json(path, state)


def _installer(subsystem):
    from core.updater.platforms import installer_for

    installer = installer_for(subsystem.platform)
    if installer is None:
        raise ValueError('rollback is unavailable for this platform')
    if subsystem.platform.startswith('linux-'):
        installer.runtime_home = subsystem.data_dir.resolve().parent
        installer.replace_full_code = True
    return installer


def _finish(subsystem, path: Path, state: dict) -> dict:
    if subsystem.platform.startswith('linux-'):
        (Path(subsystem.feed.app_path) / '.vool_updating.lock').unlink(missing_ok=True)
    _save(path, state, 'completed')
    subsystem.status.publish(UpdatePhase.ROLLED_BACK, previous_version=state['candidate']['version'],
                             manual=True, fault=UpdateFault.MANUAL_ROLLBACK)
    return {'ok': True, 'version': state['candidate']['version'], 'current_data_preserved': True}


def _stop(helper) -> None:
    if getattr(helper, 'performs_swap', False):
        if not helper.stop_running_app(timeout=30):
            raise ValueError('the runtime did not stop')
    else:
        helper.shutdown_app()
        if not helper.wait_until_stopped(timeout=30):
            raise ValueError('the runtime did not stop')


def _restore_current(subsystem, path: Path, state: dict) -> dict:
    """Failed manual rollback uses its OWN fresh snapshot, never the update's old one."""
    helper = subsystem._make_helper()
    _stop(helper)
    installer = _installer(subsystem)
    backup = expected_prior(subsystem, state['txid'])
    if backup.exists():
        result = installer.restore_prior(Path(subsystem.feed.app_path), backup, txid=state['txid'] + '-recover')
        if not result.ok:
            raise ValueError(result.detail)
    elif state['phase'] in ('swapped', 'healthy'):
        raise ValueError('current-version backup is missing')
    if state.get('snapshot_complete'):
        restore_runtime(subsystem.data_dir, path.parent / 'current-data')
    if not helper.relaunch(Path(subsystem.feed.app_path)):
        raise ValueError('current version could not restart')
    health = subsystem._make_probe()(timeout=subsystem.health_timeout, expected_version=state['current_identity'])
    if not health.ok:
        raise ValueError('current version did not return healthy')
    _save(path, state, 'restored')
    subsystem.status.publish(UpdatePhase.FAILED, fault=UpdateFault.INSTALL_FAILED,
                             detail='The requested rollback failed. The current app and data were restored')
    return {'ok': False, 'restored_current': True}


def run_manual_rollback(subsystem, candidate: dict, txid: str) -> dict:
    root = subsystem.paths.root / 'manual_rollbacks' / txid
    root.mkdir(parents=True, exist_ok=False)
    path = root / 'state.json'
    state = {'txid': txid, 'source_txid': candidate['source_txid'], 'candidate': candidate,
             'current_identity': (UpdateJournal(subsystem.paths.transaction_dir(candidate['source_txid'])).payload_of(Step.DECIDED) or {}).get('expected_identity') or subsystem.installed_version}
    _save(path, state, 'prepared')
    stopped = False
    try:
        prior = expected_prior(subsystem, candidate['source_txid'])
        if rollback_candidate(subsystem) != candidate or not prior.is_dir() or prior.is_symlink() or (
            str(prior) != candidate['prior_path'] or code_digest(prior) != candidate['sha256']
        ):
            raise ValueError('the previous-version backup failed its integrity check')
        installer = _installer(subsystem)
        stage = root / 'previous-code' if subsystem.platform.startswith('linux-') else (
            Path(subsystem.feed.app_path).parent / f'.{Path(subsystem.feed.app_path).name}.rollback-{txid}-stage')
        state['stage'] = str(stage)
        if subsystem.platform.startswith('linux-'):
            from installer.self_update import compute_preserve_names, rollback_swap

            preserve = compute_preserve_names(Path(subsystem.feed.app_path), subsystem.data_dir.resolve().parent)
            # Complete prior code = current protected-code shape with the existing
            # original swap journal applied to a COPY; the retained backup stays intact.
            shutil.copytree(subsystem.feed.app_path, stage, symlinks=True,
                            ignore=lambda directory, names: preserve & set(names) if Path(directory) == Path(subsystem.feed.app_path) else set())
            backup_copy = root / 'prior-copy'
            shutil.copytree(prior, backup_copy, symlinks=True)
            rollback_swap(stage, backup_copy)
        else:
            shutil.copytree(prior, stage, symlinks=True)
            required = installer.running_requires_notarization(Path(subsystem.feed.app_path))
            verified = installer.verify_bundle(stage, require_notarization=required)
            if not verified.ok:
                raise ValueError(verified.detail)
        if not _compatible_schema(subsystem.data_dir, candidate['version']):
            raise ValueError('current data is incompatible with the previous version; data was not reverted')
        _save(path, state, 'swap_planned')
        helper = subsystem._make_helper()
        stopped = True
        _stop(helper)
        snapshot_runtime(subsystem.data_dir, root / 'current-data')
        state['snapshot_complete'] = True
        _save(path, state, 'swap_planned')
        result = installer.atomic_swap(stage, Path(subsystem.feed.app_path), txid=txid)
        if not result.ok:
            raise ValueError(result.detail)
        _save(path, state, 'swapped')
        if not helper.relaunch(Path(subsystem.feed.app_path)):
            raise ValueError('previous version could not restart')
        health = subsystem._make_probe()(timeout=subsystem.health_timeout, expected_version=candidate['identity'])
        if not health.ok:
            raise ValueError('previous version did not return healthy')
        _save(path, state, 'healthy')
        return _finish(subsystem, path, state)
    except Exception as exc:
        if not stopped:
            _save(path, state, 'aborted')
            subsystem.status.publish(UpdatePhase.FAILED, detail=str(exc), fault=UpdateFault.VERIFICATION_FAILED)
            return {'ok': False, 'detail': str(exc)}
        try:
            return _restore_current(subsystem, path, state)
        except Exception as restore:
            _save(path, state, 'rollback_failed')
            subsystem.status.publish(UpdatePhase.FAILED, fault=UpdateFault.ROLLBACK_FAILED, detail=str(restore))
            return {'ok': False, 'detail': str(restore)}


def recover_manual_rollbacks(subsystem) -> dict:
    results = []
    for path in incomplete_rollbacks(subsystem):
        state = _read_json(path)
        if not isinstance(state, dict):
            subsystem.status.publish(UpdatePhase.FAILED, fault=UpdateFault.ROLLBACK_FAILED,
                                     detail='The manual rollback journal is damaged; retained app and data backups were kept')
            results.append({'ok': False, 'detail': 'damaged rollback journal'})
            continue
        try:
            if state['phase'] in ('swapped', 'healthy'):
                health = subsystem._make_probe()(timeout=3, expected_version=state['candidate']['identity'])
                if not health.ok:
                    helper = subsystem._make_helper()
                    _stop(helper)
                    if helper.relaunch(Path(subsystem.feed.app_path)):
                        health = subsystem._make_probe()(timeout=subsystem.health_timeout,
                                                         expected_version=state['candidate']['identity'])
                if health.ok:
                    results.append(_finish(subsystem, path, state))
                    continue
            results.append(_restore_current(subsystem, path, state))
        except Exception as exc:
            _save(path, state, 'rollback_failed')
            subsystem.status.publish(UpdatePhase.FAILED, fault=UpdateFault.ROLLBACK_FAILED, detail=str(exc))
            results.append({'ok': False, 'detail': str(exc)})
    return {'ok': all(result.get('ok') or result.get('restored_current') for result in results), 'results': results}

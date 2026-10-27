"""One disposable offline worker; only explicit runtime code and guest input are readable."""
import hashlib
import json
import os
import re
import shutil
import signal
import sys
import sysconfig
import tempfile
import time
from pathlib import Path

import psutil

from core.artifact_readers._sandbox import (
    confinement_available,
    extraction_budget,
    run_confined,
)
from core.artifact_readers._types import ReaderFailed, ReaderRefused, ReaderUnavailable

from .terms import MAX_OUTPUT, LendError, demand, identity


def available(capability):
    if not confinement_available():
        return False
    if capability == 'recording':
        # Match the fixed decoder paths inside the worker, including desktop
        # launches that do not inherit the owner's interactive shell PATH.
        path = '/opt/homebrew/bin:/usr/local/bin'
        return bool(shutil.which('ffmpeg', path=path) and shutil.which('ffprobe', path=path))
    return capability in {'contact_sheet', 'clean_pdf'}


def runtime_reads():
    """Code directories only. Never grant an entire interpreter prefix or a VOOL home."""
    from core.runtime_paths import active_vool_home

    paths = {str(Path(sys._base_executable).resolve().parent)}
    paths.add(str(Path(sys.base_prefix)/'lib'))
    paths.update(sysconfig.get_path(key) for key in ('stdlib', 'platstdlib', 'purelib', 'platlib'))
    home = active_vool_home().resolve()
    for path in paths:
        value = Path(path).resolve()
        demand(value != Path('/') and value != Path.home() and not home.is_relative_to(value), 'UNSAFE_RUNTIME')
    return sorted(paths)


def command(directory, value):
    seconds = value['max_seconds']
    if 'expires' in value:
        seconds = max(.001, min(seconds, (value['expires']-time.time()*1000)/1000))
    return [str(Path(sys._base_executable).resolve()), '-I', str(directory/'worker.py'),
            sysconfig.get_path('purelib'), str(seconds)]


def _same_process(pid, created):
    try:
        process = psutil.Process(pid)
        return process.create_time() == created and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied as exc:
        raise LendError('WORKER_CLEANUP_PENDING') from exc


def clean_orphans(root):
    """Recover only this owner's trusted worker journals, never a process-name search."""
    from core.drop.service import _private_directory, _read

    journals = root/'supervision'
    if not journals.exists():
        return
    _private_directory(journals)
    for path in journals.glob('*.json'):
        record = json.loads(_read(path, 4096))
        name = record['directory']
        demand(re.fullmatch(r'worker-[0-9a-f]{32}-[a-z0-9_]{8}', name), 'UNSAFE_WORKER_RECORD')
        if _same_process(record['parent'], record['parent_created']):
            continue
        if time.time()*1000 < record['deadline']+2000:
            continue
        pid, created = record.get('pid'), record.get('created')
        if pid and _same_process(pid, created):
            # This pid/group and its start identity were recorded at Popen by
            # the trusted supervisor, outside the worker's writable scratch.
            demand(os.getpgid(pid) == pid, 'UNSAFE_WORKER_RECORD')
            os.killpg(pid, signal.SIGKILL)
            deadline = time.monotonic()+2
            while _same_process(pid, created) and time.monotonic() < deadline:
                time.sleep(.05)
            demand(not _same_process(pid, created), 'WORKER_CLEANUP_PENDING')
        folder = root/name
        if folder.exists():
            _private_directory(folder)
            shutil.rmtree(folder)
        path.unlink()


def execute(root, data, value, *, job_id):
    demand(available(value['capability']), 'CAPABILITY_UNAVAILABLE')
    from core.drop.service import _private_directory, _read, _write

    start = time.monotonic()
    journals = root/'supervision'
    _private_directory(journals, create=True)
    journal = None
    try:
        with tempfile.TemporaryDirectory(prefix='worker-'+identity(job_id)+'-', dir=root) as temp:
            directory = Path(temp)
            journal = journals/(directory.name+'.json')
            record = {'directory':directory.name, 'parent':os.getpid(),
                      'parent_created':psutil.Process().create_time(),
                      'deadline':min(time.time()*1000+value['max_seconds']*1000,value['expires'])}
            _write(journal,json.dumps(record).encode())

            def started(process):
                record.update(pid=process.pid, created=psutil.Process(process.pid).create_time())
                _write(journal,json.dumps(record).encode())
                _write(directory/'start.ok',b'go')

            _write(directory/'input.bin', data)
            _write(directory/'terms.json', json.dumps(value).encode())
            _write(directory/'worker.py', Path(__file__).with_name('task_worker.py').read_bytes())
            with extraction_budget(scratch=directory, seconds=value['max_seconds']):
                try:
                    result = run_confined(command(directory, value), scratch=directory,
                                          timeout=value['max_seconds'], extra_read=runtime_reads(),
                                          max_stdout=4096, max_stderr=4096, allow_mach_lookup=False,
                                          allow_child_signals=True,
                                          on_start=started)
                except ReaderFailed:
                    if (directory/'deadline').exists():
                        raise LendError('TIMEOUT') from None
                    raise
            demand(result.returncode != 2, 'TIMEOUT')
            demand(result.returncode == 0, 'JOB_FAILED')
            stats = json.loads(result.stdout)
            output = _read(directory/'output.bin', MAX_OUTPUT)
            demand(output and stats['sha256'] == hashlib.sha256(output).hexdigest(), 'JOB_FAILED')
            return output, stats['name'], round(time.monotonic()-start, 3)
    except (ReaderFailed, ReaderRefused, ReaderUnavailable) as exc:
        raise LendError('TIMEOUT' if exc.code in {'decoder_timeout', 'extraction_timeout'}
                        else 'JOB_FAILED') from exc
    except (OSError, ValueError, KeyError, psutil.Error) as exc:
        raise LendError('JOB_FAILED') from exc
    finally:
        if journal is not None and not (root/journal.stem).exists():
            journal.unlink(missing_ok=True)

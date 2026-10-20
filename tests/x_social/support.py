from __future__ import annotations

import json
import os
import time
from pathlib import Path


def resource(kind: str, value: int, state: str) -> None:
    target = os.environ.get('SOCIAL_RESOURCE_LOG')
    if target:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as out:
            out.write(json.dumps({'at': time.time(), 'pid': os.getpid(), 'kind': kind, 'value': value, 'state': state}) + '\n')

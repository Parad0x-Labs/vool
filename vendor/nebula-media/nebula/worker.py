"""nebula.worker — isolated media worker over stdio (line-delimited JSON).

Protocol:
    request : {"id": <any>, "op": "<name>", "params": {...}}
    response: {"id": <any>, "ok": true,  "result": ...}
              {"id": <any>, "ok": false, "error": {"code": "...", "message": "..."}}

One long-lived process handles many requests. A malformed request or a failed
media operation produces a structured error response — the worker never exits
because of bad input. The host (e.g. VOOL) owns this process's lifecycle;
heavy ffmpeg work therefore never runs inside a UI event loop.
"""

from __future__ import annotations

import json
import sys

from .media_ops import MediaOpError, execute

MAX_LINE = 1 << 20  # 1 MiB per request line


def _handle(line: str) -> dict:
    try:
        req = json.loads(line)
        if not isinstance(req, dict):
            raise ValueError("request must be an object")
        rid = req.get("id")
    except (json.JSONDecodeError, ValueError) as exc:
        return {"id": None, "ok": False,
                "error": {"code": "bad_request", "message": str(exc)}}
    try:
        result = execute(req.get("op", ""), req.get("params") or {})
        return {"id": rid, "ok": True, "result": result}
    except MediaOpError as exc:
        return {"id": rid, "ok": False, "error": exc.to_dict()}
    except TypeError as exc:
        return {"id": rid, "ok": False,
                "error": {"code": "bad_params", "message": str(exc)}}
    except Exception as exc:  # noqa: BLE001 — the worker must survive anything
        return {"id": rid, "ok": False,
                "error": {"code": "internal", "message": f"{type(exc).__name__}: {exc}"}}


def serve(stdin=sys.stdin, stdout=sys.stdout) -> None:  # noqa: ANN001
    """Serve requests until stdin closes."""
    while True:
        line = stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        if len(line) > MAX_LINE:
            resp = {"id": None, "ok": False,
                    "error": {"code": "bad_request", "message": "request too large"}}
        else:
            resp = _handle(line)
        stdout.write(json.dumps(resp) + "\n")
        stdout.flush()


def main() -> None:
    serve()


if __name__ == "__main__":
    main()

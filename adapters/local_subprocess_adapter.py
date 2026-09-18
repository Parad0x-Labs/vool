from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from contextlib import suppress
from shutil import which
from typing import Any

from adapters.base_adapter import ModelAdapter, ModelRequest, ModelResponse
from core.provider_invocation_gateway import seal_provider_invocation


class LocalSubprocessAdapter(ModelAdapter):
    def validate_runtime(self) -> list[str]:
        command = self._command()
        if not command:
            return [f"{self.manifest.provider_id}: missing runtime_config.command"]
        if which(command[0]) is None:
            return [f"{self.manifest.provider_id}: command not found: {command[0]}"]
        return []

    def health_check(self) -> dict[str, Any]:
        command = self._command()
        if not command:
            return {"ok": False, "provider_id": self.manifest.provider_id, "error": "missing_command"}
        if which(command[0]) is None:
            return {"ok": False, "provider_id": self.manifest.provider_id, "error": f"command_not_found:{command[0]}"}
        return {"ok": True, "provider_id": self.manifest.provider_id}

    def invoke(self, request: ModelRequest) -> ModelResponse:
        command = self._command()
        if not command:
            raise RuntimeError(f"{self.manifest.provider_id}: missing runtime_config.command")
        return self._invoke_with_env(request, extra_env={})

    def _invoke_with_env(self, request: ModelRequest, *, extra_env: dict[str, str]) -> ModelResponse:
        command = self._command()
        from core.provider_call_deadline import effective_timeout_seconds

        timeout_seconds = effective_timeout_seconds(
            request,
            float(self.manifest.runtime_config.get("timeout_seconds") or 60.0),
        )
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in dict(self.manifest.runtime_config.get("env") or {}).items()})
        env.update({str(k): str(v) for k, v in extra_env.items()})
        env.setdefault("VOOL_PROVIDER_NAME", self.manifest.provider_name)
        env.setdefault("VOOL_MODEL_NAME", self.manifest.model_name)
        payload = {
            "task_kind": request.task_kind,
            "prompt": request.prompt,
            "system_prompt": request.system_prompt,
            "context": request.context,
            "temperature": request.temperature,
            "max_output_tokens": request.max_output_tokens,
            "provider_name": self.manifest.provider_name,
            "model_name": self.manifest.model_name,
        }
        permit = seal_provider_invocation(
            request=request,
            provider_id=self.manifest.provider_id,
            model_id=self.manifest.model_name,
            operation="subprocess",
            payload=payload,
        )
        sealed_payload = permit.consume()
        input_text: str | None = json.dumps(sealed_payload, sort_keys=True)
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                stdout, stderr = process.communicate(input=input_text, timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                input_text = None
                if time.monotonic() >= deadline:
                    _terminate_process_tree(process)
                    raise subprocess.TimeoutExpired(command, timeout_seconds)
                if request.is_cancelled():
                    _terminate_process_tree(process)
                    raise RuntimeError("model_call_cancelled")
        if process.returncode != 0:
            raise RuntimeError(
                f"{self.manifest.provider_id}: subprocess failed with code {process.returncode}: {stderr.strip()}"
            )
        stdout = stdout.strip()
        if not stdout:
            return ModelResponse(output_text="", confidence=0.3, raw_response={"stderr": stderr.strip()})
        try:
            obj = json.loads(stdout)
            return ModelResponse(
                output_text=str(obj.get("output_text") or obj.get("text") or ""),
                confidence=float(obj.get("confidence") or 0.5),
                raw_response=obj,
                usage=dict(obj.get("usage") or {}),
            )
        except Exception:
            return ModelResponse(output_text=stdout, confidence=0.5, raw_response={"stdout": stdout})

    def _command(self) -> list[str]:
        command = self.manifest.runtime_config.get("command") or []
        if isinstance(command, str):
            return [command]
        return [str(item) for item in command]


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=5,
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
    except Exception:
        with suppress(Exception):
            process.kill()

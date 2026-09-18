from pathlib import Path

from core import policy_engine


class FilesystemGuard:
    """
    Enforces strict filesystem virtualization rules.
    Blocks any writes escaping the specific sandbox environment root.
    """

    def __init__(self, sandbox_root: Path):
        self.root = sandbox_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def safe_resolve(self, relative_path: str) -> Path:
        """
        Takes a relative string path and resolves it inside the sandbox.
        Throws PermissionError if it attempts directory traversal.
        """
        target = (self.root / relative_path).resolve()

        # Check against global Policy Engine hard-bans
        is_allowed = True
        reason = ""
        target_str = str(target).lower()
        for banned in policy_engine.get("filesystem.deny_paths", []):
            if target_str.startswith(banned.lower()):
                is_allowed = False
                reason = f"Path hits deny rule: {banned}"
                break
        if not is_allowed:
            raise PermissionError(f"[FILESYSTEM GUARD] Access Denied: {reason}")

        # Ensure it stays within the sandbox root
        try:
            target.relative_to(self.root)
        except ValueError:
            raise PermissionError(f"[FILESYSTEM GUARD] Access Denied: Path escapes sandbox boundary ({relative_path})")

        return target

    def write_file(self, rel_path: str, content: str) -> dict:
        try:
            p = self.safe_resolve(rel_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return {"status": "success", "bytes": len(content.encode("utf-8"))}
        except Exception as e:
            return {"error": str(e)}

    def read_file(self, rel_path: str) -> dict:
        try:
            p = self.safe_resolve(rel_path)
            if not p.exists():
                return {"error": "File or directory not found"}

            if p.is_dir():
                try:
                    entries = [str(x.relative_to(self.root)) for x in p.iterdir()][:100]
                    return {"type": "directory", "entries": entries}
                except Exception as e:
                    return {"error": f"Failed to list directory: {e}"}

            return {"type": "file", "content": p.read_text(encoding="utf-8")}
        except Exception as e:
            return {"error": str(e)}

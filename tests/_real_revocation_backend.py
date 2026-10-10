"""Search-only test backends retain the real durable revocation contract."""
from core.runtime_paths import active_vool_home
from core.vool_memory import VoolMemory


class RealRevocationBackend:
    @property
    def _authority_home(self):
        return active_vool_home()

    def revoked_tokens(self, *args, **kwargs):
        memory = VoolMemory(runtime_home=self._authority_home)
        try:
            return memory.revoked_tokens(*args, **kwargs)
        finally:
            memory.close()

    def revocation_generation(self, *args, **kwargs):
        memory = VoolMemory(runtime_home=self._authority_home)
        try:
            return memory.revocation_generation(*args, **kwargs)
        finally:
            memory.close()

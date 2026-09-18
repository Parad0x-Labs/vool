class CommandSimulator:
    """
    V2: Execution simulation layer for 'simulate_only' policy mode.
    Returns exactly what would happen without touching the OS.
    """

    @staticmethod
    def run(steps: list) -> dict:
        """
        No real execution. Only validates syntax and returns simulated observation.
        """
        simulated_output = ""
        for step in steps:
            cmd = step.get("cmd", "")
            if not cmd: continue

            simulated_output += f"[SIMULATION] Validated abstract command syntax: {cmd}\n"
            if "rm " in cmd or "del " in cmd:
                simulated_output += "  -> Target would be marked for deletion.\n"
            if "npm install" in cmd:
                simulated_output += "  -> Packages would be installed into node_modules.\n"
            if "echo" in cmd:
                simulated_output += "  -> Expected output: stdout writing.\n"

        return {
            "cmd": "SIMULATED",
            "returncode": 0,
            "stdout": simulated_output,
            "stderr": "",
            "success": True,
            "mode": "simulate_only"
        }

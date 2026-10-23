"""Test container entry: external simulated model + unchanged real supervisor."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen


def main():
    base = os.environ["VLLM_BASE_URL"]
    parsed = urlsplit(base)
    if parsed.hostname != "127.0.0.1":
        raise ValueError("Rehearsal only supports a loopback TEST model")
    processes = []
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        model = subprocess.Popen([sys.executable, "/opt/rehearsal/rehearsal_model.py", "--port", str(parsed.port)])
        processes.append(model)
        for _ in range(100):
            if stopping or model.poll() is not None:
                return 1
            try:
                with urlopen(base + "/models", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("TEST model did not start")
        # TEST-only initial account setup happens before the real backend owns its DB lock.
        import importlib.util
        sys.path.insert(0, "/app")
        spec = importlib.util.spec_from_file_location("rehearsal_runtime", "/opt/deploy/runtime.py")
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)
        runtime.configure(create=True)
        from packages.adapters.platform_store import Platform
        from packages.adapters.storage import Store
        db = Store(Path(os.environ["DEMO_STATE_DIR"]))
        try:
            platform = Platform(db, Path("/app"))
            if not platform.initialized():
                platform.initialize_admin("office-test-admin", "协议测试管理员", "office-protocol-test-password")
        finally:
            db.close()
        service = subprocess.Popen([sys.executable, "/opt/deploy/runtime.py", "serve"])
        processes.append(service)
        while not stopping:
            if model.poll() is not None or service.poll() is not None:
                return service.returncode or 1
            time.sleep(0.2)
        return 0
    finally:
        stop(None, None)
        for process in reversed(processes):
            try:
                process.wait(timeout=35)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())

"""Run the virtual control loop independently from the demo backend."""

import argparse
import asyncio
import os
import signal
from pathlib import Path

from packages.adapters.live_telemetry import LiveTelemetry


async def serve(state_dir: Path) -> None:
    loop = LiveTelemetry(state_dir)
    await loop.start()
    stopped = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stopped.set)
    try:
        print(f"示例回路已启动：{loop.root}", flush=True)
        await stopped.wait()
    finally:
        await loop.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-dir", type=Path,
        default=Path(os.getenv("DEMO_STATE_DIR", "var/backend")),
        help="与 demo-backend 共用的状态目录",
    )
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.state_dir))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

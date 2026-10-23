"""CLI parser check only; CPU platform selection substitutes for absent lab GPUs.

No engine or model is started. Real GPU support is checked onsite by gpu-check.
"""

import sys

import vllm.platforms
from vllm.platforms.cpu import CpuPlatform

vllm.platforms.current_platform = CpuPlatform()
from vllm.entrypoints.cli.main import main

print("PARSER TEST ONLY: explicitly selected CPU platform; no GPU inference", flush=True)
sys.argv = ["vllm", "serve", "/metadata", "--help=all"]
main()

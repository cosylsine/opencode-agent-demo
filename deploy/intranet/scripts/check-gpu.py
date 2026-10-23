"""Executed inside the bundled vLLM image, with no model or network required."""

import os

import torch

print("torch=", torch.__version__, "CUDA=", torch.version.cuda, flush=True)
assert torch.cuda.is_available(), "CUDA is unavailable"
count = torch.cuda.device_count()
print("visible GPUs=", count, flush=True)
assert count == int(os.environ["EXPECTED_GPU_COUNT"]), "Unexpected GPU count"
for index in range(count):
    device = f"cuda:{index}"
    x = torch.ones(16, device=device)
    value = (x + x).sum().item()
    assert value == 32.0, f"CUDA computation failed on {device}"
    print(device, torch.cuda.get_device_name(index), "GPU computation=", value, flush=True)
print("所有所选 GPU 的 CUDA 运算通过；接下来启动模型验证显存与并行通信。", flush=True)

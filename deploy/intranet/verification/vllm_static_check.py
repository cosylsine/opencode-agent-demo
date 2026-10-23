"""No-weight/no-GPU checks inside the exact image shipped to the offline host."""

import importlib.metadata as metadata
import json
from pathlib import Path

versions = {name: metadata.version(name) for name in (
    "vllm", "torch", "transformers", "flashinfer-python", "flashinfer-jit-cache", "torchcodec"
)}
assert versions["vllm"].startswith("0.31.0"), versions
assert tuple(map(int, versions["transformers"].split(".")[:2])) >= (5, 8), versions
assert Path("/usr/local/cuda/compat").is_dir(), "CUDA compatibility libraries missing"
package = Path(metadata.distribution("vllm").locate_file("vllm"))
registry = (package / "model_executor/models/registry.py").read_text()
assert "Qwen3_5ForConditionalGeneration" in registry, "Model architecture missing"
assert any(package.rglob("*qwen3*tool_parser*.py")), "Qwen3 tool parser missing"
assert any(package.rglob("*qwen3*reasoning_parser*.py")), "Qwen3 reasoning parser missing"
import torchcodec

assert Path(torchcodec.__file__).is_relative_to("/opt/opencode-vllm-compat")
assert versions["torchcodec"] == "0.17.0+cpu"
from transformers import AutoConfig, AutoTokenizer

config = AutoConfig.from_pretrained("/metadata", local_files_only=True, trust_remote_code=False)
assert config.architectures == ["Qwen3_5ForConditionalGeneration"], config.architectures
tokenizer = AutoTokenizer.from_pretrained("/metadata", local_files_only=True, trust_remote_code=False)
sample = "你好，离线部署测试。"
assert tokenizer.decode(tokenizer.encode(sample)) == sample
prompt = tokenizer.apply_chat_template(
    [{"role": "user", "content": sample}], tokenize=False,
    add_generation_prompt=True, enable_thinking=False,
)
assert sample in prompt and "assistant" in prompt
print(json.dumps({
    "network": "none", "versions": versions,
    "model_architecture": "Qwen3_5ForConditionalGeneration (Qwen3.8-27B)",
    "model_registry": "present", "tool_and_reasoning_parsers": "present",
    "cuda_compat_libraries": "present", "gpu_inference": "NOT TESTED: no target GPU or weights",
    "offline_tokenizer_and_chat_template": "passed",
}, ensure_ascii=False, indent=2))

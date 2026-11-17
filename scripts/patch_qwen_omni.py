"""Keep the project's repetition-penalty control working in Omni 0.30.0."""
from pathlib import Path
from importlib.metadata import distribution

path = Path(distribution("vllm-omni").locate_file("vllm_omni/entrypoints/openai/serving_speech.py"))
source = path.read_text()
old = 'for name in ("temperature", "top_p", "top_k"):'
new = 'for name in ("temperature", "top_p", "top_k", "repetition_penalty"):'
if old in source:
    path.write_text(source.replace(old, new, 1))
    print("Applied request-level repetition_penalty compatibility patch")
elif new not in source:
    raise RuntimeError("Omni sampling adapter differs from the pinned version")

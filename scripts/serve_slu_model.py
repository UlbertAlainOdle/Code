"""Standalone OpenAI-compatible server for the merged SLU model.

Runs OUTSIDE the Saya backend in a python env that has a working CUDA torch
(e.g. the Dear0 conda env) and exposes just enough of the /v1/chat/completions
contract for saya_app/services/slu.py's "model" backend:

    POST /v1/chat/completions   {"messages": [...], "max_tokens": N}
    GET  /health

Greedy decoding on purpose (routing wants determinism).  Run:

    start_slu_server.bat        (or)
    D:/Program/anaconda3/envs/Dear0/python.exe scripts/serve_slu_model.py
"""

import argparse
import os
import time

MODEL_PATH = os.environ.get(
    "SLU_MODEL_PATH", r"D:\hf_cache\slu_merged_qwen3_4b"
)
# The merged dir's tokenizer was re-saved by transformers 5.16 (Saya env);
# older serving envs must load the ORIGINAL tokenizer files instead.
TOKENIZER_PATH = os.environ.get(
    "SLU_TOKENIZER_PATH",
    r"D:\hf_cache\models--Qwen--Qwen3-4B-Instruct-2507\snapshots"
    r"\cdbee75f17c01a7cc42f958dc650907174af0554",
)
PORT = int(os.environ.get("SLU_SERVER_PORT", "8555"))


def create_app():
    import torch
    from fastapi import FastAPI, Request
    from transformers import AutoModelForCausalLM, AutoTokenizer

    import threading

    app = FastAPI(title="Saya SLU model server")
    state = {"model": None, "tokenizer": None}
    # SLU is single-user: serialize generations so a slow/abandoned request
    # can never stack three CUDA streams into an OOM.
    generate_lock = threading.Lock()

    @app.on_event("startup")
    def load_model():
        print(f"[slu-server] loading {MODEL_PATH} ...", flush=True)
        tokenizer = AutoTokenizer.from_pretrained(TOKENIZER_PATH)
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_PATH, torch_dtype=torch.bfloat16, attn_implementation="sdpa",
        )
        model.to("cuda")
        model.eval()
        state["model"] = model
        state["tokenizer"] = tokenizer
        print("[slu-server] ready", flush=True)

    @app.get("/health")
    def health():
        return {"ok": state["model"] is not None}

    def _generate(messages, max_tokens):
        with generate_lock:
            return _generate_locked(messages, max_tokens)

    def _generate_locked(messages, max_tokens):
        tokenizer = state["tokenizer"]
        model = state["model"]
        encoded = tokenizer.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True,
            return_tensors="pt", return_dict=True,
        ).to(model.device)
        t0 = time.time()
        with torch.no_grad():
            output = model.generate(
                **encoded, max_new_tokens=max(32, min(512, max_tokens)),
                do_sample=False,
            )
        answer = tokenizer.decode(
            output[0][encoded["input_ids"].shape[1]:], skip_special_tokens=True
        )
        return answer, round(time.time() - t0, 2)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        # Raw-JSON body on purpose: version-proof against pydantic v1/v2
        # mismatches in whichever env serves the model.
        if state["model"] is None:
            return {"error": "model still loading"}
        body = await request.json()
        messages = body.get("messages") or []
        max_tokens = int(body.get("max_tokens") or 256)
        from fastapi.concurrency import run_in_threadpool

        answer, latency = await run_in_threadpool(_generate, messages, max_tokens)
        return {
            "choices": [{"message": {"role": "assistant", "content": answer}}],
            "usage": {"latency_seconds": latency, "server": "slu-standalone"},
        }

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=MODEL_PATH)
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    os.environ["SLU_MODEL_PATH"] = args.model
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

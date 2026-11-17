"""LoRA fine-tune for the SLU understanding model (run AFTER building the
dataset with scripts/build_slu_dataset.py).

    python -B scripts/train_slu_lora.py --base Qwen/Qwen2.5-1.5B-Instruct \
        --data knowledge_base/slu_corpus/slu_sft_dataset.jsonl \
        --out knowledge_base/slu_corpus/slu_lora_adapter

Hardware note: 1.5B-3B LoRA fits a 24 GB GPU (RTX 4090) comfortably with
bf16 + gradient checkpointing; 7B fits with 4-bit quantization (bitsandbytes).

Requirements (not installed by default):
    pip install torch transformers peft datasets accelerate

After training, serve the merged adapter with any OpenAI-compatible server
(llama.cpp `--lora`, vLLM `--enable-lora`, Ollama import) and point the
backend at it:

    SAYA_SLU_MODEL_URL=http://127.0.0.1:8080 SAYA_SLU_MODEL_NAME=slu-1.5b
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="Qwen/Qwen3-4B-Instruct-2507")
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--lr", type=float, default=2e-4)
    args = parser.parse_args()

    try:
        import torch
        from datasets import load_dataset
        from peft import LoraConfig, get_peft_model
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            Trainer,
            TrainingArguments,
        )
    except ImportError as exc:
        print(f"missing dependency: {exc}\n"
              "install with: pip install torch transformers peft datasets accelerate")
        return 1

    tokenizer = AutoTokenizer.from_pretrained(args.base)

    def collate(batch):
        """Simple right-padding collator (dicts of lists in, tensors out) -
        version-proof where DataCollatorForSeq2Seq grew strict input checks."""
        pad_id = tokenizer.pad_token_id or tokenizer.eos_token_id
        maxlen = max(len(item["input_ids"]) for item in batch)
        input_ids, labels, attention = [], [], []
        for item in batch:
            ids, lab = list(item["input_ids"]), list(item["labels"])
            pad = maxlen - len(ids)
            input_ids.append(ids + [pad_id] * pad)
            labels.append(lab + [-100] * pad)
            attention.append([1] * len(ids) + [0] * pad)
        return {
            "input_ids": torch.tensor(input_ids, dtype=torch.long),
            "labels": torch.tensor(labels, dtype=torch.long),
            "attention_mask": torch.tensor(attention, dtype=torch.long),
        }
    # CPU-first load then a single .to("cuda"): accelerate's device_map
    # dispatch segfaulted on Windows with the 4B model; eager attention
    # avoids the sdpa/flash kernels that crashed alongside it.
    model = AutoModelForCausalLM.from_pretrained(
        args.base, dtype=torch.bfloat16, attn_implementation="eager",
    )
    model.to("cuda")
    lora = LoraConfig(
        r=args.rank, lora_alpha=2 * args.rank, lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora)
    model.enable_input_require_grads()
    model.gradient_checkpointing_enable()

    dataset = load_dataset("json", data_files=str(Path(args.data)), split="train")

    def tokenize(row):
        # transformers 5.x returns a BatchEncoding dict by default; be
        # explicit so both list and dict shapes keep working.
        full_enc = tokenizer.apply_chat_template(
            row["messages"], tokenize=True, add_generation_prompt=False,
            return_dict=True,
        )
        full = list(full_enc["input_ids"])
        prompt_enc = tokenizer.apply_chat_template(
            row["messages"][:-1], tokenize=True, add_generation_prompt=True,
            return_dict=True,
        )
        prompt_len = len(prompt_enc["input_ids"])
        # Mask the prompt so loss is computed on the assistant JSON only.
        labels = [-100] * min(prompt_len, len(full)) + list(full[prompt_len:])
        # Hard length cap: bounds worst-case batch memory on this box's
        # temperamental CUDA allocator (no legit SLU row exceeds ~900 tokens).
        if len(full) > 1024:
            full = full[:1024]
            labels = labels[:1024]
        return {"input_ids": full, "labels": labels, "attention_mask": [1] * len(full)}

    tokenized = dataset.map(tokenize, remove_columns=dataset.column_names)

    trainer = Trainer(
        model=model,
        train_dataset=tokenized,
        args=TrainingArguments(
            output_dir=args.out,
            num_train_epochs=args.epochs,
            per_device_train_batch_size=args.batch,
            gradient_accumulation_steps=2,
            learning_rate=args.lr,
            bf16=True,
            logging_steps=10,
            save_strategy="steps",
            save_steps=130,
            report_to=[],
        ),
        data_collator=collate,
    )
    trainer.train()
    trainer.save_model(args.out)
    print(f"adapter saved to {args.out}; serve it and set SAYA_SLU_MODEL_URL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

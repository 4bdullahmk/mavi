#!/usr/bin/env python3
"""Opt-in, local-only LoRA training for a user-provided JSONL file.

Validation and preparation use only Python's standard library. Training is a separate
explicit command and requires a locally cached official Qwen model plus CUDA packages.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

OFFICIAL_MODEL = "Qwen/Qwen3-0.6B"
MAX_DATASET_BYTES = 100 * 1024 * 1024
MAX_LINE_BYTES = 1 * 1024 * 1024
MAX_ROWS = 50_000
MAX_TEXT_CHARS = 20_000
MAX_SEQUENCE_LENGTH = 1024
MIN_TOTAL_VRAM_GB = 8
MIN_FREE_VRAM_GB = 6
SECRET_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\b(?:password|passwd|secret|api[_ -]?key|access[_ -]?token|refresh[_ -]?token)\b\s*[:=]\s*\S+",
    r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}",
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b",
    r"\bsk-[A-Za-z0-9_-]{20,}\b",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b",
    r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----",
    r"https?://[^\s/:@]+:[^\s/@]+@[^\s/]+",
))


@dataclass(frozen=True)
class Example:
    prompt: str
    completion: str


class DatasetError(ValueError):
    pass


def data_root() -> Path:
    """Match the portable server's per-user data location without importing it."""
    if os.environ.get("MAVI_DATA_DIR"):
        return Path(os.environ["MAVI_DATA_DIR"]).expanduser().resolve()
    if os.environ.get("LOCALAPPDATA"):
        return (Path(os.environ["LOCALAPPDATA"]) / "Mavi").resolve()
    return (Path.home() / ".local" / "share" / "Mavi").resolve()


def _contains_credential(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def _example_from_row(value: object, line_number: int) -> Example:
    if not isinstance(value, dict):
        raise DatasetError(f"line {line_number}: each JSONL row must be an object")
    if set(value) == {"prompt", "completion"}:
        prompt, completion = value["prompt"], value["completion"]
    elif set(value) == {"messages"}:
        messages = value["messages"]
        if (not isinstance(messages, list) or len(messages) != 2 or
                any(not isinstance(message, dict) or set(message) != {"role", "content"}
                    for message in messages) or
                messages[0].get("role") != "user" or messages[1].get("role") != "assistant"):
            raise DatasetError(f"line {line_number}: messages must contain one user and one assistant text message")
        prompt, completion = messages[0]["content"], messages[1]["content"]
    else:
        raise DatasetError(f"line {line_number}: use only prompt/completion or a two-message user/assistant record")
    if not isinstance(prompt, str) or not isinstance(completion, str):
        raise DatasetError(f"line {line_number}: prompt and completion must be strings")
    prompt, completion = prompt.strip(), completion.strip()
    if not prompt or not completion:
        raise DatasetError(f"line {line_number}: prompt and completion must both be non-empty")
    if len(prompt) > MAX_TEXT_CHARS or len(completion) > MAX_TEXT_CHARS:
        raise DatasetError(f"line {line_number}: text exceeds the {MAX_TEXT_CHARS:,}-character limit")
    if _contains_credential(prompt) or _contains_credential(completion):
        raise DatasetError(f"line {line_number}: possible credential detected; remove it before training")
    return Example(prompt, completion)


def validate_dataset(path: str | Path) -> list[Example]:
    source = Path(path).expanduser()
    if source.suffix.lower() != ".jsonl":
        raise DatasetError("dataset must be a user-provided .jsonl file")
    try:
        source = source.resolve(strict=True)
        if not source.is_file():
            raise DatasetError("dataset path is not a file")
        size = source.stat().st_size
        if size == 0 or size > MAX_DATASET_BYTES:
            raise DatasetError(f"dataset must be between 1 byte and {MAX_DATASET_BYTES // (1024 * 1024)} MiB")
        examples: list[Example] = []
        with source.open("rb") as stream:
            line_number = 0
            while True:
                raw = stream.readline(MAX_LINE_BYTES + 1)
                if not raw:
                    break
                line_number += 1
                if len(raw) > MAX_LINE_BYTES:
                    raise DatasetError(f"line {line_number}: row exceeds the 1 MiB limit")
                try:
                    line = raw.decode("utf-8").strip()
                except UnicodeDecodeError as error:
                    raise DatasetError(f"line {line_number}: file must be UTF-8") from error
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as error:
                    raise DatasetError(f"line {line_number}: invalid JSON") from error
                examples.append(_example_from_row(row, line_number))
                if len(examples) > MAX_ROWS:
                    raise DatasetError(f"dataset exceeds the {MAX_ROWS:,}-row limit")
    except OSError as error:
        raise DatasetError("could not read the selected dataset") from error
    if not examples:
        raise DatasetError("dataset has no non-empty examples")
    return examples


def inspect_cuda() -> dict:
    """Read optional CUDA capacity without installing dependencies or loading weights."""
    if importlib.util.find_spec("torch") is None:
        return {"available": False, "reason": "PyTorch is not installed"}
    try:
        import torch
        if not torch.cuda.is_available():
            return {"available": False, "reason": "CUDA is unavailable"}
        properties = torch.cuda.get_device_properties(0)
        free_bytes, total_bytes = torch.cuda.mem_get_info(0)
        return {
            "available": True,
            "device": str(properties.name),
            "total_vram_gb": round(properties.total_memory / 1024 ** 3, 2),
            "free_vram_gb": round(free_bytes / 1024 ** 3, 2),
            "reported_total_gb": round(total_bytes / 1024 ** 3, 2),
        }
    except Exception:
        return {"available": False, "reason": "Could not inspect CUDA device capacity"}


def check_cuda_capacity(info: dict) -> tuple[bool, str]:
    if not info.get("available"):
        return False, str(info.get("reason", "CUDA is unavailable"))
    if info.get("total_vram_gb", 0) < MIN_TOTAL_VRAM_GB:
        return False, f"at least {MIN_TOTAL_VRAM_GB} GiB of GPU memory is recommended for this profile"
    if info.get("free_vram_gb", 0) < MIN_FREE_VRAM_GB:
        return False, f"at least {MIN_FREE_VRAM_GB} GiB of currently free GPU memory is required"
    return True, "CUDA capacity meets the conservative minimum"


def safe_output_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
        raise ValueError("output name must be 1–64 letters, digits, dots, underscores or hyphens")
    if value in {".", ".."}:
        raise ValueError("output name is invalid")
    return value


def dependency_status() -> dict[str, bool]:
    return {name: importlib.util.find_spec(name) is not None
            for name in ("torch", "transformers", "peft", "accelerate")}


def model_cached_locally() -> bool:
    """Check common Hugging Face cache locations without contacting the Hub."""
    candidates = []
    if os.environ.get("HF_HUB_CACHE"):
        candidates.append(Path(os.environ["HF_HUB_CACHE"]))
    if os.environ.get("HF_HOME"):
        candidates.append(Path(os.environ["HF_HOME"]) / "hub")
    candidates.extend((Path.home() / ".cache" / "huggingface" / "hub",
                      Path(os.environ.get("LOCALAPPDATA", Path.home())) / "huggingface" / "hub"))
    cache_name = "models--" + OFFICIAL_MODEL.replace("/", "--")
    for root in candidates:
        model_cache = root.expanduser() / cache_name
        snapshots = model_cache / "snapshots"
        if snapshots.is_dir() and any((snapshot / "config.json").is_file() for snapshot in snapshots.iterdir()):
            return True
    return False


def _train(examples: list[Example], output_name: str, max_steps: int | None = None) -> Path:
    """Run a small, CUDA-only LoRA job, using cached model files only."""
    # Offline flags are set before Transformers/Hub are imported. No model downloads occur.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    try:
        import torch
        from peft import LoraConfig, TaskType, get_peft_model
        from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainingArguments
    except ImportError as error:
        raise RuntimeError("training needs locally installed PyTorch, Transformers, PEFT and Accelerate") from error

    capacity_ok, reason = check_cuda_capacity(inspect_cuda())
    if not capacity_ok:
        raise RuntimeError(f"training stopped: {reason}")

    name = safe_output_name(output_name)
    adapter_root = data_root() / "models" / "adapters"
    target = adapter_root / name
    if target.exists():
        raise RuntimeError("adapter output already exists; choose a new output name")
    adapter_root.mkdir(parents=True, exist_ok=True)
    staging = adapter_root / f".{name}.partial-{uuid.uuid4().hex}"
    staging.mkdir(mode=0o700)
    try:
        tokenizer = AutoTokenizer.from_pretrained(OFFICIAL_MODEL, local_files_only=True, trust_remote_code=False)
        model = AutoModelForCausalLM.from_pretrained(
            OFFICIAL_MODEL, local_files_only=True, trust_remote_code=False,
            torch_dtype=torch.float16, low_cpu_mem_usage=True)
        model.to("cuda")
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        model.config.use_cache = False
        lora = LoraConfig(
            task_type=TaskType.CAUSAL_LM, r=8, lora_alpha=16, lora_dropout=0.05,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        )
        model = get_peft_model(model, lora)

        records = []
        for example in examples:
            prefix = tokenizer.apply_chat_template(
                [{"role": "user", "content": example.prompt}],
                tokenize=False, add_generation_prompt=True)
            full = prefix + example.completion + (tokenizer.eos_token or "")
            encoded = tokenizer(full, truncation=True, max_length=MAX_SEQUENCE_LENGTH)
            prefix_ids = tokenizer(prefix, add_special_tokens=False)["input_ids"]
            prefix_size = min(len(prefix_ids), len(encoded["input_ids"]))
            labels = [-100] * prefix_size + encoded["input_ids"][prefix_size:]
            if not any(label != -100 for label in labels):
                raise RuntimeError("an example's prompt exceeds the training sequence limit")
            encoded["labels"] = labels
            records.append(encoded)

        class TrainingRows(torch.utils.data.Dataset):
            def __len__(self) -> int:
                return len(records)

            def __getitem__(self, index: int) -> dict:
                return records[index]

        def collate(features: list[dict]) -> dict:
            max_len = max(len(feature["input_ids"]) for feature in features)
            batch = {"input_ids": [], "attention_mask": [], "labels": []}
            for feature in features:
                padding = max_len - len(feature["input_ids"])
                batch["input_ids"].append(feature["input_ids"] + [tokenizer.pad_token_id] * padding)
                batch["attention_mask"].append(feature["attention_mask"] + [0] * padding)
                batch["labels"].append(feature["labels"] + [-100] * padding)
            return {key: torch.tensor(value, dtype=torch.long) for key, value in batch.items()}

        arguments = TrainingArguments(
            output_dir=str(staging / "trainer"), num_train_epochs=1,
            max_steps=max_steps if max_steps and max_steps > 0 else -1,
            per_device_train_batch_size=1, gradient_accumulation_steps=4,
            learning_rate=2e-4, logging_steps=5, save_strategy="no",
            report_to="none", fp16=True, gradient_checkpointing=True,
            remove_unused_columns=False, dataloader_num_workers=0,
        )
        trainer = Trainer(model=model, args=arguments, train_dataset=TrainingRows(), data_collator=collate)
        trainer.train()
        model.save_pretrained(staging / "adapter")
        tokenizer.save_pretrained(staging / "adapter")
        metadata = {
            "base_model": OFFICIAL_MODEL,
            "method": "LoRA",
            "examples": len(examples),
            "sequence_length": MAX_SEQUENCE_LENGTH,
            "completed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        (staging / "training.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        (staging / "training.json").chmod(0o600)
        staging.replace(target)
        return target / "adapter"
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare or explicitly train a local Mavi LoRA adapter.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "prepare", "train"):
        sub = commands.add_parser(name)
        sub.add_argument("--dataset", required=True, help="User-provided UTF-8 JSONL file; never downloaded or copied")
        if name in ("prepare", "train"):
            sub.add_argument("--name", help="Local adapter folder name")
        if name == "train":
            sub.add_argument("--confirm-local-training", action="store_true",
                             help="Confirm that this opt-in job may fit an adapter on the selected file")
            sub.add_argument("--max-steps", type=int, help="Optional small smoke-run cap; defaults to one epoch")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        examples = validate_dataset(args.dataset)
        print(f"Validated {len(examples):,} examples. Dataset contents are not printed or copied.")
        if args.command == "validate":
            print("Validation only; no model, GPU or output files accessed.")
            return 0
        target_root = data_root() / "models" / "adapters"
        name = safe_output_name(args.name) if args.name else "<choose-a-name>"
        print(f"Official base: {OFFICIAL_MODEL}")
        print(f"Adapter destination: {target_root / name}")
        print("The dataset path and examples are not written to adapter metadata.")
        if args.command == "prepare":
            dependencies = dependency_status()
            print("Optional dependencies: " + ", ".join(f"{key}={'available' if value else 'missing'}" for key, value in dependencies.items()))
            capacity = inspect_cuda()
            ok, capacity_message = check_cuda_capacity(capacity)
            if capacity.get("available"):
                print(f"CUDA device: {capacity['device']} · {capacity['free_vram_gb']} GiB free / {capacity['total_vram_gb']} GiB total")
            else:
                print(f"CUDA check: {capacity_message}")
            print(f"Official base model cache: {'present' if model_cached_locally() else 'not found; training will require it to be cached first'}")
            print("Prepare is a dry run. No training or model download occurred.")
            return 0
        if not args.confirm_local_training:
            raise RuntimeError("training requires --confirm-local-training")
        if not args.name:
            raise RuntimeError("training requires --name")
        if args.max_steps is not None and args.max_steps < 1:
            raise RuntimeError("--max-steps must be a positive integer")
        missing = [name for name, available in dependency_status().items() if not available]
        if missing:
            raise RuntimeError("training dependencies are missing: " + ", ".join(missing))
        info = inspect_cuda()
        capacity_ok, capacity_message = check_cuda_capacity(info)
        if not capacity_ok:
            raise RuntimeError(f"training stopped: {capacity_message}")
        result = _train(examples, args.name, args.max_steps)
        print(f"Local LoRA run completed. Review the adapter at: {result}")
        print("Mavi's active model was not changed. No dataset, weights or credentials were uploaded.")
        return 0
    except (DatasetError, OSError, RuntimeError, ValueError) as error:
        print(f"Training setup: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

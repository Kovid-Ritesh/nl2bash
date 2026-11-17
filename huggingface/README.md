---
language:
  - en
base_model: Qwen/Qwen2.5-Coder-3B-Instruct
library_name: peft
pipeline_tag: text-generation
tags:
  - qwen2
  - qwen2.5-coder
  - lora
  - qlora
  - bash
  - shell
  - code
  - nl2bash
---

# Qwen2.5-Coder-3B NL2SH ALFA QLoRA

A LoRA adapter for translating English requests into Linux/Bash commands, built
on **Qwen/Qwen2.5-Coder-3B-Instruct**. This repository contains adapter weights
and a tokenizer; download the base model separately when loading with PEFT.

The adapter powers [nl2bash](https://github.com/Kovid-Ritesh/nl2bash), a local
terminal agent with command review and limited file-operation undo. Those
execution safeguards belong to the application, **not to the model weights**.

## Model details

| Property | Value |
| --- | --- |
| Adapter repository | `kovidritesh/qwen2.5-coder-3b-nl2sh-alfa-qlora` |
| Base model | `Qwen/Qwen2.5-Coder-3B-Instruct` |
| Task | Natural-language-to-shell command generation |
| Adapter format | PEFT LoRA, safetensors |
| Task type | `CAUSAL_LM` |
| LoRA rank | 64 |
| LoRA alpha | 32 |
| LoRA dropout | 0.1 |
| Bias | `none` |
| DoRA / rank-stabilized LoRA | Both disabled in the saved configuration |
| Target modules | `q_proj`, `k_proj`, `v_proj`, `o_proj`, `gate_proj`, `up_proj`, `down_proj` |

These settings are taken from the saved
[adapter configuration](https://huggingface.co/kovidritesh/qwen2.5-coder-3b-nl2sh-alfa-qlora/blob/2c851234f1c731fbbdbf848d9e080238653a1bb7/adapter_config.json).
The release name identifies QLoRA; its training-time quantization recipe is not
specified by that adapter configuration. QLoRA training does not require every
inference deployment to load the base in 4-bit. The provided serving notebook
uses BF16/FP16 base weights plus this adapter.

## Intended use

- Suggest Bash commands for file discovery, inspection, and other shell tasks.
- Experiment with natural-language terminal interfaces.
- Evaluate generated commands in disposable test directories or containers.

The model produces text. Review that text before executing it. Commands can be
wrong, destructive, environment-specific, or inconsistent with your intent.
Do not rely on the model to classify its own output as safe.

## Quickstart with Transformers and PEFT

Install a CUDA-compatible PyTorch build for your hardware, then:

```bash
pip install "transformers>=4.51,<5" "peft>=0.15,<0.19" "accelerate>=1.6,<2"
```

```python
import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

base_id = "Qwen/Qwen2.5-Coder-3B-Instruct"
adapter_id = "kovidritesh/qwen2.5-coder-3b-nl2sh-alfa-qlora"
base_revision = "488639f1ff808d1d3d0ba301aef8c11461451ec5"
adapter_revision = "2c851234f1c731fbbdbf848d9e080238653a1bb7"

has_cuda = torch.cuda.is_available()
device = "cuda:0" if has_cuda else "cpu"
dtype = (
    torch.bfloat16 if has_cuda and torch.cuda.is_bf16_supported()
    else torch.float16 if has_cuda
    else torch.float32
)
tokenizer = AutoTokenizer.from_pretrained(adapter_id, revision=adapter_revision)
if tokenizer.pad_token_id is None:
    tokenizer.pad_token = tokenizer.eos_token

base = AutoModelForCausalLM.from_pretrained(
    base_id,
    revision=base_revision,
    torch_dtype=dtype,
    device_map={"": device},
)
model = PeftModel.from_pretrained(base, adapter_id, revision=adapter_revision)
model.eval()

messages = [
    {"role": "system", "content": "Translate the request into a shell command. Return only the command."},
    {"role": "user", "content": "Find all Python files recursively in the current directory."},
]
prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
inputs = tokenizer(prompt, return_tensors="pt", add_special_tokens=False).to(device)
with torch.inference_mode():
    output = model.generate(
        **inputs,
        max_new_tokens=128,
        do_sample=False,
        pad_token_id=tokenizer.pad_token_id,
        eos_token_id=tokenizer.eos_token_id,
    )
print(tokenizer.decode(output[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True))
```

An example response observed during application testing was:

```bash
find . -name "*.py"
```

This is a smoke-test example, not a correctness guarantee or benchmark result.
The system message above is the application's inference prompt; the original
training prompt format has not been documented. The saved tokenizer's chat
template is used for formatting.

## Serve and test an endpoint

The [Kaggle serving notebook](https://github.com/Kovid-Ritesh/nl2bash/blob/main/notebooks/nl2sh_kaggle_ngrok.ipynb)
loads this adapter on the configured base and exposes a bearer-authenticated
FastAPI endpoint through ngrok. Add `NGROK_AUTHTOKEN` and `NL2SH_API_KEY` as Kaggle
Secrets, enable Internet and a GPU, and run the cells in order.

The supplied server supports `POST /v1/chat/completions` with `model="nl2sh"`,
plain-text messages, and `stream=false`. Its 4,096-token context budget and
512-token maximum requested output are **application limits**, not a claim about
the base model's full context capacity. The endpoint ends with the notebook session.

Use the [inference testing notebook](https://github.com/Kovid-Ritesh/nl2bash/blob/main/notebooks/nl2sh_inference_test.ipynb)
to try prompts without executing the replies. Use the
[CLI](https://github.com/Kovid-Ritesh/nl2bash) for local execution with review.

## Training information

The saved adapter configuration provides the LoRA settings listed above. Dataset
composition, train/validation splits, preprocessing, optimizer configuration,
learning-rate schedule, training hardware, and the exact training command have
not been documented in this model card. No values are inferred from the model
name or presented as established training facts.

## Evaluation and limitations

No held-out accuracy, execution-success benchmark, safety benchmark, or comparison
with the base model is reported here. CLI tests verify application behavior;
they do not measure model quality. Evaluate generated commands on representative
tasks and inspect their effects before adopting the model for your environment.

Known practical limitations include:

- Generated commands may delete or overwrite data, expose files, or contact remote systems.
- Linux utilities, flags, paths, quoting, and permissions vary across environments.
- A plausible command can still implement the wrong task.
- Prompt injection and unsafe requests are not prevented by this adapter alone.
- The model does not provide rollback. The CLI's `/undo` applies only to supported
  managed file operations; arbitrary programs and Bash expressions have no general undo.

## License

The base model lists the
[Qwen Research License](https://huggingface.co/Qwen/Qwen2.5-Coder-3B-Instruct/blob/main/LICENSE).
The adapter's existing model card does not specify a separate adapter license.
This README does not assign a new license to the adapter or imply Apache-2.0/MIT
terms. Review the base model's terms and obtain clarification from the adapter
maintainer before relying on redistribution or commercial-use permissions.

## Links

- [Adapter repository](https://huggingface.co/kovidritesh/qwen2.5-coder-3b-nl2sh-alfa-qlora)
- [Base model](https://huggingface.co/Qwen/Qwen2.5-Coder-3B-Instruct)
- [nl2bash source and documentation](https://github.com/Kovid-Ritesh/nl2bash)
- [PEFT model loading](https://huggingface.co/docs/peft/package_reference/peft_model)

"""Deploy with: modal deploy modal_app.py"""

import modal

BASE_MODEL = "Qwen/Qwen2.5-Coder-3B-Instruct"
BASE_REVISION = "488639f1ff808d1d3d0ba301aef8c11461451ec5"
ADAPTER_MODEL = "kovidritesh/qwen2.5-coder-3b-nl2sh-alfa-qlora"
ADAPTER_REVISION = "2c851234f1c731fbbdbf848d9e080238653a1bb7"
MODEL_NAME = "nl2sh"
BASE_PATH = "/models/base"
ADAPTER_PATH = "/models/adapter"


def download_models():
    import json
    from pathlib import Path

    from huggingface_hub import snapshot_download

    snapshot_download(
        BASE_MODEL,
        revision=BASE_REVISION,
        local_dir=BASE_PATH,
        allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model"],
    )
    snapshot_download(
        ADAPTER_MODEL,
        revision=ADAPTER_REVISION,
        local_dir=ADAPTER_PATH,
        allow_patterns=["*.json", "*.safetensors", "*.txt"],
    )
    config = json.loads((Path(ADAPTER_PATH) / "adapter_config.json").read_text())
    if config["base_model_name_or_path"] != BASE_MODEL or config["r"] != 64:
        raise ValueError("Adapter base model or rank changed; update the server configuration.")


# Bake the pinned weights into the image so cold starts do not download them.
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("build-essential", "libnuma1")
    .pip_install("vllm==0.34.0")
    .env({"HF_HUB_DISABLE_TELEMETRY": "1", "VLLM_NO_USAGE_STATS": "1"})
    .run_function(download_models, timeout=1800)
)

app = modal.App("qwen-nl2sh")


@app.function(
    image=image,
    gpu="L4",
    min_containers=0,
    max_containers=1,
    scaledown_window=300,
    timeout=600,
)
@modal.concurrent(max_inputs=16)
@modal.web_server(8000, startup_timeout=600, requires_proxy_auth=True)
def serve():
    import subprocess

    subprocess.Popen(
        [
            "vllm", "serve", BASE_PATH,
            "--served-model-name", "qwen-base",
            "--tokenizer", ADAPTER_PATH,
            "--host", "0.0.0.0",
            "--port", "8000",
            "--dtype", "bfloat16",
            "--max-model-len", "4096",
            "--max-num-seqs", "16",
            "--gpu-memory-utilization", "0.90",
            "--enable-lora",
            "--max-loras", "1",
            "--max-lora-rank", "64",
            "--lora-modules", f"{MODEL_NAME}={ADAPTER_PATH}",
            "--generation-config", "vllm",
            "--enforce-eager",
        ]
    )

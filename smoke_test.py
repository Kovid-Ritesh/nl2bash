"""Check a deployed endpoint: python smoke_test.py https://YOUR-URL.modal.run"""

import json
import os
import sys
import urllib.request


def request(base_url, path, payload=None):
    headers = {
        "Modal-Key": os.environ["MODAL_PROXY_KEY"],
        "Modal-Secret": os.environ["MODAL_PROXY_SECRET"],
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=600) as response:
        return json.load(response)


def main():
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        raise SystemExit("Usage: python smoke_test.py https://YOUR-URL.modal.run")
    base_url = sys.argv[1]
    models = request(base_url, "/v1/models")
    if not any(model["id"] == "nl2sh" for model in models["data"]):
        raise RuntimeError("The nl2sh adapter is missing from the server's model list.")
    result = request(
        base_url,
        "/v1/chat/completions",
        {
            "model": "nl2sh",
            "messages": [
                {"role": "system", "content": "Translate the request into a shell command. Return only the command."},
                {"role": "user", "content": "List all files including hidden files in the current directory."},
            ],
            "temperature": 0,
            "max_tokens": 128,
        },
    )
    content = result["choices"][0]["message"]["content"]
    if not content or not content.strip():
        raise RuntimeError(f"Empty generation: {result}")
    print(content)
    print("PASS: adapter is registered and returned a nonempty completion.")


if __name__ == "__main__":
    main()

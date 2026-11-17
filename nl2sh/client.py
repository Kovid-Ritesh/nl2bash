import json
import re
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def endpoint_root(url):
    url = url.strip().rstrip("/")
    url = url.removesuffix("/v1/chat/completions").removesuffix("/v1")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Endpoint must be an HTTPS URL without embedded credentials.")
    if parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("Use the endpoint root, /v1 base URL, or /v1/chat/completions URL.")
    return url


def generate_command(endpoint, api_key, prompt):
    if not prompt.strip() or len(prompt) > 12000:
        raise ValueError("Enter a prompt between 1 and 12000 characters.")
    payload = {
        "model": "nl2sh",
        "messages": [
            {"role": "system", "content": (
                "Translate the request into a single shell command. Return only the command, "
                "without explanation or markdown. Use Bash syntax when needed. "
                "The command will be reviewed locally before execution."
            )},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": 256,
        "stream": False,
    }
    request = urllib.request.Request(
        endpoint_root(endpoint) + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json",
                 "ngrok-skip-browser-warning": "1"},
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=180) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("Endpoint response exceeded 64 KiB.")
        data = json.loads(raw)
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError("The model command was truncated; rephrase the prompt.")
        command = choice["message"]["content"]
        if not isinstance(command, str):
            raise ValueError("Endpoint did not return a text command.")
        # Accept one complete fenced command, never extract a command from prose.
        match = re.fullmatch(r"```(?:bash|sh|shell)?\s*\n([^\n]+)\n```", command.strip())
        return match.group(1) if match else command.strip()
    except urllib.error.HTTPError as error:
        hints = {401: "API key rejected", 429: "GPU busy; retry later",
                 404: "endpoint not found or ngrok tunnel offline", 502: "serving notebook offline"}
        raise ValueError(f"Inference HTTP {error.code}: {hints.get(error.code, 'request failed')}") from None
    except (urllib.error.URLError, TimeoutError):
        raise ValueError("Cannot reach inference endpoint. Check the URL and serving notebook.") from None
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        raise ValueError("Endpoint returned an invalid chat-completion response.") from None

"""Smoke-test the local vLLM OpenAI-compatible server (compose.models.yml).

Usage:
  uv run scripts/test_vllm_gemma.py
  uv run scripts/test_vllm_gemma.py --prompt "Say hello in one sentence"
  uv run scripts/test_vllm_gemma.py --base-url http://localhost:8001 --timeout 300
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import requests

DEFAULT_BASE_URL = "http://localhost:8001"
DEFAULT_MODEL = "gemma4-e2b"


def wait_until_ready(base_url: str, timeout_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    models_url = f"{base_url.rstrip('/')}/v1/models"
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            response = requests.get(models_url, timeout=5)
            if response.status_code == 200:
                payload = response.json()
                print(f"Ready: {models_url}", flush=True)
                print(json.dumps(payload, indent=2), flush=True)
                return payload
            last_error = RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
        except requests.RequestException as exc:
            last_error = exc

        print(f"Waiting for {models_url} ... ({last_error})", flush=True)
        time.sleep(2)

    raise TimeoutError(f"Server not ready after {timeout_s:.0f}s: {last_error}")


def chat_completion(
    *,
    base_url: str,
    model: str,
    prompt: str,
    temperature: float,
    timeout_s: float,
) -> dict:
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "stream": False,
    }
    print(f"\nPOST {url}")
    print(json.dumps(body, indent=2))

    response = requests.post(url, json=body, timeout=timeout_s)
    print(f"\nHTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        print(response.text)
        response.raise_for_status()
        raise

    print(json.dumps(payload, indent=2))
    response.raise_for_status()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt", default="ping")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument(
        "--ready-timeout",
        type=float,
        default=600,
        help="Seconds to wait for /v1/models before giving up",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120,
        help="HTTP timeout for the chat completion request",
    )
    parser.add_argument(
        "--skip-ready-check",
        action="store_true",
        help="Do not poll /v1/models first",
    )
    args = parser.parse_args()

    try:
        if not args.skip_ready_check:
            wait_until_ready(args.base_url, args.ready_timeout)

        payload = chat_completion(
            base_url=args.base_url,
            model=args.model,
            prompt=args.prompt,
            temperature=args.temperature,
            timeout_s=args.timeout,
        )
    except (requests.RequestException, TimeoutError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        print("ERROR: unexpected response shape", file=sys.stderr)
        return 1

    print("\n--- assistant ---")
    try:
        print(content)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((content + "\n").encode("utf-8", errors="replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

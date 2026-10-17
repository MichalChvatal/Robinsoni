#!/usr/bin/env python3
"""Generate an image using MAI Image model through Azure APIM + OpenAI-compatible API.

This script tries the common Azure APIM patterns:
1. model=<deployment-or-model-name>
2. headers with api-key
3. URL ending with /openai/v1/

Usage:
    set API_KEY=...
    python generate_image_mai_azure.py --prompt "Minimal modern logo for TriaLink"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib import request, error


def pick_api_key(arg_key: str | None) -> str:
    if arg_key:
        return arg_key
    key = os.getenv("API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("IMAGE_API_KEY")
    if not key:
        raise SystemExit("Missing API key. Pass --api-key or set API_KEY.")
    return key


def build_client_config(base_url: str | None, deployment: str | None):
    url = (base_url or os.getenv("IMAGE_API_BASE_URL") or "https://apim-rakathon-q7146n.azure-api.net/openai/v1/").rstrip("/") + "/"
    model_candidates = []
    if deployment:
        model_candidates.append(deployment)
    env_model = os.getenv("IMAGE_MODEL") or os.getenv("MODEL") or "mai-image-2-5-global"
    model_candidates.append(env_model)
    if env_model != "mai-image-2-5-global":
        model_candidates.append("mai-image-2-5-global")
    # dedupe while preserving order
    seen = set()
    ordered = []
    for item in model_candidates:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return url, ordered


def post_json(url: str, headers: dict, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=180) as resp:
            body = resp.read()
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Connection error: {exc}") from exc

    try:
        return json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Invalid JSON response: {body!r}") from exc


def save_image(response: dict, output_path: Path):
    data = response.get("data") or []
    if not data:
        raise RuntimeError(f"No image data returned: {response}")

    first = data[0]
    if "b64_json" in first and first["b64_json"]:
        import base64
        output_path.write_bytes(base64.b64decode(first["b64_json"]))
        return

    if "url" in first and first["url"]:
        with request.urlopen(request.Request(first["url"], method="GET"), timeout=180) as resp:
            output_path.write_bytes(resp.read())
        return

    raise RuntimeError(f"Unsupported image payload: {first}")


def try_generate(base_url: str, api_key: str, model_name: str, prompt: str, size: str, output_path: Path):
    endpoint = base_url.rstrip("/") + "/images/generations"
    headers = {
        "Content-Type": "application/json",
        "api-key": api_key,
    }
    payload = {"model": model_name, "prompt": prompt, "size": size}
    response = post_json(endpoint, headers, payload)
    save_image(response, output_path)
    print(f"Success with model='{model_name}' -> {output_path}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Generate image with MAI Image through Azure APIM.")
    parser.add_argument("--prompt", required=True, help="Prompt for the image model")
    parser.add_argument("--api-key", help="API key; default from API_KEY/OPENAI_API_KEY/IMAGE_API_KEY")
    parser.add_argument("--base-url", default=os.getenv("IMAGE_API_BASE_URL"), help="Azure APIM base URL")
    parser.add_argument("--deployment", help="Optional deployment name (if different from model name)")
    parser.add_argument("--model", default=os.getenv("IMAGE_MODEL") or "mai-image-2-5-global", help="Model name or deployment name")
    parser.add_argument("--size", default=os.getenv("IMAGE_SIZE") or "1024x1024")
    parser.add_argument("--output", type=Path, default=Path("generated-logo.png"), help="Output file")
    args = parser.parse_args()

    api_key = pick_api_key(args.api_key)
    base_url = args.base_url or "https://apim-rakathon-q7146n.azure-api.net/openai/v1/"
    output_path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    candidates = []
    if args.deployment:
        candidates.append(args.deployment)
    candidates.append(args.model)
    if "mai-image-2-5-global" not in candidates:
        candidates.append("mai-image-2-5-global")

    errors = []
    for model in candidates:
        try:
            try_generate(base_url, api_key, model, args.prompt, args.size, output_path)
            return 0
        except Exception as exc:
            errors.append((model, str(exc)))
            print(f"Failed model='{model}': {exc}", file=sys.stderr)

    print("\nTried models:", file=sys.stderr)
    for model, err in errors:
        print(f" - {model}: {err}", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())

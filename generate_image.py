#!/usr/bin/env python3
"""Generate an image from a prompt using an OpenAI-compatible or Azure OpenAI API.

Examples:
  python generate_image.py --api-key "$OPENAI_API_KEY" --provider openai --model gpt-image-1 --prompt "Minimalist logo for TriaLink"
  python generate_image.py --api-key "$AZURE_API_KEY" --provider azure --base-url "https://<resource>.openai.azure.com" --deployment <deployment-name> --prompt "Minimalist logo for TriaLink" --output trialink-logo.png
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path
from urllib import error, request


def _env_or_arg(value: str | None, env_var: str) -> str | None:
    if value is not None:
        return value
    return os.getenv(env_var)


def _post_json(url: str, headers: dict[str, str], payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=180) as resp:
            body = resp.read()
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API request failed ({exc.code}): {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"API connection failed: {exc}") from exc

    try:
        return json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Invalid API response: {body!r}") from exc


def _build_api_url(args: argparse.Namespace) -> str:
    base_url = _env_or_arg(args.base_url, "IMAGE_API_BASE_URL")
    provider = args.provider.lower()
    if provider == "azure":
        if not base_url:
            raise ValueError("Azure provider needs --base-url or IMAGE_API_BASE_URL, e.g. https://<resource>.openai.azure.com")
        base = base_url.rstrip("/")
        api_version = args.api_version or os.getenv("IMAGE_API_VERSION", "2024-02-01")
        return f"{base}/openai/deployments/{args.deployment}/images/generations?api-version={api_version}"
    if base_url:
        if base_url.rstrip("/").endswith("/v1"):
            return base_url.rstrip("/") + "/images/generations"
        return base_url.rstrip("/") + "/v1/images/generations"
    return "https://api.openai.com/v1/images/generations"


def _write_image_payload(payload: dict, output_path: Path) -> None:
    data = payload.get("data") or []
    if not data:
        raise RuntimeError(f"API did not return an image: {payload}")

    first = data[0]
    if "b64_json" in first and first["b64_json"]:
        image_bytes = base64.b64decode(first["b64_json"])
        output_path.write_bytes(image_bytes)
        return

    if "url" in first and first["url"]:
        image_url = first["url"]
        req = request.Request(image_url, method="GET")
        with request.urlopen(req, timeout=180) as resp:
            output_path.write_bytes(resp.read())
        return

    raise RuntimeError(f"Unsupported image payload returned by API: {first}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an image from a prompt via an OpenAI-compatible API.")
    parser.add_argument("--prompt", help="Text prompt for the image generation model.")
    parser.add_argument("--prompt-file", type=Path, help="Optional file containing the prompt text.")
    parser.add_argument("--output", type=Path, default=Path("generated-image.png"), help="Destination image path.")
    parser.add_argument("--api-key", help="API key. Alternative: IMAGE_API_KEY env var.")
    parser.add_argument("--provider", choices=["openai", "azure"], default="openai", help="API provider type.")
    parser.add_argument("--base-url", help="Custom API base URL. Alternative: IMAGE_API_BASE_URL.")
    parser.add_argument("--deployment", help="Azure deployment name. Required for Azure provider.")
    parser.add_argument("--model", default=os.getenv("IMAGE_API_MODEL", "gpt-image-1"), help="Model name to request.")
    parser.add_argument("--size", default=os.getenv("IMAGE_API_SIZE", "1024x1024"), help="Image size, e.g. 1024x1024 or 1536x1024.")
    parser.add_argument("--n", type=int, default=int(os.getenv("IMAGE_API_COUNT", "1")), help="Number of images to produce.")
    parser.add_argument("--api-version", help="Azure OpenAI API version; default 2024-02-01.")
    args = parser.parse_args()

    prompt = args.prompt
    if args.prompt_file:
        prompt = args.prompt_file.read_text(encoding="utf-8")
    if not prompt:
        raise SystemExit("A prompt is required. Pass --prompt or --prompt-file.")

    api_key = _env_or_arg(args.api_key, "IMAGE_API_KEY")
    if not api_key:
        raise SystemExit("An API key is required. Pass --api-key or set IMAGE_API_KEY.")

    if args.provider == "azure" and not args.deployment:
        raise SystemExit("Azure provider requires --deployment.")

    url = _build_api_url(args)
    headers = {"Content-Type": "application/json"}
    if args.provider == "azure":
        headers["api-key"] = api_key
    else:
        headers["Authorization"] = f"Bearer {api_key}"

    if "apim-rakathon" in (args.base_url or "").lower() or "apim-rakathon" in url.lower():
        headers["api-key"] = api_key

    payload = {
        "model": args.model,
        "prompt": prompt,
        "size": args.size,
        "n": args.n,
    }

    response = _post_json(url, headers, payload)
    output_path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_image_payload(response, output_path)
    print(f"Saved image to {output_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)

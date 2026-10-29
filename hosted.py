"""Render WSGI entry point; inference runs on DigitalOcean, not on Render."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from collections import deque
from http import HTTPStatus
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from app import STATIC, ask_question, generate, normalize_payload


class InferenceUnavailable(RuntimeError):
    """An upstream failure that can be shown without exposing request details."""


class DemoLimit(RuntimeError):
    pass


class ProviderBusy(DemoLimit):
    pass


class DigitalOceanClient:
    endpoint = "https://inference.do-ai.run/v1/chat/completions"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def complete(self, body: dict[str, object]) -> str:
        # llama.cpp's schema extension is not part of DO's documented API.
        # Ask for JSON in the prompt, then validate it in the shared app code.
        schema = body["response_format"]["schema"]
        messages = [dict(message) for message in body["messages"]]
        messages[0]["content"] += (
            " Output only a JSON object, without Markdown or commentary. "
            "The JSON must follow this schema: " + json.dumps(schema)
        )
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": body.get("temperature", 0.35),
            "max_completion_tokens": 1024 if "lines" in schema["properties"] else 160,
            "stream": False,
        }
        request = Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=60) as response:
                raw = response.read(131073)
            if len(raw) > 131072:
                raise InferenceUnavailable("The hosted model returned too much data. Try again.")
            result = json.loads(raw)
            choice = result["choices"][0]
            if choice.get("finish_reason") == "length":
                raise InferenceUnavailable("The model ran out of space. Try shorter notes.")
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Empty completion")
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            if content.startswith("```") and content.endswith("```"):
                content = re.sub(r"^```(?:json)?\s*", "", content)[:-3].strip()
            return content
        except HTTPError as exc:
            # Never log prompts, API keys, or the upstream response body.
            sys.stderr.write(f"DigitalOcean inference returned HTTP {exc.code}\n")
            if exc.code == 429:
                raise ProviderBusy("The model provider is busy. Wait a minute before trying again.") from None
            raise InferenceUnavailable("The hosted model is unavailable. Please try again later.") from None
        except (URLError, TimeoutError, OSError) as exc:
            raise InferenceUnavailable("The hosted model could not be reached. Please try again.") from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise InferenceUnavailable("The hosted model returned an unreadable response. Please try again.") from None


class HostedApplication:
    def __init__(self, api_key: str, model: str, public_origin: str, hourly_limit: int = 60):
        self.client = DigitalOceanClient(api_key, model)
        self.configured = bool(api_key and model)
        self.public_origin = public_origin.rstrip("/")
        self.hourly_limit = max(1, hourly_limit)
        self.requests: deque[float] = deque()
        self.lock = threading.Lock()
        self.inference_slot = threading.BoundedSemaphore(1)
        self.cooldown_until = 0.0

    def reserve_request(self) -> None:
        now = time.monotonic()
        with self.lock:
            while self.requests and self.requests[0] <= now - 3600:
                self.requests.popleft()
            if now < self.cooldown_until:
                raise DemoLimit("The model provider is busy. Wait a minute before trying again.")
            if len(self.requests) >= self.hourly_limit:
                raise DemoLimit("This public demo has reached its hourly limit. Please try later.")
            self.requests.append(now)

    def __call__(self, environ, start_response):
        def send(status: int, data: object, mime: str = "application/json; charset=utf-8"):
            content = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
            headers = [
                ("Content-Type", mime),
                ("Content-Length", str(len(content))),
                ("Cache-Control", "no-store"),
                ("X-Content-Type-Options", "nosniff"),
                ("Referrer-Policy", "no-referrer"),
                ("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"),
            ]
            if status == 429:
                headers.append(("Retry-After", "60"))
            start_response(f"{status} {HTTPStatus(status).phrase}", headers)
            return [content]

        path = environ.get("PATH_INFO", "/")
        method = environ.get("REQUEST_METHOD", "GET")
        if method == "GET":
            if path == "/healthz":
                # A liveness check must never issue a billable inference request.
                return send(200, {"status": "ok"})
            if path == "/api/status":
                return send(200, {
                    "ready": self.configured,
                    "local_only": False,
                    "mode": "hosted",
                    "provider": "DigitalOcean",
                    "model": self.client.model,
                    "error": None if self.configured else "Hosted demo setup is incomplete.",
                })
            files = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/style.css": ("style.css", "text/css; charset=utf-8"),
                "/app.js": ("app.js", "application/javascript; charset=utf-8"),
            }
            if path not in files:
                return send(404, {"error": "Not found"})
            filename, mime = files[path]
            content = (STATIC / filename).read_bytes()
            if path == "/":
                content = content.replace(b'data-mode="local"', b'data-mode="hosted"', 1)
            return send(200, content, mime)

        if method != "POST":
            return send(405, {"error": "Method not allowed"})
        if path not in {"/api/generate", "/api/question"}:
            return send(404, {"error": "Not found"})
        origin = environ.get("HTTP_ORIGIN")
        if origin and (not self.public_origin or origin != self.public_origin):
            return send(403, {"error": "Please use this demo's own page."})
        if environ.get("CONTENT_TYPE", "").split(";")[0].strip().lower() != "application/json":
            return send(415, {"error": "Send application/json."})
        if not self.configured:
            return send(503, {"error": "Hosted demo setup is incomplete."})
        try:
            length = int(environ.get("CONTENT_LENGTH") or "0")
            if not 0 < length <= 10000:
                return send(413, {"error": "Request size must be under 10 KB."})
            payload = json.loads(environ["wsgi.input"].read(length))
            normalize_payload(payload)
        except (ValueError, UnicodeDecodeError):
            return send(400, {"error": "Send a name and at least one memory as valid JSON."})
        if not self.inference_slot.acquire(blocking=False):
            return send(429, {"error": "The demo is writing another draft. Please try again shortly."})
        try:
            self.reserve_request()
            if path == "/api/generate":
                result = generate(payload, self.client.complete, f"{self.client.model} / DigitalOcean")
            else:
                result = ask_question(payload, self.client.complete)
            return send(200, result)
        except ProviderBusy as exc:
            with self.lock:
                self.cooldown_until = time.monotonic() + 60
            return send(429, {"error": str(exc)})
        except DemoLimit as exc:
            return send(429, {"error": str(exc)})
        except (InferenceUnavailable, RuntimeError) as exc:
            return send(502, {"error": str(exc)})
        finally:
            self.inference_slot.release()


def create_application() -> HostedApplication:
    origin = os.environ.get("PUBLIC_ORIGIN") or os.environ.get("RENDER_EXTERNAL_URL", "")
    if origin:
        parts = urlsplit(origin)
        if parts.scheme not in {"http", "https"} or not parts.netloc or parts.path not in {"", "/"} or parts.query or parts.fragment:
            raise ValueError("PUBLIC_ORIGIN must be a URL origin without a path, query, or fragment.")
    return HostedApplication(
        api_key=os.environ.get("DO_MODEL_ACCESS_KEY", "").strip(),
        model=os.environ.get("DO_MODEL", "llama-4-maverick").strip(),
        public_origin=origin,
        hourly_limit=int(os.environ.get("DEMO_REQUESTS_PER_HOUR", "60")),
    )


application = create_application()

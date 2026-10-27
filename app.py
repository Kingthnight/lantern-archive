"""Lantern Archive: a private, local remembrance writer powered by llama.cpp."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
MODEL_HOST = "127.0.0.1"
MODEL_PORT = 8766
APP_PORT = 8765
MODEL_URL = f"http://{MODEL_HOST}:{MODEL_PORT}"
GENERATION_LOCK = threading.Lock()
MODEL_API_KEY = secrets.token_urlsafe(32)
MODEL_PROCESS: subprocess.Popen[str] | None = None


def model_ready() -> bool:
    try:
        request = Request(f"{MODEL_URL}/health", headers={"Authorization": f"Bearer {MODEL_API_KEY}"})
        with urlopen(request, timeout=1) as response:
            return response.status == 200
    except (URLError, TimeoutError):
        return False


def normalize_payload(payload: object) -> tuple[str, str, list[str]]:
    if not isinstance(payload, dict):
        raise ValueError("Please send a JSON object.")
    name = str(payload.get("name", "")).strip()[:80]
    relationship = str(payload.get("relationship", "")).strip()[:80]
    memories = payload.get("memories")
    if not name:
        raise ValueError("Add the person's name first.")
    if not isinstance(memories, list):
        raise ValueError("Add at least one memory.")
    cleaned = [str(item).strip()[:600] for item in memories[:8] if str(item).strip()]
    if not cleaned:
        raise ValueError("Add at least one memory.")
    return name, relationship, cleaned


def build_messages(name: str, relationship: str, memories: list[str]) -> list[dict[str, str]]:
    numbered = "\n".join(f"[{index}] {memory}" for index, memory in enumerate(memories, 1))
    return [
        {
            "role": "system",
            "content": (
                "You are a careful remembrance editor. Return JSON with a 'lines' array. "
                "For each numbered note, write exactly ONE warm, plain sentence in "
                "a remembrance voice that "
                "restates only that note, in the same order. Preserve every concrete "
                "detail. Do not infer personality, motives, atmosphere, beliefs, "
                "emotions, routines, family relationships, or events. Do not add "
                "a title, introduction, conclusion, or citations. Each sentence must "
                "stand alone and contain no fact absent from its corresponding note."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Person: {name}\nRelationship: {relationship or 'not specified'}\n"
                f"Notes:\n{numbered}\n\nReturn exactly {len(memories)} lines in JSON."
            ),
        },
    ]


def complete(body: dict[str, object]) -> str:
    body.update({"model": "lantern", "stream": False, "chat_template_kwargs": {"enable_thinking": False}})
    request = Request(
        f"{MODEL_URL}/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {MODEL_API_KEY}"},
        method="POST",
    )
    with GENERATION_LOCK:
        with urlopen(request, timeout=240) as response:
            result = json.load(response)
    choice = result["choices"][0]["message"]
    return re.sub(r"<think>.*?</think>", "", str(choice.get("content") or ""), flags=re.DOTALL).strip()


def generate(payload: object) -> dict[str, object]:
    name, relationship, memories = normalize_payload(payload)
    raw = complete({
        "messages": build_messages(name, relationship, memories),
        "temperature": 0.35,
        "max_tokens": 300,
        "response_format": {
            "type": "json_object",
            "schema": {
                "type": "object",
                "properties": {"lines": {"type": "array", "minItems": len(memories), "maxItems": len(memories), "items": {"type": "string"}}},
                "required": ["lines"],
                "additionalProperties": False,
            },
        },
    })
    try:
        lines = json.loads(raw)["lines"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("The local model did not return a usable draft. Please try again.") from exc
    if not isinstance(lines, list) or len(lines) != len(memories) or any(not isinstance(line, str) or not line.strip() for line in lines):
        raise RuntimeError("The local model missed a memory. Please try again.")
    cited = "\n".join(f"{line.strip()} [{i}]" for i, line in enumerate(lines, 1))
    draft = f"For {name}, these are the details we carry forward.\n\n{cited}"
    return {"draft": draft, "model": "Qwen3.5-4B / llama.cpp", "source_count": len(memories)}


def ask_question(payload: object) -> dict[str, str]:
    name, relationship, memories = normalize_payload(payload)
    numbered = "\n".join(f"[{i}] {memory}" for i, memory in enumerate(memories, 1))
    raw = complete({
        "messages": [
            {"role": "system", "content": (
                "You are a gentle oral-history interviewer. Ask exactly one short, "
                "open-ended question that could help someone recall another concrete "
                "detail related to the notes. Do not assume any new fact or that the "
                "listener witnessed an event; they may have heard a story secondhand. "
                "Use neutral wording such as 'What else do you know about...?' or "
                "'Can you tell me more about...?' Do not ask about death or grief. "
                "Return JSON with only a 'question' field."
            )},
            {"role": "user", "content": f"Person: {name}\nRelationship: {relationship or 'not specified'}\nNotes:\n{numbered}"},
        ],
        "temperature": 0.5,
        "max_tokens": 100,
        "response_format": {
            "type": "json_object",
            "schema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"], "additionalProperties": False},
        },
    })
    try:
        question = json.loads(raw)["question"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("The local model did not return a question. Please try again.") from exc
    if not isinstance(question, str) or not question.strip():
        raise RuntimeError("The local model did not return a question. Please try again.")
    return {"question": question.strip()[:300]}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, content: bytes, mime: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'")
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, status: int, data: object) -> None:
        self._send(status, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if self.headers.get("Host") not in {f"127.0.0.1:{APP_PORT}", f"localhost:{APP_PORT}"}:
            self._json(403, {"error": "Local access only"})
            return
        if self.path == "/api/status":
            error = "Local model process exited. Check the terminal for details." if MODEL_PROCESS and MODEL_PROCESS.poll() is not None else None
            self._json(200, {"ready": model_ready(), "local_only": True, "error": error})
            return
        files = {"/": ("index.html", "text/html"), "/style.css": ("style.css", "text/css"), "/app.js": ("app.js", "application/javascript")}
        if self.path not in files:
            self._json(404, {"error": "Not found"})
            return
        file_name, mime = files[self.path]
        self._send(200, (STATIC / file_name).read_bytes(), f"{mime}; charset=utf-8")

    def do_POST(self) -> None:
        if self.headers.get("Host") not in {f"127.0.0.1:{APP_PORT}", f"localhost:{APP_PORT}"}:
            self._json(403, {"error": "Local access only"})
            return
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://127.0.0.1:{APP_PORT}", f"http://localhost:{APP_PORT}"}:
            self._json(403, {"error": "Local access only"})
            return
        if self.path not in {"/api/generate", "/api/question"}:
            self._json(404, {"error": "Not found"})
            return
        if not model_ready():
            self._json(503, {"error": "The local model is still starting. Try again shortly."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 10000:
                raise ValueError("Request size must be under 10 KB.")
            data = json.loads(self.rfile.read(length))
            self._json(200, generate(data) if self.path == "/api/generate" else ask_question(data))
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
        except (HTTPError, URLError, TimeoutError, KeyError, IndexError, RuntimeError) as exc:
            self._json(502, {"error": f"Local inference failed: {exc}"})

    def log_message(self, format: str, *args: object) -> None:
        sys.stdout.write("[web] " + format % args + "\n")


def main() -> None:
    global MODEL_PROCESS
    parser = argparse.ArgumentParser(description="Run Lantern Archive entirely on this computer")
    parser.add_argument("--model", type=Path, help="Path to a local GGUF model")
    parser.add_argument("--llama-server", default=shutil.which("llama-server") or shutil.which("llama-server.exe"))
    args = parser.parse_args()
    if not args.model or not args.model.is_file():
        parser.error("Pass --model with the path to a downloaded GGUF file.")
    if not args.llama_server:
        parser.error("Install llama.cpp and ensure llama-server is on PATH, or pass --llama-server.")

    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    log_path = Path(tempfile.gettempdir()) / f"lantern-llama-{os.getpid()}.log"
    log = log_path.open("w", encoding="utf-8")
    command = [
        args.llama_server, "-m", str(args.model), "--host", MODEL_HOST,
        "--port", str(MODEL_PORT), "--alias", "lantern", "-c", "4096",
        "-t", "4", "-ngl", "0", "--api-key", MODEL_API_KEY,
        "--no-webui", "--chat-template-kwargs", '{"enable_thinking":false}',
    ]
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    MODEL_PROCESS = process
    server = ThreadingHTTPServer(("127.0.0.1", APP_PORT), Handler)
    try:
        print(f"Lantern Archive: http://127.0.0.1:{APP_PORT}", flush=True)
        print("Loading local model; the page will show when it is ready.", flush=True)
        print(f"Model diagnostics: {log_path}", flush=True)
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()


if __name__ == "__main__":
    main()

"""Z Assistant web server.

Serves the browser UI in ``web/`` and a small JSON/SSE API:

    GET  /api/status   startup stages, compute route (Boost / local), session, stats
    POST /api/chat     {"text": "..."}  ->  text/event-stream of answer events
    POST /api/reset    forget the conversation
    POST /api/quality  run the Galileo prompt-quality evaluation

In normal mode the server is a thin bridge: it reads the status that ``run.sh``
and the LLM worker (``zassistant.py``) write to Redis, and passes chat jobs to
the worker through Redis. It never loads a model itself, so the UI comes up
instantly, even while Boost is still connecting.

``--demo`` swaps in a fake backend (no Redis, Ollama or GPU needed) that
simulates a Boost startup and streams canned answers, for reviewing the UI.
"""

import argparse
import json
import os
import random
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, Iterator, List

from flask import Flask, Response, jsonify, request, send_from_directory

WEB_DIR = Path(__file__).resolve().parent / "web"
STATUS_KEY = "z:status"
JOBS_KEY = "z:jobs"
STAGES = ["session", "ollama", "model", "index"]
REPLY_TIMEOUT_S = 180  # give up if the worker goes silent for this long
MAX_PROMPT_CHARS = 4000


def sse(event: Dict) -> str:
    return "data: " + json.dumps(event) + "\n\n"


# --------------------------------------------------------------------------
# Real backend: Redis bridge to run.sh + zassistant.py
# --------------------------------------------------------------------------
class RedisBackend:
    demo = False

    def __init__(self, host: str, port: int) -> None:
        import redis

        self.r = redis.Redis(host=host, port=port, db=0, decode_responses=True)

    def status(self) -> Dict:
        try:
            h = self.r.hgetall(STATUS_KEY)
        except Exception:
            return {"state": "offline", "now": time.time()}

        stages = []
        for sid in STAGES:
            stages.append({
                "id": sid,
                "state": h.get(f"stage.{sid}", "pending"),
                "ms": int(h[f"stage.{sid}.ms"]) if h.get(f"stage.{sid}.ms") else None,
                "started": int(h[f"stage.{sid}.t0"]) / 1000 if h.get(f"stage.{sid}.t0") else None,
                "detail": h.get(f"stage.{sid}.detail", ""),
            })

        answers = int(h.get("answers", 0) or 0)
        total_ms = int(h.get("total_ms", 0) or 0)
        error = h.get("error", "")
        ready = h.get("ready") == "1"
        return {
            "state": "error" if error else ("ready" if ready else "booting"),
            "error": error,
            "mode": h.get("mode", "local"),
            "model": h.get("model", ""),
            "gpu_id": h.get("gpu_id", ""),
            "booted_at": float(h.get("booted_at", 0) or 0),
            "session": {
                "started": float(h.get("session_started", 0) or 0),
                "timeout": int(h.get("session_timeout", 0) or 0),
            },
            "docs": json.loads(h.get("docs", "[]") or "[]"),
            "stages": stages,
            "stats": {
                "answers": answers,
                "avg_ms": round(total_ms / answers) if answers else None,
                "last_ms": int(h["last_ms"]) if h.get("last_ms") else None,
            },
            "demo": False,
            "now": time.time(),
        }

    def _job(self, job: Dict) -> Iterator[Dict]:
        """Queue a job for the worker and yield its events until done/error."""
        job_id = uuid.uuid4().hex
        pubsub = self.r.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(f"z:stream:{job_id}")  # subscribe first: no lost events
        try:
            self.r.rpush(JOBS_KEY, json.dumps({"id": job_id, **job}))
            last = time.time()
            while True:
                msg = pubsub.get_message(timeout=1.0)
                if msg is None:
                    if time.time() - last > REPLY_TIMEOUT_S:
                        yield {"type": "error", "message": "The assistant stopped responding."}
                        return
                    continue
                last = time.time()
                event = json.loads(msg["data"])
                yield event
                if event.get("type") in ("done", "error"):
                    return
        finally:
            pubsub.close()

    def chat(self, text: str) -> Iterator[Dict]:
        return self._job({"type": "chat", "text": text})

    def command(self, kind: str) -> Dict:
        text, last = "", {}
        for event in self._job({"type": kind}):
            if event.get("type") == "token":
                text += event.get("text", "")
            last = event
        if last.get("type") == "error":
            return {"ok": False, "message": last.get("message", "Failed")}
        return {"ok": True, "message": text}


# --------------------------------------------------------------------------
# Demo backend: simulated Boost startup + canned answers
# --------------------------------------------------------------------------
DEMO_DOCS = [
    {"name": "4AA4-7665ENW", "pages": 17},
    {"name": "4AA7-6404ENW", "pages": 11},
    {"name": "4AA7-5783ENW", "pages": 5},
]

DEMO_ANSWERS = [
    (r"boost|share|borrow|remote",
     "**Z by HP Boost** lets you borrow an idle GPU from a Z workstation on your network. "
     "Your laptop keeps running the app, while the heavy GPU work runs on the Z host and the "
     "results come straight back.\n\nThat's exactly how I'm running right now: the language "
     "model lives on a remote Z GPU, and this window is just the interface.",
     [("4AA7-5783ENW", 2), ("4AA7-6404ENW", 4)]),
    (r"vfx|video|render|8k|edit",
     "For video and VFX you want plenty of GPU memory and fast storage. A Z tower such as the "
     "**Z8 Fury** gives you room for multiple NVIDIA RTX professional GPUs, so you can render on "
     "one while you keep editing on another.\n\n- **At the desk:** a Z8 or Z6 tower\n"
     "- **On the move:** a ZBook, plus Boost when a render needs more GPU",
     [("4AA7-6404ENW", 3), ("4AA4-7665ENW", 9)]),
    (r"cad|engineer|bim|simulat|design",
     "For CAD and simulation, single-thread CPU speed matters as much as the GPU. A **Z4** "
     "tower is a strong all-rounder for most CAD work, and you can step up to a **Z6** or "
     "**Z8** if you run large simulations.\n\nIf you mostly work on site, a ZBook with an RTX "
     "professional GPU keeps certified CAD drivers with you.",
     [("4AA4-7665ENW", 5), ("4AA4-7665ENW", 12)]),
    (r"ai|data|llm|train|fine|model|gpu memory",
     "For AI and data science, GPU memory is the number to watch. Running and fine-tuning "
     "large models is much easier with high-memory RTX professional GPUs, and a **Z8 Fury** "
     "can hold several of them.\n\nIf your team shares a few big workstations, **Boost** lets "
     "everyone borrow those GPUs from their own laptops instead of buying one each.",
     [("4AA7-6404ENW", 2), ("4AA7-5783ENW", 1)]),
]

DEMO_FALLBACK = (
    "Good question! I'm best at helping you choose between Z by HP workstations: towers like "
    "the Z4, Z6 and Z8, mobile ZBooks, and GPU sharing with Boost.\n\nTell me what software "
    "you use and what slows you down today, and I'll point you to a good fit.",
    [("4AA4-7665ENW", 1)],
)


class DemoBackend:
    demo = True

    def __init__(self, mode: str, boot_seconds: float) -> None:
        self.mode = mode
        self.boot = max(boot_seconds, 0.0)
        self.t0 = time.time()
        self.lock = threading.Lock()
        self.answers = 0
        self.total_ms = 0
        self.last_ms = None

    def _timeline(self) -> List[tuple]:
        # (stage, share of boot time). Local mode has no Boost session.
        plan = [("session", 0.2), ("ollama", 0.15), ("model", 0.45), ("index", 0.2)]
        if self.mode != "boost":
            plan = [("session", 0.0), ("ollama", 0.2), ("model", 0.5), ("index", 0.3)]
        return plan

    def status(self) -> Dict:
        elapsed = time.time() - self.t0
        cursor, stages = 0.0, []
        for sid, share in self._timeline():
            dur = self.boot * share
            if sid == "session" and self.mode != "boost":
                stages.append({"id": sid, "state": "skipped", "ms": None, "started": None, "detail": ""})
                continue
            if elapsed >= cursor + dur:
                state, ms = "done", int(dur * 1000)
            elif elapsed >= cursor:
                state, ms = "running", None
            else:
                state, ms = "pending", None
            detail = {
                "session": "GPU e47e046e…57d0 · 60 min",
                "ollama": "via hpcf run" if self.mode == "boost" else "local GPU",
                "model": "llama3.1:8b",
                "index": "3 PDFs · 33 pages",
            }[sid]
            started = self.t0 + cursor if state != "pending" else None
            stages.append({"id": sid, "state": state, "ms": ms, "started": started, "detail": detail})
            cursor += dur
        ready = elapsed >= cursor
        with self.lock:
            stats = {
                "answers": self.answers,
                "avg_ms": round(self.total_ms / self.answers) if self.answers else None,
                "last_ms": self.last_ms,
            }
        return {
            "state": "ready" if ready else "booting",
            "error": "",
            "mode": self.mode,
            "model": "llama3.1:8b",
            "gpu_id": "e47e046e-6c41-fea0-c46a-1da1f32e57d0" if self.mode == "boost" else "",
            "booted_at": self.t0,
            # Pretend the session began two minutes ago so the countdown looks lived-in.
            "session": {"started": self.t0 - 108, "timeout": 3600} if self.mode == "boost" else {"started": 0, "timeout": 0},
            "docs": DEMO_DOCS,
            "stages": stages,
            "stats": stats,
            "demo": True,
            "now": time.time(),
        }

    def chat(self, text: str) -> Iterator[Dict]:
        start = time.time()
        answer, refs = DEMO_FALLBACK
        for pattern, body, srcs in DEMO_ANSWERS:
            if re.search(pattern, text, re.I):
                answer, refs = body, srcs
                break
        time.sleep(0.9)  # "retrieving + first token"
        yield {"type": "sources", "items": [{"doc": d, "page": p} for d, p in refs]}
        for token in re.findall(r"\S+\s*|\n+", answer):
            time.sleep(random.uniform(0.018, 0.05))
            yield {"type": "token", "text": token}
        ms = int((time.time() - start) * 1000)
        with self.lock:
            self.answers += 1
            self.total_ms += ms
            self.last_ms = ms
        yield {"type": "done", "ms": ms, "compute": self.mode}

    def command(self, kind: str) -> Dict:
        if kind == "quality":
            time.sleep(1.2)
            return {"ok": True, "message": "Prompt quality evaluation has been completed (demo)."}
        return {"ok": True, "message": "Conversation cleared."}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def create_app(backend) -> Flask:
    app = Flask(__name__, static_folder=None)

    @app.get("/")
    def index():
        return send_from_directory(WEB_DIR, "index.html")

    @app.get("/<path:path>")
    def static_files(path: str):
        return send_from_directory(WEB_DIR, path)

    @app.get("/api/status")
    def status():
        return jsonify(backend.status())

    @app.post("/api/chat")
    def chat():
        body = request.get_json(silent=True) or {}
        text = str(body.get("text", "")).strip()[:MAX_PROMPT_CHARS]
        if not text:
            return jsonify({"error": "Empty message"}), 400

        def stream():
            try:
                for event in backend.chat(text):
                    yield sse(event)
            except Exception as exc:  # backend unreachable, etc.
                yield sse({"type": "error", "message": f"Backend error: {exc}"})

        return Response(stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/reset")
    def reset():
        return jsonify(backend.command("reset"))

    @app.post("/api/quality")
    def quality():
        return jsonify(backend.command("quality"))

    if backend.demo:
        @app.post("/api/demo/reboot")
        def demo_reboot():
            """Replay the simulated startup (open the UI with ?boot). Optional ?mode=boost|local."""
            mode = request.args.get("mode")
            if mode in ("boost", "local"):
                backend.mode = mode
            backend.t0 = time.time()
            return jsonify({"ok": True, "mode": backend.mode})

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description="Z Assistant web UI")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    ap.add_argument("--demo", action="store_true", help="fake backend for UI review (no AI needed)")
    ap.add_argument("--demo-mode", choices=["boost", "local"], default="boost")
    ap.add_argument("--boot-seconds", type=float, default=10.0, help="length of the simulated startup in demo mode")
    args = ap.parse_args()

    if args.demo:
        backend = DemoBackend(args.demo_mode, args.boot_seconds)
    else:
        backend = RedisBackend(os.environ.get("REDIS_HOST", "localhost"), int(os.environ.get("REDIS_PORT", 6379)))

    print(f"Z Assistant UI on http://{args.host}:{args.port}{'  (demo)' if args.demo else ''}", flush=True)
    create_app(backend).run(host=args.host, port=args.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()

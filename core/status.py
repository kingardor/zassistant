"""Startup/status reporting shared with run.sh and the web server.

Everything lives in one Redis hash (``z:status``) so the UI can render the
Boost startup screen and the session popover without talking to the model.

Stage fields: ``stage.<id>`` = pending | running | done | error | skipped,
plus ``stage.<id>.t0`` (epoch ms), ``stage.<id>.ms`` and ``stage.<id>.detail``.
"""

import json
import time

STATUS_KEY = "z:status"


def _now_ms() -> int:
    return int(time.time() * 1000)


class Status:
    def __init__(self, cache) -> None:
        self.cache = cache
        self.current = None  # last stage started, so a crash can be pinned on it

    def set(self, **fields) -> None:
        self.cache.hset(STATUS_KEY, mapping={k: str(v) for k, v in fields.items()})

    def begin(self, stage: str, detail: str = "") -> None:
        self.current = stage
        fields = {f"stage.{stage}": "running", f"stage.{stage}.t0": _now_ms()}
        if detail:
            fields[f"stage.{stage}.detail"] = detail
        self.set(**fields)

    def done(self, stage: str, detail: str = "") -> None:
        t0 = self.cache.hget(STATUS_KEY, f"stage.{stage}.t0")
        fields = {f"stage.{stage}": "done"}
        if t0:
            fields[f"stage.{stage}.ms"] = _now_ms() - int(t0)
        if detail:
            fields[f"stage.{stage}.detail"] = detail
        self.set(**fields)

    def fail(self, stage: str, message: str) -> None:
        self.set(**{f"stage.{stage}": "error", "error": message})

    def ready(self, docs) -> None:
        self.set(ready=1, docs=json.dumps(docs))

    def record_answer(self, ms: int) -> None:
        pipe = self.cache.pipeline()
        pipe.hincrby(STATUS_KEY, "answers", 1)
        pipe.hincrby(STATUS_KEY, "total_ms", ms)
        pipe.hset(STATUS_KEY, "last_ms", ms)
        pipe.execute()

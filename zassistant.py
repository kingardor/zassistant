"""Z Assistant LLM worker.

Loads the model and the Z by HP document index, then answers jobs that the web
server (server.py) queues in Redis. Progress is written to the ``z:status``
hash so the browser can show the Boost startup screen.

Run by run.sh, under ``hpcf run`` when Boost is enabled.
"""
import sys
sys.path.insert(1, 'core')
import os
import json
import time
import redis
import traceback

from status import Status

JOBS_KEY = 'z:jobs'


class ZAssistant:
    def __init__(self) -> None:
        self.cache = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
        self.status = Status(self.cache)
        self.mode = 'boost' if os.environ.get('BOOST') == '1' else 'local'
        self.llm = None

    def boot(self) -> None:
        if os.environ.get('LANGMODEL') != 'ollama-rag':
            self.status.fail('model', 'Invalid LANGMODEL (expected ollama-rag)')
            raise ValueError("Invalid language model")

        # Imported here so a crash in heavy imports is still reported to the UI
        from llama_rag import OllamaRag, warm_model, MODEL
        from rag import RAG

        print('Loading model', flush=True)
        self.status.begin('model', MODEL)
        warm_model(MODEL)
        self.status.done('model', MODEL)

        print('Indexing documents', flush=True)
        self.status.begin('index', 'Z by HP documents')
        rag = RAG()
        pages = sum(d['pages'] for d in rag.docs)
        self.status.done('index', f"{len(rag.docs)} PDFs · {pages} pages")

        self.llm = OllamaRag(rag)
        self.status.ready(rag.docs)
        print('Z Assistant ready', flush=True)

    def publish(self, job_id: str, event: dict) -> None:
        self.cache.publish(f'z:stream:{job_id}', json.dumps(event))

    def handle(self, job: dict) -> None:
        job_id, kind = job['id'], job.get('type', 'chat')
        text = job.get('text', '').strip()

        if kind == 'reset' or text == '/new':
            self.llm.reset()
            self.publish(job_id, {'type': 'done'})
            return
        if kind == 'quality' or text in ('/quality', '/promptquality'):
            self.publish(job_id, {'type': 'token', 'text': self.llm.quality()})
            self.publish(job_id, {'type': 'done'})
            return

        print(f"USER: {text}", flush=True)
        start = time.time()
        for event in self.llm.stream(text):
            self.publish(job_id, event)
        ms = int((time.time() - start) * 1000)
        self.status.record_answer(ms)
        self.publish(job_id, {'type': 'done', 'ms': ms, 'compute': self.mode})

    def serve(self) -> None:
        while True:
            _, raw = self.cache.blpop(JOBS_KEY)
            job = json.loads(raw)
            try:
                self.handle(job)
            except Exception as exc:
                traceback.print_exc()
                self.publish(job['id'], {'type': 'error', 'message': f'Something went wrong: {exc}'})


if __name__ == "__main__":
    z = ZAssistant()
    try:
        z.boot()
    except Exception as exc:
        traceback.print_exc()
        if not z.cache.hget('z:status', 'error'):
            z.status.fail(z.status.current or 'model', f'Startup failed: {exc}')
        sys.exit(1)
    z.serve()

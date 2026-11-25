import os
import json
import urllib.request
from datetime import datetime
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage, AIMessage
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain.chains import create_history_aware_retriever, create_retrieval_chain

MODEL = os.environ.get('MODEL', 'llama3.1:8b')
OLLAMA_URL = 'http://localhost:11434'


def galileo_login():
    """Log in to Galileo for prompt-quality scoring. Returns the module, or None if not configured."""
    try:
        import promptquality as pq
        from keys import GALILEO_API_KEY
    except ImportError:
        print('Galileo API key not found: prompt-quality scoring is disabled', flush=True)
        return None
    os.environ['GALILEO_API_KEY'] = GALILEO_API_KEY
    pq.login("https://console.hp.galileocloud.io/")
    return pq


def warm_model(model: str = MODEL) -> None:
    """Ask Ollama to load the model into GPU memory now, instead of on the first question."""
    body = json.dumps({'model': model, 'keep_alive': os.environ.get('OLLAMA_KEEP_ALIVE', '1h')}).encode()
    req = urllib.request.Request(f'{OLLAMA_URL}/api/generate', data=body,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=3600) as resp:
        resp.read()


class OllamaRag:
    def __init__(self, rag):
        self.llm = ChatOllama(
            model=MODEL,
            temperature=0.01,

        )
        self.rag = rag

        self.chat_history = list()
        contextualize_q_system_prompt = self.prompt_template = """
        <|begin_of_text|><|start_header_id|>system<|end_header_id|>

        Cutting Knowledge Date: December 2023
        Today Date: 23 July 2024

        Given a chat history and the latest user question \
        which might reference context in the chat history, formulate a standalone question \
        which can be understood without the chat history. Do NOT answer the question, \
        just reformulate it if needed and otherwise return it as is.<|eot_id|>
        <|start_header_id|>user<|end_header_id|>
        Chat History: {chat_history}
        ---
        Context: {context}
        ---
        Question: {input}<|eot_id|>
        <|start_header_id|>assistant<|end_header_id|>
        """

        contextualize_q_prompt = ChatPromptTemplate.from_template(
            contextualize_q_system_prompt
        )

        self.history_aware_retriever = create_history_aware_retriever(
            self.llm,
            self.rag.retriever,
            contextualize_q_prompt
        )

        self.prompt_template = """
        <|begin_of_text|><|start_header_id|>system<|end_header_id|>

        Cutting Knowledge Date: December 2023
        Today Date: 23 July 2024

        You are an AI assistant named Z.
        You are helpful, very polite, friendly and funny.
        You will assist the user in their queries using your knowledge about Z by HP products.
        Utilise the context provided to answer the user's query.
        Do not mention about the context in your answer.
        If you don't know the answer, just say that you don't know.
        Answer under 3 sentences cause you are a short interaction assistant.<|eot_id|>
        <|start_header_id|>user<|end_header_id|>
        Chat History: {chat_history}
        ---
        Context: {context}
        ---
        Question: {input}<|eot_id|>
        <|start_header_id|>assistant<|end_header_id|>
        """

        prompt = ChatPromptTemplate.from_template(
            self.prompt_template
        )

        question_answer_chain = create_stuff_documents_chain(
            self.llm, prompt
        )

        self.chain = create_retrieval_chain(
            self.history_aware_retriever, question_answer_chain
        )

        self.prompt_handler = None
        self.pq = galileo_login()
        if self.pq is not None:
            self.galileo_project_name = "Z Assistant"
            self.galileo_project = self.pq.get_project_from_name(project_name=self.galileo_project_name)
            self.run_name = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
            self.prompt_handler = self._new_prompt_handler()

    def _new_prompt_handler(self):
        return self.pq.GalileoPromptCallback(
            project_name=self.galileo_project_name,
            scorers=[
                self.pq.Scorers.context_adherence_luna,
                self.pq.Scorers.correctness,
                self.pq.Scorers.toxicity,
                self.pq.Scorers.sexist]
        )

    @staticmethod
    def _sources(docs, limit: int = 4):
        """Unique (document, page) pairs the answer was grounded on, in retrieval order."""
        seen, items = set(), []
        for doc in docs:
            name = os.path.splitext(os.path.basename(doc.metadata.get('source', '')))[0]
            page = doc.metadata.get('page')
            page = page + 1 if isinstance(page, int) else None  # PyPDF pages are 0-based
            if (name, page) in seen:
                continue
            seen.add((name, page))
            items.append({'doc': name, 'page': page})
        return items[:limit]

    def stream(self, query: str):
        """Yield UI events while answering: one 'sources' event, then 'token' events."""
        config = dict(callbacks=[self.prompt_handler]) if self.prompt_handler else {}
        answer = ''
        for chunk in self.chain.stream(
            {'chat_history': self.chat_history, 'input': query},
            config=config,
        ):
            if 'context' in chunk:
                yield {'type': 'sources', 'items': self._sources(chunk['context'])}
            token = chunk.get('answer')
            if token:
                answer += token
                yield {'type': 'token', 'text': token}

        self.chat_history.extend([
            HumanMessage(content=query), AIMessage(content=answer)
        ])

    def reset(self) -> None:
        self.chat_history = list()

    def quality(self) -> str:
        if self.prompt_handler is None:
            return "Galileo isn't configured, so prompt quality can't be evaluated. Add core/keys.py with GALILEO_API_KEY."
        self.prompt_handler.finish()
        # finish() uploads this run; start collecting a fresh one for the next check
        self.prompt_handler = self._new_prompt_handler()
        return "Prompt quality evaluation has been completed"

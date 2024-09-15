import os
from datetime import datetime
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain.chains import create_history_aware_retriever, create_retrieval_chain
from rag import RAG
import promptquality as pq

try:
    from keys import GALILEO_API_KEY
    os.environ['GALILEO_API_KEY'] = GALILEO_API_KEY
    galileo_url = "https://console.hp.galileocloud.io/"
    pq.login(galileo_url)
except:
    print("Galileo API key not found")
    import sys
    sys.exit(1)

class OllamaRag:
    def __init__(self):
        self.llm = ChatOllama(
            model="llama3.1:8b",
            temperature=0.01,

        )    
        self.rag = RAG()

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

        self.galileo_project_name = "Z Assistant"
        self.galileo_project = pq.get_project_from_name(project_name=self.galileo_project_name)

        self.run_name = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")

        self.prompt_handler = pq.GalileoPromptCallback(
            project_name=self.galileo_project_name,
            scorers=[
                pq.Scorers.context_adherence_luna, 
                pq.Scorers.correctness, 
                pq.Scorers.toxicity, 
                pq.Scorers.sexist]
        )
    
    def exec(self, query: str):
        if query == '/promptquality':
            self.prompt_handler.finish()
            return "Prompt quality evaluation has been completed"
        
        response = self.chain.invoke(
            {   
                'chat_history': self.chat_history,
                'input': query,
                'context': self.rag.retriever | self.rag.format_docs
            },
            config=dict(callbacks=[self.prompt_handler])
        )
        self.chat_history.extend([
            HumanMessage(content=query), response['answer']
        ])

        return response['answer']
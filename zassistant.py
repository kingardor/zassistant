import sys
sys.path.insert(1, 'core')
import os
import time
import redis
import traceback

from threading import Thread

# LLMS
from llama_rag import OllamaRag

# UI
from ui import UserInterface

class ZAssistant:
    def start_threads(self) -> None:
        # Start the LLM thread
        print('Starting LLM thread', flush=True)
        llm_thread = Thread(target=self.llm_thread)
        llm_thread.daemon = True
        llm_thread.start()
        print('LLM thread started', flush=True)

    def llm_thread(self) -> None:
        
        def _prompt_handling(llm_input: str) -> None:
            response = self.llm.exec(llm_input)
            print("Z: {}".format(response), flush=True)
            self.cache.set('response', response)
            self.ui.add_text('Z', response)
                
        while True:
            speaker = 'USER'
            try:
                user_input = self.cache.get('prompt').decode('utf-8')
                if '[TEXT]' in user_input:   
                    user_input = user_input.replace('[TEXT]', '')
                self.ui.add_text(speaker, user_input)
                print("{}: {}".format(speaker, user_input), flush=True)
                self.cache.delete('prompt')
            except:
                user_input = None

            if user_input is not None:
                try:                    
                    if user_input == 'Bye.' or user_input == 'Thank you.' or user_input == 'Thank you for watching.':
                        continue
                    _prompt_handling(user_input)
                except:
                    print('Error processing user input', flush=True)
                    traceback.print_exc()
            
            time.sleep(0.5)

    def __init__(self) -> None:

        self.cache = None
        self.llm = None
        self.ui = None

        self.langmodel = os.environ.get('LANGMODEL')

        # Initialize the cache
        print('Initializing cache', flush=True)
        self.cache = redis.Redis(
            host='localhost', 
            port=6379, 
            db=0
        )

        # Initialize the language model
        if self.langmodel == "ollama-rag":
            print('Loading Ollama RAG', flush=True)
            self.llm = OllamaRag()
            print('Ollama RAG loaded', flush=True)
        else:
            raise ValueError("Invalid language model")

        # Initialise UI
        print('Initializing UI', flush=True)
        self.ui = UserInterface()
        print('UI initialized', flush=True)

        # Start threads
        self.start_threads()

if __name__ == "__main__":
    zassistant = ZAssistant()
    zassistant.ui.start_ui()
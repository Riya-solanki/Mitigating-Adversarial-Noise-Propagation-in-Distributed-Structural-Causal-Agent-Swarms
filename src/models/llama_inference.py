import logging
import os
import requests

logger = logging.getLogger(__name__)

class LlamaInferenceEngine:
    """
    Connects to the local Dockerized Ollama setup to run Llama-3-8b.
    """
    def __init__(self, model_id="llama3"):
        self.model_id = model_id
        # When running in Docker, OLLAMA_HOST points to the ollama container.
        # Fallback to localhost if running this script directly without docker.
        self.api_url = f"{os.getenv('OLLAMA_HOST', 'http://localhost:11434')}/api/generate"
        logger.info(f"Initialized LLM Inference Engine with {model_id} at {self.api_url}")

    def generate_response(self, prompt, max_tokens=100):
        """
        Calls the Ollama API to generate text.
        Make sure you run `docker exec -it <ollama_container_id> ollama run llama3` first.
        """
        logger.info(f"Generating response for prompt: {prompt[:50]}...")
        
        payload = {
            "model": self.model_id,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": max_tokens
            }
        }
        
        try:
            response = requests.post(self.api_url, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            return data.get("response", "").strip()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error communicating with Ollama: {e}")
            return f"Error: Failed to reach local LLM at {self.api_url}."

if __name__ == "__main__":
    # Simple test script for the LLM
    print("Testing Llama Inference Engine...")
    engine = LlamaInferenceEngine()
    print("Sending prompt to Ollama API...")
    result = engine.generate_response("You are an AI Node in a smart home. Should you turn on the AC if the window is open? Answer briefly.")
    print("\nLlama Response:")
    print(result)

# prueba_health.py
import requests
import os
from dotenv import load_dotenv
load_dotenv()

r = requests.get(f"{os.getenv('API_BASE_URL')}/health", timeout=10)
print("Status:", r.status_code)
print("Body:", r.json())
# Esperar: {"status": "ok"}


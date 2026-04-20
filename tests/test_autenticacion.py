# prueba_info.py
import requests
import os
from dotenv import load_dotenv
load_dotenv()

headers = {"Authorization": f"Bearer {os.getenv('BEARER_TOKEN')}"}
r = requests.get(f"{os.getenv('API_BASE_URL')}/info", headers=headers, timeout=10)
print("Status:", r.status_code)
print("Body:", r.json())
# Esperar: batch_size, window_start, window_end, total_records, total_pages

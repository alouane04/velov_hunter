import os
import requests
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.environ.get("JCDECAUX_API_KEY")
CONTRACT = "lyon"
STATION = 5030

url = f"https://api.jcdecaux.com/vls/v3/stations/{STATION}?contract={CONTRACT}&apiKey={API_KEY}"
print("Fetching:", url)
res = requests.get(url)
print(res.status_code)
print(res.json())

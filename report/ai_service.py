import requests

MODEL_SERVER_URL = "http://localhost:8080/generate_report"

def generate_report(name: str, date: str) -> str:
    res = requests.post(
        MODEL_SERVER_URL,
        json={"name": name, "date": date},
        timeout=600
    )
    return res.json()["report"]

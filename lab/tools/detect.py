"""Symptom-only incident emitter; deliberately cannot read scenario state."""
import argparse
import datetime
import json
import time
import uuid
import httpx

def check(url):
    problems = []
    with httpx.Client(base_url=url, timeout=8, cookies={"lab_session": str(uuid.uuid4())}) as client:
        for path, service in [("/api/products", "catalog"), ("/api/cart", "cart")]:
            try:
                response = client.get(path)
                if response.status_code >= 500:
                    problems.append({"service": service, "path": path, "http_status": response.status_code})
            except httpx.RequestError:
                problems.append({"service": "web", "path": path, "symptom": "request_failed"})
    return problems

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8080")
    p.add_argument("--output", required=True)
    p.add_argument("--interval", type=int, default=10)
    args = p.parse_args()
    previous = None
    consecutive = 0
    while True:
        symptoms = check(args.url)
        consecutive = consecutive + 1 if symptoms else 0
        if consecutive >= 3 and symptoms != previous:
            event = {"schema_version": 1, "incident_id": str(uuid.uuid4()), "namespace": "lab-app",
                     "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "symptoms": symptoms}
            with open(args.output, "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
            print(json.dumps(event), flush=True)
            previous = symptoms
        if not symptoms:
            previous = None
        time.sleep(args.interval)

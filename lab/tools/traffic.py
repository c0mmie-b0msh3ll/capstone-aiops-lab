"""Real HTTP journeys. Nonzero exit on failure; no synthetic success counters."""
import argparse
import json
import time
import uuid
import httpx


def journey(url):
    with httpx.Client(base_url=url, timeout=12, cookies={"lab_session": str(uuid.uuid4())}) as c:
        def call(method, path, **kw):
            r = c.request(method, path, **kw)
            r.raise_for_status()
            return r.json()
        products = call("GET", "/api/products")["products"]
        assert len(products) >= 2, "Need at least two seed products"
        p, q = products[:2]
        assert call("GET", "/api/products/" + p["id"])["id"] == p["id"]
        image = c.get("/images/" + p["picture"])
        image.raise_for_status()
        assert image.headers.get("content-type", "").startswith("image/")
        call("POST", "/api/cart", json={"product_id": p["id"], "quantity": 2})
        call("POST", "/api/cart", json={"product_id": q["id"], "quantity": 1})
        items = call("GET", "/api/cart")["items"]
        assert {i["product_id"]: i["quantity"] for i in items} == {p["id"]: 2, q["id"]: 1}
        call("DELETE", "/api/cart/" + p["id"])
        assert call("GET", "/api/cart")["items"] == [{"product_id": q["id"], "quantity": 1}]
        call("DELETE", "/api/cart")
        assert call("GET", "/api/cart")["items"] == []


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=float, default=3)
    args = parser.parse_args()
    while True:
        start = time.monotonic()
        try:
            journey(args.url)
            print(json.dumps({"status": "passed", "duration": time.monotonic()-start}), flush=True)
        except Exception as e:
            print(json.dumps({"status": "failed", "error": str(e)}), flush=True)
            if not args.loop:
                raise
        if not args.loop:
            break
        time.sleep(args.interval)

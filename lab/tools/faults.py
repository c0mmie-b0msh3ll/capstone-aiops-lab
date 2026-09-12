"""Operator-only injector. Do not install this module/state in the agent runtime."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

CASES = {
    "F01": ("service", "cart", "Cart Service selector mismatch"),
    "F02": ("deployment", "cart", "Wrong Valkey endpoint"),
    "F03": ("deployment", "catalog", "Wrong database endpoint"),
    "F04": ("deployment", "cart", "Nonexistent image tag"),
    "F05": ("deployment", "web", "Crashing entrypoint (bad-release fixture)"),
    "F06": ("deployment", "web", "Wrong readiness path"),
    "F07": ("deployment", "cart", "Memory limit below working set"),
    "F08": ("deployment", "cart", "Zero desired replicas"),
}

def k(context, *args):
    r = subprocess.run(["kubectl", "--context", context, "-n", "lab-app", *args], check=True, capture_output=True, text=True)
    return json.loads(r.stdout) if r.stdout.strip() else {}

def patch(context, kind, name, changes):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(changes, f); path = Path(f.name)
    try:
        return k(context, "patch", kind, name, "--type=json", "--patch-file", str(path), "-o", "json")
    finally:
        path.unlink()

def changes(case, obj):
    base = "/spec/template/spec/containers/0"
    op = lambda path, value: {"op": "replace", "path": path, "value": value}
    if case == "F01": return [op("/spec/selector", {"app": "cart-missing"})]
    if case == "F08": return [op("/spec/replicas", 0)]
    container = obj["spec"]["template"]["spec"]["containers"][0]
    if case in ("F02", "F03"):
        name, value = ("VALKEY_ADDR", "missing-valkey:6379") if case == "F02" else ("DB_CONNECTION_STRING", "postgres://lab:$(DB_PASSWORD)@missing-db:5432/lab?sslmode=disable")
        i = next(i for i,e in enumerate(container["env"]) if e["name"] == name)
        return [op(base+f"/env/{i}/value", value)]
    if case == "F04": return [op(base+"/image", container["image"].rsplit(":",1)[0]+":missing-"+uuid.uuid4().hex[:8])]
    if case == "F05": return [{"op":"add","path":base+"/command","value":["python","-c","raise SystemExit('bad-release fixture')"]}]
    if case == "F06": return [op(base+"/readinessProbe/httpGet/path", "/invalid-ready")]
    if case == "F07": return [op(base+"/resources/limits/memory", "64Mi")]
    raise ValueError(case)

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["list", "inject", "reset"])
    p.add_argument("--case", choices=list(CASES))
    p.add_argument("--context", required=True)
    p.add_argument("--state-dir", default=".lab-state/operator")
    args = p.parse_args()
    state = Path(args.state_dir); state.mkdir(parents=True, exist_ok=True)
    active = state/"active.json"
    if args.command == "list":
        print(json.dumps(CASES, indent=2))
    elif args.command == "inject":
        if active.exists(): raise SystemExit("Reset the active case first")
        if not args.case: raise SystemExit("--case required")
        kind, name, desc = CASES[args.case]
        current = k(args.context, "get", kind, name, "-o", "json")
        record = {"case": args.case, "description": desc, "context": args.context, "kind": kind,
                  "name": name, "uid": current["metadata"]["uid"], "before_spec": current["spec"], "started_at": time.time()}
        active.write_text(json.dumps(record, indent=2), encoding="utf-8")
        patch(args.context, kind, name, [{"op":"test","path":"/metadata/resourceVersion","value":current["metadata"]["resourceVersion"]}]+changes(args.case,current))
        print("Injected; verify business symptoms and Kubernetes evidence before scoring. State:", active)
    else:
        record = json.loads(active.read_text(encoding="utf-8"))
        if record["context"] != args.context: raise SystemExit("Context mismatch")
        patch(args.context, record["kind"], record["name"], [{"op":"test","path":"/metadata/uid","value":record["uid"]},
              {"op":"replace","path":"/spec","value":record["before_spec"]}])
        record["reset_at"] = time.time()
        (state/f'{record["case"]}-{int(record["started_at"])}.json').write_text(json.dumps(record,indent=2),encoding="utf-8")
        active.unlink()
        print("Baseline spec restored; run traffic.py to verify recovery.")

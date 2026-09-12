"""Reference runbook executor. Signer/key must be outside the agent's trust boundary.

No arbitrary commands or patches. Approval binds all parameters, cluster/context,
namespace, UID/resourceVersion, operator, and expiration. SQLite consumes each ID
before a write, so uncertain outcomes require investigation and a fresh proposal.
"""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import uuid

NAMESPACE = "lab-app"
TARGETS = {"cart", "catalog", "web"}

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

def kubectl(context, *args):
    result = subprocess.run(["kubectl", "--context", context, "-n", NAMESPACE, *args],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout) if result.stdout.strip() else {}

def patch_for(runbook, target, params, current):
    if target not in TARGETS:
        raise ValueError("Target not allowed")
    base = "/spec/template/spec/containers/0"
    if runbook == "restore-selector" and target == "cart" and params == {"app": "cart"}:
        return [{"op": "replace", "path": "/spec/selector", "value": {"app": "cart"}}]
    if runbook == "set-replicas" and set(params) == {"replicas"} and type(params["replicas"]) is int and 1 <= params["replicas"] <= 2:
        return [{"op": "replace", "path": "/spec/replicas", "value": params["replicas"]}]
    if runbook == "restore-image" and set(params) == {"image"}:
        # Restrict to the same ECR repository; operator approves the exact immutable tag/digest.
        old = current["spec"]["template"]["spec"]["containers"][0]["image"]
        image = params["image"]
        repo = old.split("@")[0].rsplit(":", 1)[0]
        if isinstance(image, str) and image.startswith(repo + ":") and image != old and len(image) < 512:
            return [{"op": "replace", "path": base + "/image", "value": image}]
    if runbook == "restore-memory" and params == {"memory": "384Mi"} and target == "cart":
        return [{"op": "replace", "path": base + "/resources/limits/memory", "value": "384Mi"}]
    if runbook == "restore-probe" and target == "web" and params == {"path": "/healthz"}:
        return [{"op": "replace", "path": base + "/readinessProbe/httpGet/path", "value": "/healthz"}]
    if runbook == "restore-command" and target == "web" and params == {}:
        return [{"op": "remove", "path": base + "/command"}]
    if runbook == "restore-endpoint":
        expected = {"cart": ("VALKEY_ADDR", "valkey:6379"), "catalog": ("DB_CONNECTION_STRING", "postgres://lab:$(DB_PASSWORD)@postgresql:5432/lab?sslmode=disable")}
        if target in expected and params == {"name": expected[target][0], "value": expected[target][1]}:
            variables = current["spec"]["template"]["spec"]["containers"][0]["env"]
            index = next(i for i, item in enumerate(variables) if item["name"] == params["name"])
            return [{"op": "replace", "path": base + f"/env/{index}/value", "value": params["value"]}]
    raise ValueError("Runbook/parameters not allowed")

def kind_for(runbook):
    return "service" if runbook == "restore-selector" else "deployment"

def proposal(context, incident, runbook, target, params):
    current = kubectl(context, "get", kind_for(runbook), target, "-o", "json")
    changes = patch_for(runbook, target, params, current)
    return {"schema_version": 1, "action_id": str(uuid.uuid4()), "incident_id": incident,
            "context": context, "namespace": NAMESPACE, "runbook": runbook, "target": target,
            "parameters": params, "uid": current["metadata"]["uid"],
            "resource_version": current["metadata"]["resourceVersion"], "patch": changes,
            "expires_at": int(time.time()) + 900}

def sign(action, key, approver):
    approval = {"action": action, "approver": approver, "approved_at": int(time.time())}
    return {**approval, "signature": hmac.new(key, canonical(approval), hashlib.sha256).hexdigest()}

def validate(approved, key, now=None):
    payload = {k: v for k, v in approved.items() if k != "signature"}
    expected = hmac.new(key, canonical(payload), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, approved.get("signature", "")):
        raise ValueError("Invalid approval signature")
    action = approved["action"]
    now = time.time() if now is None else now
    if not approved.get("approver") or approved["approved_at"] > now + 30 or action["expires_at"] <= now:
        raise ValueError("Approval expired or invalid")
    if action["namespace"] != NAMESPACE:
        raise ValueError("Namespace not allowed")
    return action

def execute(approved, key, database, context):
    action = validate(approved, key)
    if action["context"] != context:
        raise ValueError("Unexpected cluster context")
    current = kubectl(context, "get", kind_for(action["runbook"]), action["target"], "-o", "json")
    if current["metadata"]["uid"] != action["uid"] or current["metadata"]["resourceVersion"] != action["resource_version"]:
        raise ValueError("Resource changed since proposal; investigate and request new approval")
    patch = patch_for(action["runbook"], action["target"], action["parameters"], current)
    if patch != action["patch"]:
        raise ValueError("Patch differs from approved action")
    db = sqlite3.connect(database)
    db.execute("CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, approval TEXT, result TEXT)")
    try:
        db.execute("INSERT INTO actions VALUES (?, ?, ?)", (action["action_id"], json.dumps(approved), "attempting"))
        db.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError("Approval already consumed") from exc
    patch = [{"op": "test", "path": "/metadata/uid", "value": action["uid"]},
             {"op": "test", "path": "/metadata/resourceVersion", "value": action["resource_version"]}] + patch
    path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(patch, f); path = f.name
        result = kubectl(context, "patch", kind_for(action["runbook"]), action["target"],
                         "--type=json", "--patch-file", path, "-o", "json")
        db.execute("UPDATE actions SET result=? WHERE id=?", ("applied; business verification required", action["action_id"]))
        db.commit()
        return {"action_id": action["action_id"], "status": "applied", "resource_version": result["metadata"]["resourceVersion"]}
    except Exception:
        db.execute("UPDATE actions SET result=? WHERE id=?", ("failed or uncertain; inspect cluster", action["action_id"]))
        db.commit()
        raise
    finally:
        if path: os.unlink(path)
        db.close()

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    s = p.add_subparsers(dest="command", required=True)
    a = s.add_parser("propose")
    for name in ["context", "incident", "runbook", "target", "parameters", "out"]: a.add_argument("--"+name, required=True)
    a = s.add_parser("approve")
    for name in ["proposal", "key-file", "approver", "out"]: a.add_argument("--"+name, required=True)
    a = s.add_parser("execute")
    for name in ["approval", "key-file", "database", "context"]: a.add_argument("--"+name, required=True)
    args = p.parse_args()
    if args.command == "propose":
        Path(args.out).write_text(json.dumps(proposal(args.context, args.incident, args.runbook, args.target, json.loads(args.parameters)), indent=2), encoding="utf-8")
    else:
        key = Path(args.key_file).read_bytes()
        if len(key) < 32: raise ValueError("Approval key must contain at least 32 random bytes")
        if args.command == "approve":
            data = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
            print(json.dumps(data, indent=2))
            if input("Type the action_id to approve this exact change: ").strip() != data["action_id"]:
                raise SystemExit("Not approved")
            Path(args.out).write_text(json.dumps(sign(data, key, args.approver), indent=2), encoding="utf-8")
        else:
            print(json.dumps(execute(json.loads(Path(args.approval).read_text(encoding="utf-8")), key, args.database, args.context)))

import copy
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("action", Path(__file__).parents[1]/"tools/action.py")
action = importlib.util.module_from_spec(spec)
spec.loader.exec_module(action)
KEY = b"a"*32

def sample():
    return {"namespace":"lab-app", "expires_at":10**12, "action_id":"test", "parameters":{"replicas":1}}

def test_modified_parameters_rejected():
    signed = action.sign(sample(), KEY, "operator")
    signed["action"]["parameters"]["replicas"] = 2
    with pytest.raises(ValueError, match="signature"): action.validate(signed, KEY)

def test_valid_signature_and_expiry():
    signed = action.sign(sample(), KEY, "operator")
    assert action.validate(signed, KEY)["namespace"] == "lab-app"
    with pytest.raises(ValueError, match="expired"): action.validate(signed, KEY, now=10**12)

def test_namespace_and_arbitrary_target_rejected():
    data = sample(); data["namespace"] = "kube-system"
    with pytest.raises(ValueError, match="Namespace"): action.validate(action.sign(data,KEY,"operator"),KEY)
    with pytest.raises(ValueError): action.patch_for("set-replicas","postgresql",{"replicas":1},{})
    with pytest.raises(ValueError): action.patch_for("set-replicas","cart",{"replicas":100},{})
    with pytest.raises(ValueError): action.patch_for("arbitrary-shell","cart",{}, {})

def test_replay_and_changed_resource_rejected(monkeypatch, tmp_path):
    current={"metadata":{"uid":"u","resourceVersion":"1"},"spec":{"replicas":0}}
    monkeypatch.setattr(action,"kubectl",lambda *a: copy.deepcopy(current))
    proposal=action.proposal("lab","incident","set-replicas","cart",{"replicas":1})
    signed=action.sign(proposal,KEY,"operator")
    assert action.execute(signed,KEY,tmp_path/"audit.db","lab")["status"] == "applied"
    with pytest.raises(ValueError,match="consumed"): action.execute(signed,KEY,tmp_path/"audit.db","lab")
    current["metadata"]["resourceVersion"]="2"
    with pytest.raises(ValueError,match="changed"): action.execute(signed,KEY,tmp_path/"other.db","lab")

def test_wrong_cluster_rejected(tmp_path):
    signed=action.sign({**sample(),"context":"lab"},KEY,"operator")
    with pytest.raises(ValueError,match="context"): action.execute(signed,KEY,tmp_path/"audit.db","production")

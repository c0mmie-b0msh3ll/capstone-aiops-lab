"""Operator-only live acceptance test. This is NOT an AI investigation or human approval.

Runs known scenarios and signs test actions with a throwaway in-memory key, labels
every approval automated-acceptance-test, and always resets the case afterwards.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import action
import faults

def raw(context, *args):
    return subprocess.run(["kubectl", "--context", context, "-n", "lab-app", *args], check=True, text=True, capture_output=True).stdout

def smoke(context):
    return json.loads(raw(context, "exec", "deployment/load-generator", "--", "python", "/app/tools/traffic.py", "--url", "http://web:8080"))

def settled_smoke(context):
    # Readiness can precede gRPC client's reconnection after a rollout.
    deadline=time.time()+60
    while True:
        try:
            return smoke(context)
        except subprocess.CalledProcessError:
            if time.time()>deadline:raise
            time.sleep(3)

def symptomatic(context, case):
    path = "/api/products" if case in ("F03","F05","F06") else "/api/cart"
    code = "import httpx,uuid; r=httpx.get('http://web:8080"+path+"',cookies={'lab_session':str(uuid.uuid4())},timeout=8); print(r.status_code)"
    try:
        result = raw(context,"exec","deployment/load-generator","--","python","-c",code)
        if case == "F01":
            healthy = raw(context,"exec","deployment/load-generator","--","python","-c","import httpx; print(httpx.get('http://web:8080/api/products',timeout=8).status_code)")
            return int(result.strip()) >= 500 and int(healthy.strip()) == 200
        return int(result.strip()) >= 500
    except subprocess.CalledProcessError as exc:
        return case != "F01" and ("ConnectError" in exc.stderr or "ReadTimeout" in exc.stderr)

def run(context, case, state, executor_kubeconfig=None):
    settled_smoke(context)
    kind,target,_ = faults.CASES[case]
    before = faults.k(context,"get",kind,target,"-o","json")
    params={}
    if case=="F01": runbook,params="restore-selector",{"app":"cart"}
    elif case=="F02": runbook,params="restore-endpoint",{"name":"VALKEY_ADDR","value":"valkey:6379"}
    elif case=="F03": runbook,params="restore-endpoint",{"name":"DB_CONNECTION_STRING","value":"postgres://lab:$(DB_PASSWORD)@postgresql:5432/lab?sslmode=disable"}
    elif case=="F04": runbook,params="restore-image",{"image":before["spec"]["template"]["spec"]["containers"][0]["image"]}
    elif case=="F05": runbook="restore-command"
    elif case=="F06": runbook,params="restore-probe",{"path":"/healthz"}
    elif case=="F07": runbook,params="restore-memory",{"memory":"384Mi"}
    elif case=="F08": runbook,params="set-replicas",{"replicas":1}
    record={"case":case,"started_at":time.time(),"test_type":"operator acceptance, not AI evaluation"}
    try:
        subprocess.run([sys.executable,str(Path(__file__).with_name('faults.py')),"inject","--context",context,"--case",case,"--state-dir",str(state/'operator')],check=True,capture_output=True,text=True)
        deadline=time.time()+100
        while not symptomatic(context,case):
            if time.time()>deadline: raise RuntimeError("Fault did not produce business symptom")
            time.sleep(3)
        pods=faults.k(context,"get","pods","-l","app="+target,"-o","json")
        record['fault_pods']=[{'name':p['metadata']['name'],'statuses':p.get('status',{}).get('containerStatuses',[])} for p in pods['items']]
        if case=='F07' and not any(s.get('lastState',{}).get('terminated',{}).get('reason')=='OOMKilled' or s.get('state',{}).get('terminated',{}).get('reason')=='OOMKilled' for p in pods['items'] for s in p.get('status',{}).get('containerStatuses',[])):
            raise RuntimeError("Memory symptom observed but OOMKilled not yet proven")
        record['fault_observed_at']=time.time()
        key=os.urandom(32)
        for attempt in range(5):
            proposal=action.proposal(context,case+'-acceptance',runbook,target,params)
            approved=action.sign(proposal,key,'automated-acceptance-test')
            try:
                original_kubeconfig=os.environ.get('KUBECONFIG')
                try:
                    if executor_kubeconfig:
                        os.environ['KUBECONFIG']=executor_kubeconfig
                    record['action']=action.execute(approved,key,str(state/'acceptance.db'),context)
                finally:
                    if original_kubeconfig is None:
                        os.environ.pop('KUBECONFIG',None)
                    else:
                        os.environ['KUBECONFIG']=original_kubeconfig
                break
            except ValueError as exc:
                if 'changed' not in str(exc) or attempt==4: raise
        if kind=='deployment': raw(context,'rollout','status','deployment/'+target,'--timeout=120s')
        deadline=time.time()+45
        while True:
            try:
                record['business_verification']=smoke(context);break
            except subprocess.CalledProcessError:
                if time.time()>deadline:raise
                time.sleep(3)
        record['status']='passed'
    except Exception as exc:
        record['status']='failed';record['error']=str(exc)
    finally:
        if (state/'operator/active.json').exists():
            subprocess.run([sys.executable,str(Path(__file__).with_name('faults.py')),'reset','--context',context,'--state-dir',str(state/'operator')],check=True,capture_output=True,text=True)
            if kind=='deployment':raw(context,'rollout','status','deployment/'+target,'--timeout=120s')
            settled_smoke(context)
        record['finished_at']=time.time()
        (state/(case+'.json')).write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in record.items() if k!='fault_pods'}),flush=True)
    if record['status']!='passed':raise RuntimeError(record.get('error','case failed'))

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--context',required=True)
    p.add_argument('--cases',nargs='+',choices=list(faults.CASES),default=['F01'])
    p.add_argument('--state-dir',default='.lab-state/acceptance')
    p.add_argument('--executor-kubeconfig',help='Use a distinct remediator-role kubeconfig for the approved write')
    p.add_argument('--confirm-test-actions',action='store_true',required=True)
    a=p.parse_args();state=Path(a.state_dir);state.mkdir(parents=True,exist_ok=True)
    for case in a.cases:run(a.context,case,state,a.executor_kubeconfig)

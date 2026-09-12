# Validation record

Current implementation: deployed and validated on AWS EKS in account 589077667575, region us-east-1.

- Four container images build successfully (Cart .NET 10, inherited Catalog Go, inherited image server, Python web).
- Helm lint: application and observability values pass.
- Terraform validate passes; reviewed plan adds lab resources without changing/deleting existing AWS resources.
- Five approval tests pass: tampered parameters, expiry, namespace/target allowlist, replay, changed resource and wrong context.
- Real-container HTTP journey passes: list/details/image, add two products, read quantities, remove only one, preserve the other, empty cart.
- Browser: product list, add and bag rendering verified.
- Local OTel -> Jaeger: web/catalog/cart/image-provider traces present.
- Local Prometheus: real `lab_http_requests_total` metrics present.

## EKS acceptance completed

- EKS control plane, two workers, EBS CSI and metrics-server active.
- All application and observability Pods Ready after reset.
- Real HTTP business journey passes on EKS.
- F01–F08 each have a successful fault -> runbook -> business recovery run. See evidence/acceptance.json.
- F07 has Kubernetes OOMKilled evidence (exit 137), not merely a generic crash.
- F08 write was executed using the dedicated remediator AssumeRole kubeconfig.
- Investigator AssumeRole reads Pods and resource metrics, port-forwards Prometheus/Jaeger, and is denied Secret reads, exec and workload writes.
- Remediator can patch named lab Cart resources and is denied kube-system writes.
- Prometheus returns request and Pod-restart metric series; Jaeger returns web/catalog/cart/image-provider traces; Grafana dashboard lab-health is accessible.

## Practical boundaries

These are operator acceptance tests with known runbooks and test signatures, not proof that an AI agent diagnoses the incidents correctly or that a human approved the automated tests. The CLI manual signer is supplied; team AI still integrates its agent and approval UI/executor isolation.

Roles currently trust the existing operator for testing. Add the actual AI and executor principal ARNs before team use. Do not give admin source credentials or signing keys to the AI runtime.

F01 was calibrated to recycle web clients because established gRPC connections survived selector changes. F07 uses a 16Mi limit/8Mi request to make OOM reproducible; the original 64Mi assumption was not used. Reset waits for business recovery because Kubernetes readiness can precede gRPC reconnection. F05 is a crashing-entrypoint fixture, not a separately versioned bad image.

A successful run of each case is recorded; repeated-run reliability/load benchmarks and concurrent edits to the same cart have not been established. Default load uses independent sessions and sequential operations. Historical upstream components/workflows remain for provenance but are not part of the lab deployment.


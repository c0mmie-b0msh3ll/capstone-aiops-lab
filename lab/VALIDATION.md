# Validation record

Current implementation: source built and local business flows verified; AWS deployment validation in progress.

- Four container images build successfully (Cart .NET 10, inherited Catalog Go, inherited image server, Python web).
- Helm lint: application and observability values pass.
- Terraform validate passes; reviewed plan adds lab resources without changing/deleting existing AWS resources.
- Five approval tests pass: tampered parameters, expiry, namespace/target allowlist, replay, changed resource and wrong context.
- Real-container HTTP journey passes: list/details/image, add two products, read quantities, remove only one, preserve the other, empty cart.
- Browser: product list, add and bag rendering verified.
- Local OTel -> Jaeger: web/catalog/cart/image-provider traces present.
- Local Prometheus: real `lab_http_requests_total` metrics present.

Do not infer all eight Kubernetes fault cases are verified until the EKS evidence below is recorded.

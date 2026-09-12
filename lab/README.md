# Capstone AIOps Lab

Fork của `nguyenductien-qnm/capstone-phase-3`. Điểm bắt đầu của bản lab là thư mục **lab/**; các workflow, Terraform và chart ở thư mục cũ là di sản upstream, không dùng để deploy lab.

## Phạm vi

- EKS 1.35, hai EC2 worker t3.large, mặc định us-east-1.
- Web Orbit mới, gọi Catalog Go và Cart .NET kế thừa qua gRPC.
- Duyệt/chi tiết/ảnh sản phẩm, thêm/xem/xóa từng món/làm trống giỏ.
- PostgreSQL và Valkey trong cluster, PVC EBS; dữ liệu catalog seed từ upstream.
- Không triển khai Kafka, checkout, payment, AI review, CDN, domain hoặc ArgoCD.
- Flagd nhỏ được giữ để tương thích SDK của service; flags mặc định rỗng, injector chỉ thay đổi Kubernetes.
- Namespace `lab-app` cho ứng dụng, `lab-observe` cho OTel Collector, Prometheus, Jaeger, Grafana, kube-state-metrics.

## Triển khai

Yêu cầu: AWS CLI, Terraform >=1.6, Helm 3, kubectl, Docker, Python 3.12; PowerShell 7 cho script.

1. Copy `terraform/terraform.tfvars.example` thành `terraform/terraform.tfvars`; điền đúng account, principal và IP public `/32`. Provider khóa account bằng `allowed_account_ids`.
2. Từ `lab/terraform`: `terraform init`, `terraform plan -out=lab.tfplan`, kiểm tra rồi `terraform apply lab.tfplan`.
3. Build bốn image từ **repo root**:

```powershell
docker build -t capstone-lab-web:dev -f lab/Dockerfile .
docker build -t capstone-lab-cart:dev -f techx-corp-platform/src/cart/src/Dockerfile techx-corp-platform
docker build -t capstone-lab-catalog:dev -f techx-corp-platform/src/product-catalog/Dockerfile techx-corp-platform
docker build -t capstone-lab-image-provider:dev -f techx-corp-platform/src/image-provider/Dockerfile techx-corp-platform
```

4. Đăng nhập ECR bằng `aws ecr get-login-password`; tag/push image vào repository từ `terraform output repositories`. Dùng tag duy nhất cho mỗi build (ECR immutable).
5. Tạo `lab/generated/images.yaml` chứa `workloads.<service>.image` cho web/cart/catalog/image-provider; load-generator dùng cùng image với web.
6. Từ repo root: `./lab/deploy.ps1 -Images lab/generated/images.yaml`. Script sinh DB/Grafana secrets, không ghi password vào Git; Helm deploy baseline. Không chạy script này trong incident vì Helm upgrade có thể ghi đè thay đổi trực tiếp.

Ví dụ image override:

```yaml
workloads:
  web: {image: ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/capstone-aiops-lab/web:BUILD}
  load-generator: {image: ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/capstone-aiops-lab/web:BUILD}
  cart: {image: ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/capstone-aiops-lab/cart:BUILD}
  catalog: {image: ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/capstone-aiops-lab/catalog:BUILD}
  image-provider: {image: ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/capstone-aiops-lab/image-provider:BUILD}
```

## Mở demo

Kubeconfig riêng: `.lab-state/kubeconfig`, context `capstone-aiops-lab`. Đặt biến `KUBECONFIG` tới đường dẫn tuyệt đối đó. Mỗi port-forward chạy trong terminal riêng, mặc định chỉ bind localhost:

```powershell
kubectl --context capstone-aiops-lab -n lab-app port-forward service/web 18081:8080
kubectl --context capstone-aiops-lab -n lab-observe port-forward service/grafana 13000:3000
kubectl --context capstone-aiops-lab -n lab-observe port-forward service/prometheus 19090:9090
kubectl --context capstone-aiops-lab -n lab-observe port-forward service/jaeger 16686:16686
python lab/tools/traffic.py --url http://localhost:18081
```

Grafana anonymous chỉ có quyền Viewer và chỉ truy cập qua đường nội bộ/port-forward. Jaeger dùng memory giới hạn 10.000 traces: export bằng chứng trước restart; không phải kho lưu lâu dài. Prometheus giữ 2 ngày/1GB. Kubernetes audit log ở CloudWatch, retention 7 ngày.

## Demo lỗi

```powershell
python lab/tools/faults.py inject --context capstone-aiops-lab --case F01
python lab/tools/detect.py --url http://localhost:18081 --output .lab-state/incidents.jsonl
# AI điều tra và đề xuất action theo INTEGRATION.md.
python lab/tools/faults.py reset --context capstone-aiops-lab
python lab/tools/traffic.py --url http://localhost:18081
```

Chỉ inject một case tại một thời điểm. `faults.py` là công cụ operator, không cấp cho runtime AI. Sau inject phải kiểm tra lỗi nghiệp vụ và bằng chứng K8s; lệnh patch thành công chưa đủ chứng minh case xảy ra. Reset khôi phục spec trước inject, không xóa PVC/dữ liệu. Chạy journey tạo phiên mới để tránh phụ thuộc giỏ từ lượt trước.

| Case | Fault | Runbook sửa |
|---|---|---|
| F01 | Cart Service selector sai | restore-selector |
| F02 | Endpoint Valkey sai | restore-endpoint |
| F03 | Endpoint PostgreSQL sai | restore-endpoint |
| F04 | Image Cart không tồn tại | restore-image |
| F05 | Web entrypoint crash (fixture mô phỏng bad release) | restore-command |
| F06 | Readiness path Web sai | restore-probe |
| F07 | Limit Cart 16Mi, request 8Mi (ép OOM lúc khởi động) | restore-memory |
| F08 | Replica Cart về 0 | set-replicas |

F07 chỉ được tính OOM scenario khi `lastState.terminated.reason=OOMKilled`; nếu runtime tự abort hoặc lỗi khác thì báo unsupported/khác loại, không chấm nhầm. F05 là fixture command crash, chưa phải một image release riêng. Tắt HPA/auto-remediation trong MVP để tránh hệ thống khác sửa hộ.

## Reset và hủy

`faults.py reset` phục hồi scenario hiện tại. Reset dữ liệu hoàn toàn là thao tác operator riêng: backup bằng chứng, uninstall app, xóa **chỉ** PVC trong `lab-app`, rồi deploy và seed lại. Không dùng thao tác này làm action của AI.

Khi kết thúc dùng lab: uninstall hai Helm release, xóa PVC lab và chờ EBS được thu hồi **trước** `terraform destroy`. ECR immutable vẫn chứa image: xóa image trong bốn repository lab trước khi destroy. Không destroy VPC/cluster đang dùng bởi nhóm khác. Terraform state local trong `lab/terraform` cần được giữ/backup an toàn, không commit; chuyển remote backend khi có nhiều operator.

## Kiểm chứng

`python -m pytest lab/tests -q`; `helm lint lab/charts/lab -f lab/app-values.yaml`; lint tương tự với `observe-values.yaml`; `terraform validate`. Smoke qua HTTP phải chạy trên deployment thực tế. Xem `VALIDATION.md` để biết chính xác những gì đã kiểm tra; không suy ra tất cả fault đã pass từ unit tests.

F01 đổi selector và recycle web client Pods để loại kết nối gRPC cũ; chỉ tính lỗi khi Catalog vẫn trả thành công nhưng Cart thất bại. F07 chờ bằng chứng OOMKilled trước khi báo inject thành công.

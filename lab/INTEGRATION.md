# Contract cho team AI — agent chạy local

## Danh tính và đường mạng

Terraform nhận `ai_principal_arns` và `executor_principal_arns`. Mỗi danh sách không rỗng sẽ tạo role tương ứng, trust policy, `eks:DescribeCluster`, EKS access entry và group RBAC. Mặc định không trust account-wide và không có ARN giả. Team cấp principal nguồn quyền `sts:AssumeRole` tới role đích (bắt buộc kiểm tra cả hai phía khi cross-account).

`lab:investigator`: đọc workload/events/logs/endpoints, `kubectl top pods`; không đọc Secrets, không exec, không patch. Trong `lab-observe` được port-forward để đọc Prometheus/Jaeger. Role remediator không có quyền assume-role dây chuyền và không được cấp cho tiến trình suy luận AI.

API EKS public giới hạn IP đã khai báo, private endpoint bật cho node. Cập nhật `allowed_cidrs` bằng Terraform khi IP local thay đổi. IAM thành công không thay thế đường mạng. Không mở telemetry ra Internet.

```powershell
./lab/connect.ps1 -RoleArn arn:aws:iam::ACCOUNT:role/capstone-aiops-lab-investigator -SourceProfile your-profile
```

Script tạo kubeconfig riêng và dùng AWS CLI exec credential để refresh token. Truyền đường dẫn kubeconfig này cho Kubernetes client của AI. Đây là access vào cluster, không phải deploy agent vào EKS.

## Công cụ AI cần implement

1. `get_workloads`, `get_events`, `get_logs`, `get_endpoints`, `get_pod_metrics`: Kubernetes API với quyền đọc.
2. `query_metrics`: Prometheus `/api/v1/query`, `/api/v1/query_range` qua localhost:19090.
3. `query_traces`: Jaeger `/api/services`, `/api/traces?service=web&lookback=15m` qua localhost:16686.
4. `propose_action`: dùng `tools/action.py propose` hoặc gọi hàm `proposal`.
5. `request_approval`: chuyển proposal đầy đủ sang operator/UI tin cậy.
6. `execute_approved_action`: gọi executor độc lập; kiểm tra signature, expiry, precondition và action ID.
7. `verify`: chạy flow nghiệp vụ bị ảnh hưởng, kiểm tra telemetry và ghi báo cáo.

Không cần quyền Git. Executor viết trực tiếp Kubernetes API. Không cho agent tự gọi `approve`, đọc key hay dùng credentials admin/operator. File HMAC ở bản CLI là reference integration, **không** tự tạo cách ly bảo mật nếu agent và signer cùng OS user có thể đọc file nhau. Khi nối AI thật, để signer/key/executor trong process/service thuộc người vận hành hoặc host/OS identity riêng. Approval UI hiện chưa được cung cấp; CLI có yêu cầu nhập đúng action ID bằng tay.

## Incident và action

`detect.py` phát JSONL sau ba lần probe lỗi liên tiếp. Incident chỉ gồm schema_version, incident_id, observed_at, namespace, symptoms. Không chứa case ID/ground truth. Detector này là baseline tích hợp, chưa phải full anomaly detection.

Action proposal gồm incident_id/action_id, context, namespace, runbook, target, parameters, uid, resource_version, patch, expires_at. Approval ký toàn bộ payload cộng approver/timestamp. SQLite của executor ghi trạng thái trước khi patch, không replay approval ngay cả khi kết quả không chắc chắn. `applied` chỉ có nghĩa API write thành công; AI phải verify mới được kết luận `recovered`.

RBAC không thể giới hạn mọi field của Deployment: allowlist field và tham số do executor enforce. Không thêm arbitrary shell/kubectl vào tool của agent. Credentials role ghi chỉ nằm ở executor.

## Thử approval thủ công

Tạo key ngẫu nhiên >=32 bytes trên máy operator, ngoài vùng AI đọc được. Ví dụ runbook cho F01:

```powershell
python lab/tools/action.py propose --context capstone-aiops-lab --incident INCIDENT-ID --runbook restore-selector --target cart --parameters '{"app":"cart"}' --out proposal.json
python lab/tools/action.py approve --proposal proposal.json --key-file OPERATOR-KEY --approver OPERATOR --out approved.json
python lab/tools/action.py execute --context capstone-aiops-lab --approval approved.json --key-file OPERATOR-KEY --database OPERATOR-AUDIT.db
```

Các tham số khác:

| Runbook/target | Parameters |
|---|---|
| restore-endpoint/cart | `{"name":"VALKEY_ADDR","value":"valkey:6379"}` |
| restore-endpoint/catalog | `{"name":"DB_CONNECTION_STRING","value":"postgres://lab:$(DB_PASSWORD)@postgresql:5432/lab?sslmode=disable"}` |
| restore-image/cart | `{"image":"SAME-ECR-REPOSITORY:KNOWN-GOOD-TAG"}` |
| restore-command/web | `{}` |
| restore-probe/web | `{"path":"/healthz"}` |
| restore-memory/cart | `{"memory":"384Mi"}` |
| set-replicas/cart | `{"replicas":1}` |

Không tự đoán known-good image: dùng inventory deployment/image và bằng chứng rollout, sau đó người duyệt xác nhận. Nếu resourceVersion thay đổi trong lúc chờ duyệt, proposal bị từ chối; đọc lại và xin approval mới.

## Tiêu chí bàn giao AI

- AssumeRole thành công từ máy local; `can-i get pods` = yes, patch/exec/secrets = no với investigator.
- Prometheus query và Jaeger traces đọc được; tên dịch vụ: web, catalog, cart.
- Baseline journey B01–B06 pass; incident xuất hiện khi inject F01.
- AI đề xuất đúng selector; operator duyệt; executor sửa; journey phục hồi.
- Approval bị sửa, hết hạn, replay hoặc sai cluster bị từ chối.
- Ground truth và operator audit nằm ngoài quyền của AI; user/source credentials có quyền admin không được đưa vào runtime AI.

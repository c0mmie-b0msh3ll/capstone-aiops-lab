# Lệnh điều khiển lỗi — AIOps Lab

Các lệnh bên dưới dùng **PowerShell**, trên máy operator đã đăng nhập AWS. Chỉ bơm **một case mỗi lần**, reset và kiểm tra phục hồi trước khi chuyển case.

## 1. Chuẩn bị terminal

Chạy lại phần này mỗi khi mở terminal mới:

```powershell
cd E:\capstone-aiops-lab
$env:KUBECONFIG = 'E:\capstone-aiops-lab\.lab-state\kubeconfig'
kubectl --context capstone-aiops-lab -n lab-app get pods
```

Dùng kubeconfig operator để inject/reset. Kubeconfig investigator của AI không có quyền sửa hạ tầng.

## 2. Demo nhanh: bơm lỗi → quan sát → reset

### Bơm F02: Cart dùng sai địa chỉ Valkey

```powershell
python lab/tools/faults.py inject --context capstone-aiops-lab --case F02
```

Traffic mặc định đã chạy trong cluster. Mở web, thử thêm sản phẩm vào giỏ: xem sản phẩm vẫn được nhưng giỏ hàng lỗi. Đợi 30–60 giây để dashboard cập nhật.

- Web: http://localhost:18081
- Dashboard: http://localhost:13000/d/lab-health?refresh=5s
- Jaeger: http://localhost:16686
- Prometheus: http://localhost:19090

Có thể chạy thêm một hành trình để xác nhận lỗi:

```powershell
python lab/tools/traffic.py --url http://localhost:18081
```

Trong lúc lỗi, script báo fail là đúng kỳ vọng. Lệnh này chỉ tạo request, không sửa cấu hình.

### Tắt lỗi / reset

```powershell
python lab/tools/faults.py reset --context capstone-aiops-lab
```

Reset phục hồi **toàn bộ spec của tài nguyên bị inject** từ bản chụp ngay trước khi bơm lỗi. Nó không phải thao tác xóa cluster hay xóa dữ liệu. Những thay đổi khác trên cùng spec sau thời điểm inject cũng có thể bị ghi đè khi reset.

### Xác nhận phục hồi

```powershell
kubectl --context capstone-aiops-lab -n lab-app rollout status deployment/cart --timeout=120s
kubectl --context capstone-aiops-lab -n lab-app rollout status deployment/catalog --timeout=120s
kubectl --context capstone-aiops-lab -n lab-app rollout status deployment/web --timeout=120s
python lab/tools/traffic.py --url http://localhost:18081
```

Nếu Pod đã Ready nhưng gRPC chưa kết nối lại, đợi vài giây rồi chạy lại traffic. Dashboard dùng cửa sổ 1 phút nên sau sửa cần khoảng 1 phút để lỗi cũ trôi hết; không chỉ dựa vào màu Pod để kết luận đã phục hồi.

## 3. Các case và triệu chứng

| Case | Cấu hình bị thay đổi | Triệu chứng cần nhìn |
|---|---|---|
| F01 | Service Cart chọn sai Pod | Giỏ hàng lỗi; Pod Cart có thể vẫn Ready |
| F02 | Cart dùng sai địa chỉ Valkey | Giỏ hàng lỗi; xem sản phẩm vẫn có thể thành công |
| F03 | Catalog dùng sai địa chỉ PostgreSQL | Xem sản phẩm lỗi; hành trình tự động có thể dừng trước bước giỏ hàng |
| F04 | Cart dùng image tag không tồn tại | Pod mới ErrImagePull / ImagePullBackOff; kiểm tra thêm ảnh hưởng giỏ hàng |
| F05 | Web chạy lệnh khởi động gây crash | Web mất khả năng phục vụ; Pod crash/restart |
| F06 | Web dùng sai đường dẫn readiness probe | Web Pod có thể Running nhưng không Ready; Service không đưa traffic tới Pod |
| F07 | Cart bị giới hạn RAM còn 16Mi | OOMKilled; restart; giỏ hàng lỗi |
| F08 | Cart bị scale về 0 replica | Không có Pod Cart phục vụ; giỏ hàng lỗi |

**Chọn đúng một lệnh bên dưới**, không chạy lần lượt cả khối mà chưa reset.

### F01 — Sai Service selector Cart

```powershell
python lab/tools/faults.py inject --context capstone-aiops-lab --case F01
```

Case này còn tạo lại Web Pod để ngắt kết nối gRPC cũ, nên port-forward web có thể bị ngắt.

### F02 — Sai endpoint Valkey

```powershell
python lab/tools/faults.py inject --context capstone-aiops-lab --case F02
```

### F03 — Sai endpoint PostgreSQL

```powershell
python lab/tools/faults.py inject --context capstone-aiops-lab --case F03
```

### F04 — Image Cart không tồn tại

```powershell
python lab/tools/faults.py inject --context capstone-aiops-lab --case F04
```

### F05 — Web crash khi khởi động

```powershell
python lab/tools/faults.py inject --context capstone-aiops-lab --case F05
```

### F06 — Web sai readiness probe

```powershell
python lab/tools/faults.py inject --context capstone-aiops-lab --case F06
```

### F07 — Cart thiếu RAM / OOMKilled

```powershell
python lab/tools/faults.py inject --context capstone-aiops-lab --case F07
```

Injector chờ tối đa khoảng 90 giây để quan sát OOMKilled. Nếu báo chưa quan sát được OOM, cấu hình lỗi vẫn có thể đã được áp dụng: reset trước khi thử lại.

### F08 — Cart không có replica

```powershell
python lab/tools/faults.py inject --context capstone-aiops-lab --case F08
```

## 4. Xem danh sách case và lỗi đang active

Danh sách case hỗ trợ:

```powershell
python lab/tools/faults.py list --context capstone-aiops-lab
```

Xem bản ghi inject đang active trên máy operator:

```powershell
if (Test-Path '.lab-state/operator/active.json') {
    Get-Content '.lab-state/operator/active.json' -Raw |
        ConvertFrom-Json |
        Select-Object case, description, context, kind, name
} else {
    Write-Host 'Không có bản ghi inject active trong thư mục state này.'
}
```

Bản ghi active phục vụ operator, không đưa cho AI để tránh lộ đáp án. Không có bản ghi không chứng minh cluster khỏe; luôn kiểm tra traffic.

## 5. Lệnh điều tra nhanh

```powershell
# Pod và số replica
kubectl --context capstone-aiops-lab -n lab-app get pods,deployments

# Sự kiện gần nhất: image pull, probe, scheduling...
kubectl --context capstone-aiops-lab -n lab-app get events --sort-by=.metadata.creationTimestamp

# Log Cart: lỗi kết nối Valkey
kubectl --context capstone-aiops-lab -n lab-app logs deployment/cart --since=5m --tail=100

# Log Catalog: lỗi kết nối PostgreSQL
kubectl --context capstone-aiops-lab -n lab-app logs deployment/catalog --since=5m --tail=100

# Chi tiết Pod Cart: trạng thái hiện tại, lần kết thúc trước, OOMKilled
kubectl --context capstone-aiops-lab -n lab-app describe pods -l app=cart

# Chi tiết Pod Web: crash hoặc readiness probe
kubectl --context capstone-aiops-lab -n lab-app describe pods -l app=web

# Service Cart và endpoint thực tế
kubectl --context capstone-aiops-lab -n lab-app get service cart -o wide
kubectl --context capstone-aiops-lab -n lab-app get endpointslices -l kubernetes.io/service-name=cart
```

F08 không còn Pod Cart nên lệnh xem log Cart có thể không trả được log. Đọc Deployment để kiểm tra replica.

## 6. Mở lại web / Grafana / Jaeger / Prometheus

Mỗi port-forward chạy trong **một terminal riêng** và giữ terminal đó mở. Chỉ chạy lại khi cổng chưa hoạt động; nếu báo cổng đang được sử dụng, kiểm tra cửa sổ port-forward cũ.

### Terminal Web

```powershell
$env:KUBECONFIG = 'E:\capstone-aiops-lab\.lab-state\kubeconfig'
kubectl --context capstone-aiops-lab -n lab-app port-forward service/web 18081:8080
```

### Terminal Grafana

```powershell
$env:KUBECONFIG = 'E:\capstone-aiops-lab\.lab-state\kubeconfig'
kubectl --context capstone-aiops-lab -n lab-observe port-forward service/grafana 13000:3000
```

### Terminal Jaeger

```powershell
$env:KUBECONFIG = 'E:\capstone-aiops-lab\.lab-state\kubeconfig'
kubectl --context capstone-aiops-lab -n lab-observe port-forward service/jaeger 16686:16686
```

### Terminal Prometheus

```powershell
$env:KUBECONFIG = 'E:\capstone-aiops-lab\.lab-state\kubeconfig'
kubectl --context capstone-aiops-lab -n lab-observe port-forward service/prometheus 19090:9090
```

F01/F05/F06 có thể ngắt port-forward Web do Web Pod thay đổi. Với F05/F06, reset và chờ Web phục hồi rồi mở lại port-forward.

**Ctrl+C ở terminal port-forward chỉ đóng kết nối localhost, không reset lỗi. Ctrl+C ở terminal inject cũng không hoàn tác thay đổi đã áp dụng.** Muốn tắt lỗi phải chạy `faults.py reset`.

## 7. Traffic bổ sung từ máy local

Chạy một hành trình:

```powershell
python lab/tools/traffic.py --url http://localhost:18081
```

Chạy lặp, nghỉ 3 giây sau mỗi hành trình:

```powershell
python lab/tools/traffic.py --url http://localhost:18081 --loop --interval 3
```

Nhấn Ctrl+C để dừng traffic local. Load-generator mặc định trong cluster vẫn chạy. `--interval 3` là thời gian nghỉ, không phải RPS cố định.

## 8. Khi lệnh reset / inject báo lỗi

- **Reset the active case first:** chạy reset, kiểm tra phục hồi, rồi mới inject case khác.
- **Không tìm thấy active.json:** kiểm tra đã `cd E:\capstone-aiops-lab` chưa và đã reset trước đó chưa. Không tự tạo/xóa file state để bỏ qua kiểm tra.
- **Context mismatch / UID test thất bại:** kiểm tra kubeconfig và tài nguyên; có thể đối tượng đã bị tạo lại. Không ép reset sang cluster hoặc tài nguyên khác.
- **Unauthorized / Unable to locate credentials:** kiểm tra phiên AWS và kubeconfig operator bằng `aws sts get-caller-identity` rồi `kubectl ... get pods` như phần chuẩn bị.
- **Web không mở được nhưng Pod khỏe:** kiểm tra terminal port-forward, mở lại theo phần 6.

Nếu lúc inject dùng `--state-dir` riêng, reset phải dùng **cùng thư mục đó**, ví dụ:

```powershell
python lab/tools/faults.py reset --context capstone-aiops-lab --state-dir .lab-state/my-demo
```

Không chạy Helm upgrade/deploy để dọn lỗi trong lúc demo; nó có thể ghi đè thay đổi đang được điều tra. Dùng reset của injector để kết thúc case.

## 9. Demo với AI agent

**Operator inject → traffic phát sinh triệu chứng → AI điều tra → đề xuất runbook → người duyệt → executor sửa → chạy traffic xác nhận.**

Khi muốn quan sát AI xử lý, giữ lỗi active, chưa chạy reset. Sau khi AI hoàn tất và đã ghi nhận kết quả, operator reset để đóng case và chuẩn bị demo tiếp.

Hợp đồng tích hợp agent và luồng approve/executor: [INTEGRATION.md](INTEGRATION.md).

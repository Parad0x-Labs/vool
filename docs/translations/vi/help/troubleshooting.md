---
description: Cách khắc phục những sự cố thường gặp nhất.
translation: machine
source_sha256: 792c90fd68caf7409212d3742109c46e49d1f7e640aa3e9f70db9fe61ca55d77
---

# Khắc phục sự cố

## macOS từ chối mở ứng dụng

Điều này là bình thường: ứng dụng chưa được công chứng. Hãy kiểm tra mã kiểm tra trước, rồi mở
**Cài đặt hệ thống → Quyền riêng tư & Bảo mật** và bấm **Vẫn mở**. Trên macOS 14, bạn cũng có thể
giữ Control khi bấm vào VOOL và chọn **Mở**.

## Windows

Bản phát hành này không có bản tải xuống cho Windows. Trên Windows, VOOL được cài từ mã nguồn và ở mức
thử nghiệm; xem [hướng dẫn cài đặt](../getting-started/install.md#cài-từ-mã-nguồn).

## Không có mô hình cục bộ nào được liệt kê

1. Xác nhận môi trường chạy đang hoạt động.
2. Xác nhận đã tải về ít nhất một mô hình.
3. Xác nhận cổng khớp với cổng mà VOOL cần.

```bash
curl http://localhost:11434/api/tags
```

Nếu lệnh trên không trả về gì, môi trường chạy chưa hoạt động.

## Phản hồi chậm

Thường là do kích thước mô hình so với bộ nhớ RAM còn trống. Xem
[Chạy mô hình cục bộ](../../../guides/local-models.md).

## Bắt đầu lại từ đầu

Thoát VOOL và xóa thư mục cấu hình được liệt kê trong
[Cấu hình](../../../reference/configuration.md). Các tệp trong không gian làm việc không bị động đến.

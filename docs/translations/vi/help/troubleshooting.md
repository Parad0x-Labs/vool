---
description: Cách khắc phục những sự cố thường gặp nhất.
translation: machine
source_sha256: a681cc81febcca3f8c24291a03f3a9acd6b09cbf6573f251f03114e1d69ac61d
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

1. Xác nhận môi trường chạy mô hình cục bộ đã được cài — trên Linux, hãy cài [Ollama](https://ollama.com) trước nếu bạn chưa cài (xem [Chạy mô hình cục bộ](../guides/local-models.md#cài-một-môi-trường-chạy)).
2. Xác nhận môi trường chạy đang hoạt động.
3. Xác nhận đã tải về ít nhất một mô hình.
4. Xác nhận cổng khớp với cổng mà VOOL cần.

```bash
curl http://localhost:11434/api/tags
```

Nếu lệnh trên không trả về gì, môi trường chạy chưa hoạt động.

## Phản hồi chậm

Thường là do kích thước mô hình so với bộ nhớ RAM còn trống. Xem
[Chạy mô hình cục bộ](../guides/local-models.md).

## Lỗi hiển thị một mã

Mỗi sự cố đều có một mã cố định. [Lỗi và mã lỗi](../reference/errors.md) giải thích cách đọc mã, còn
[Sổ tay lỗi](../ERROR_BOOK.md) cho biết với từng mã: chuyện gì đã xảy ra, điều gì có thể đã xảy ra
rồi và cần làm gì tiếp theo.

## Bắt đầu lại từ đầu

Thoát VOOL và xóa thư mục "runtime home" được liệt kê trong
[Cấu hình](../reference/configuration.md). Việc này sẽ xóa các cuộc trò chuyện, bộ nhớ, cài đặt, các
khóa đã lưu và mọi ví mà VOOL giữ trên máy tính này, vì vậy hãy lưu những gì bạn cần trước: xem
[Sao lưu và khôi phục](backup-and-recovery.md). Các tệp trong không gian làm việc không bị động đến.

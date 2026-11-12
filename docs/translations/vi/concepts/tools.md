---
description: Công cụ là những hành động VOOL có thể thực hiện, và mỗi hành động đều được ghi lại.
translation: machine
source_sha256: 53722a7f30f4446b6d029465a402a823f60496a66008b5df150d201c1963b152
---

# Công cụ

Công cụ là một hành động mà runtime có thể thực hiện thay bạn — đọc một tệp, chạy một lệnh, tìm kiếm
trong một thư mục.

## Có thể kiểm tra theo thiết kế

Mỗi lệnh gọi công cụ ghi lại những gì được yêu cầu, những gì đã chạy và những gì trả về. Một lần chạy có
thể được đọc lại sau đó mà không cần chạy lại.

## Quyền

Một công cụ chạy ngay hay hỏi trước tùy thuộc vào chế độ bạn đã chọn và vào hành động. Một cuộc trò chuyện
mới bắt đầu ở chế độ Thủ công, nơi mọi thay đổi đều hỏi trước. Ở chế độ Tự động, việc tạo và sửa tệp, các
lệnh và hành động mạng chạy mà không hỏi, còn việc xóa, ghi đè, cài đặt, commit và push Git, triển khai,
gửi tin nhắn, thanh toán và thay đổi cài đặt vẫn hỏi. Ở mọi chế độ, công cụ tệp chỉ hoạt động bên trong
[không gian làm việc](workspaces.md), công cụ tìm kiếm và tải về đọc tài nguyên công khai mà không ghi
gì, và bí mật đã lưu không bao giờ được đọc. [Quyền](permissions.md) trình bày cả năm chế độ và những gì
mỗi nút phê duyệt cho phép.

```text
tool: read_file
path: src/main.py
result: 412 lines
```

Xem thêm [Biên nhận](receipts.md).

---
description: Những mô hình AI nào hoạt động với VOOL — mô hình cục bộ trên phần cứng của chính bạn và mô hình đám mây qua tài khoản nhà cung cấp của bạn.
translation: machine
source_sha256: 1528dfca8e391868d09156c42c1dd51514770ffab17077d9260d248842c82060
---

# Mô hình được hỗ trợ

VOOL không đi kèm mô hình nào. Nó kết nối với các môi trường chạy và nhà cung cấp mà bạn chọn.

## Môi trường chạy cục bộ

Mọi môi trường chạy cung cấp một điểm cuối (endpoint) tương thích OpenAI trên localhost đều dùng được. Việc
phát hiện diễn ra tự động với các môi trường chạy phổ biến.

## Nhà cung cấp đám mây

Mô hình đám mây được truy cập bằng khóa của bạn, từ máy của bạn. Chọn một mô hình đám mây sẽ đưa ứng dụng
vào chế độ đám mây (chỉ báo hiển thị tên nhà cung cấp) — xem [Cục bộ và đám mây](../concepts/local-and-cloud.md).

[OpenRouter](../guides/openrouter.md) là kết nối đơn lẻ rộng nhất: một khóa, nhiều nhà cung cấp.

## Lựa chọn

| Công việc | Lựa chọn hợp lý |
| --- | --- |
| Tệp riêng tư, không có mạng | Cục bộ, 7B hoặc lớn hơn |
| Lập trình hằng ngày | Mô hình lập trình cục bộ 7–14B |
| Suy luận dài | Một mô hình đám mây lớn hơn |
| Kiểm soát chi phí | Cục bộ trước, đám mây khi đáng giá |

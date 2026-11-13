---
description: Những mô hình AI nào hoạt động với VOOL — mô hình cục bộ trên phần cứng của chính bạn và mô hình đám mây qua tài khoản nhà cung cấp của bạn.
translation: machine
source_sha256: 2ce84ebed0545059de772cb175bd8f088ee2d22feb60d4256ee7800f20097824
---

# Mô hình được hỗ trợ

VOOL không đi kèm mô hình nào — mô hình cục bộ đầu tiên được tải về vào lần đầu bạn dùng nó. Nó kết nối với các môi trường chạy và nhà cung cấp mà bạn chọn.

## Môi trường chạy cục bộ

Ollama, llama.cpp, vLLM và MLX được phát hiện tự động. Các môi trường chạy khác cung cấp một điểm cuối (endpoint) tương thích OpenAI trên localhost
có thể được chỉ định thủ công.

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

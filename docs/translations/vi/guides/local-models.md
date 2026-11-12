---
description: Cách chạy mô hình AI cục bộ trên chính máy tính của bạn với VOOL — cài máy chủ mô hình cục bộ, chọn kích thước mô hình và làm việc hoàn toàn ngoại tuyến.
translation: machine
source_sha256: d93b9995b0f4fec8aca12bb90d8d6f9ff0fd9623c43d4529913dc76f055ac24c
---

# Chạy mô hình cục bộ

## Cài một môi trường chạy

VOOL giao tiếp với một máy chủ mô hình cục bộ. Ứng dụng macOS có sẵn trình chạy mô hình cục bộ riêng,
nên trên Mac bạn có thể bỏ qua bước này. Trên Linux và các bản cài từ mã nguồn, hãy cài một máy chủ
như Ollama, khởi động nó và tải về một mô hình.

```bash
ollama serve
ollama pull qwen2.5-coder:7b
```

## Trỏ VOOL tới nó

Mở **Cài đặt → Mô hình và nhà cung cấp**. Các môi trường chạy cục bộ đang chạy được phát hiện tự động.
Chọn một mô hình và chỉ báo chế độ sẽ chuyển sang Cục bộ.

## Chứng nhận một lần

Một mô hình cục bộ chỉ viết câu trả lời sau khi đã được chứng nhận. Mở **Cài đặt → Mô hình và nhà cung cấp → Mô hình cục bộ**,
đăng ký mô hình, rồi chọn **Certify**. Việc chứng nhận chạy một bước kiểm tra công cụ ngắn trên máy
của bạn; không có gì được tải về và không ai được liên hệ. Cho đến khi một mô hình vượt qua bước này,
VOOL không cho nó viết câu trả lời cuối cùng.

## Chọn kích thước

| Kích thước mô hình | Bộ nhớ RAM cần | Phù hợp với |
| --- | ---: | --- |
| 3B | ~4 GB | Chỉnh sửa ngắn, câu hỏi nhanh |
| 7–8B | ~8 GB | Dùng thông thường, phần lớn việc lập trình |
| 14B | ~16 GB | Suy luận dài hơn |
| 32B+ | 32 GB trở lên | Việc nặng hơn, chậm hơn |

{% hint style="info" %}
Các bản dựng lượng tử hóa giảm đáng kể lượng bộ nhớ RAM cần dùng. Một mô hình 7B ở mức 4-bit chạy thoải
mái trong khoảng 5 GB.
{% endhint %}

## Nếu nó chạy chậm

* Chọn một mô hình nhỏ hơn hoặc được lượng tử hóa mạnh hơn.
* Đóng các ứng dụng khác dùng nhiều bộ nhớ RAM.
* Rút ngắn ngữ cảnh — ít tệp hơn trong yêu cầu nghĩa là ít việc phải xử lý hơn.

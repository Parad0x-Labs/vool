---
description: Cách chạy mô hình AI cục bộ trên chính máy tính của bạn với VOOL — cài máy chủ mô hình cục bộ, chọn kích thước mô hình và làm việc hoàn toàn ngoại tuyến.
translation: machine
source_sha256: a19d4790729d13ed5e1b134bcb67ed3e7a80609561be4227ab9480492100c8a6
---

# Chạy mô hình cục bộ

## Cài một môi trường chạy

Mặc định VOOL dùng Ollama để chạy mô hình cục bộ; các môi trường chạy cục bộ khác có điểm cuối tương thích OpenAI cũng dùng được. Ollama là một chương trình riêng, được cài riêng từ [ollama.com](https://ollama.com). Ứng dụng macOS có sẵn trình chạy mô hình cục bộ riêng,
nên trên Mac bạn có thể bỏ qua bước này. Trên Linux và các bản cài từ mã nguồn, hãy cài Ollama, khởi động nó và tải về một mô hình.

```bash
ollama serve
ollama pull qwen3:4b
```

Mặc định VOOL chọn mô hình cục bộ cho bạn — một mô hình Qwen có kích thước hợp với máy tính của bạn. Trên nhiều máy tính, mô hình đó là `qwen3:4b`; trên máy tính có từ 32 GB bộ nhớ RAM trở lên, mặc định là một mô hình Qwen lớn hơn.

## Trỏ VOOL tới nó

Mở **Cài đặt → Mô hình và nhà cung cấp**. Các môi trường chạy cục bộ đang chạy được phát hiện tự động.
Chọn một mô hình và chỉ báo chế độ sẽ chuyển sang Cục bộ.

## Chuẩn bị một lần

Một mô hình cục bộ chỉ viết câu trả lời sau khi VOOL đã kiểm tra nó. Mở **Cài đặt → Mô hình và nhà cung cấp → Mô hình cục bộ** và nhấn **Dùng mô hình này** cạnh mô hình đó. VOOL lặng lẽ kiểm tra, ngay trên máy tính của bạn, xem mô hình có dùng công cụ của VOOL đủ tốt để trả lời thay bạn hay không. Việc kiểm tra có thể mất một hai phút; không có gì được tải về và không ai được liên hệ. Khi vượt qua, mô hình hiện **Sẵn sàng** và VOOL trả lời bằng nó. Cho đến lúc đó, VOOL không cho nó viết câu trả lời cuối cùng.

* **Không phù hợp:** mô hình không dùng công cụ của VOOL đủ tốt. VOOL liệt kê những gì bạn có thể làm thay vào đó: dùng hoặc tải về mô hình mà nó đã chọn cho máy tính này, hoặc dùng một mô hình đám mây.
* **Chưa kiểm tra xong:** hãy chắc chắn Ollama đang mở, rồi nhấn **Thử lại**. Máy tính chậm có thể cần thử thêm một lần nữa.

Nếu không có mô hình cục bộ nào sẵn sàng khi VOOL khởi động, nó sẽ kiểm tra tối đa ba mô hình trên máy tính này, bắt đầu từ mô hình bạn đã chọn, và dùng mô hình đầu tiên vượt qua. Nó bỏ qua mô hình quá lớn so với máy tính này. Mô hình đã sẵn sàng luôn được ưu tiên trước mô hình chưa sẵn sàng.

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

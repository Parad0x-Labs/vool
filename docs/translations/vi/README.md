---
description: VOOL là trợ lý AI trên máy tính, chạy các mô hình AI cục bộ ngay trên máy của bạn hoặc kết nối với mô hình đám mây bằng khóa của chính bạn. Miễn phí và riêng tư. Có bản tải xuống cho Mac (Apple Silicon và Intel) và Linux; Windows ở mức thử nghiệm và cài từ mã nguồn.
translation: machine
source_sha256: c31e5a047068e09c01bbd2e2c90cbbed0b70e78926847a5b737f9e0e625ab1dd
---

# VOOL

VOOL là trợ lý AI trên máy tính. Nó được cài như một ứng dụng trên máy tính của bạn và có thể
chạy các mô hình AI cục bộ trên chính máy đó, hoặc kết nối với nhà cung cấp AI đám mây bằng khóa API
của chính bạn. Nó lưu lại bản ghi dễ đọc cho mọi thao tác nó thực hiện.

Nó miễn phí và hoạt động ngoại tuyến với các mô hình cục bộ. Có bản tải xuống cho Mac (Apple Silicon
và Intel) và Linux x86_64; Windows ở mức thử nghiệm và cài từ mã nguồn.

Tài liệu này hướng dẫn cài đặt VOOL, kết nối mô hình, và giải thích các khái niệm đằng sau cách nó
xử lý tệp, bộ nhớ và khóa của bạn.

## Kết quả so sánh

Trên 150 câu hỏi về bộ nhớ của LoCoMo chưa từng được dùng, với cùng một mô hình trả lời cho mọi tác tử và cùng
một bộ chấm GPT-4o, bộ nhớ của VOOL đã vượt hai tác tử mã nguồn mở khác:

| | VOOL | OpenClaw | Hermes |
| --- | --- | --- | --- |
| Câu trả lời đúng | **82,7%** (124 / 150) | 71,3% (107) | 62,7% (94) |
| Token mỗi câu hỏi | **9,6k** | 51,8k | 33,5k |
| Chi phí mỗi câu hỏi | **0,0033 USD** | 0,0100 USD | 0,0046 USD |

Tức là ít hơn 5,4 lần số token so với OpenClaw và ít hơn 3,5 lần so với Hermes, với chi phí mỗi câu trả lời
chỉ bằng một phần ba của OpenClaw. Bộ nhớ đi kèm VOOL 0.7 đạt 123 trên 150 với cùng bộ câu hỏi.
Mọi lần chạy, cách thiết lập và các điểm yếu đều được công bố: [điểm chuẩn bộ nhớ](https://github.com/Parad0x-Labs/vool/tree/main/docs/benchmarks).

## Bắt đầu từ đây

| Nếu bạn muốn | Hãy đọc |
| --- | --- |
| Cho VOOL chạy | [Cài đặt VOOL](getting-started/install.md) |
| Hiểu màn hình đầu tiên | [Lần chạy đầu tiên](getting-started/first-run.md) |
| Trỏ nó tới một mô hình | [Kết nối mô hình](getting-started/connect-a-model.md) |
| Kiểm soát những khoản chi có thể xảy ra | [Giới hạn chi tiêu](guides/spending-limits.md) |
| Biết những gì rời khỏi máy của bạn | [Cục bộ và đám mây](concepts/local-and-cloud.md) |

## VOOL là gì

* **Một ứng dụng máy tính.** Nó được cài trên máy tính của bạn — không phải một thẻ trình duyệt, cũng không
  phải một dịch vụ lưu trữ trên mạng.
* **Hoạt động ngoại tuyến.** Khi đã chọn một mô hình cục bộ, VOOL không cần kết nối internet.
* **Mặc định là cục bộ.** Khi đã chọn một mô hình cục bộ, lời nhắc và tệp được xử lý
  trên máy của bạn.
* **Không phụ thuộc mô hình.** Có thể chọn cả môi trường chạy cục bộ lẫn nhà cung cấp đám mây. Bạn tự cung cấp
  khóa đám mây; khóa đó nằm lại trên máy của bạn.
* **Có thể kiểm tra.** Mỗi lệnh gọi công cụ được ghi lại cùng đầu vào và kết quả, nên có thể
  đọc lại một lần chạy sau khi nó diễn ra.

{% hint style="warning" %}
VOOL đang ở giai đoạn beta cho Mac dùng Apple Silicon, Mac dùng Intel và Linux x86_64. Các ứng dụng Mac
chưa được Apple công chứng, nên macOS sẽ yêu cầu bạn xác nhận vào lần đầu mở; trang
[Cài đặt VOOL](getting-started/install.md#mở-lần-đầu-trên-mac) hướng dẫn cách làm. Hãy xem
[Trạng thái phát hành](trust/release-status.md) trước khi cài.
{% endhint %}

## Hai chế độ, nói rõ ràng

VOOL phân biệt đúng hai trạng thái, và giao diện luôn cho biết trạng thái nào đang bật:

1. **Cục bộ** — công việc ở lại trên máy tính này.
2. **Đám mây** — ngữ cảnh đã chọn được gửi thẳng tới nhà cung cấp bạn đã chọn,
   từ máy của bạn, bằng khóa của bạn.

Không có trạng thái thứ ba, và cũng không có máy chủ chuyển tiếp nào do Parad0x vận hành ở giữa. Chi tiết
có trong [Xử lý dữ liệu](trust/data-handling.md).

## Nhận trợ giúp

* [Câu hỏi thường gặp](help/faq.md) — những câu hỏi hay gặp nhất
* [Khắc phục sự cố](help/troubleshooting.md) — khi có gì đó không khởi động
* [Discord](https://discord.gg/V9NkjP3Fzz) — hỏi đội ngũ phát triển

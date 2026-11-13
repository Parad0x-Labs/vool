---
description: Ngôn ngữ, trợ năng, lịch sử hoạt động, biên nhận, và cách các câu trả lời của mô hình được kiểm chứng.
translation: machine
source_sha256: 519707395e736b24ba96005597cca3bc59dcf12ca85d905c70f8a50884c5bba2
---

# Ngôn ngữ, trợ năng và bản ghi

## Ngôn ngữ giao diện

Giao diện của VOOL có sẵn bằng nhiều ngôn ngữ; các chuỗi mới hơn có thể vẫn hiện bằng tiếng Anh cho đến khi có bản dịch. Việc chọn ngôn ngữ chỉ là chuyện trình bày: các quyết định
về quyền, thẩm quyền chi tiêu và mã lỗi **không phụ thuộc vào ngôn ngữ ngay từ cấu trúc** — một thông báo
lỗi đã dịch không bao giờ có thể nới rộng hay thu hẹp điều mà quyết định gốc bằng tiếng Anh đã định ra.

## Trợ năng

* Phím tắt cho các luồng thao tác chính (xem [Phím tắt](shortcuts.md)).
* Giao diện sáng và tối trong tài liệu và trong các màn hình của ứng dụng, theo thiết lập hệ thống của bạn
  và có thể chọn thủ công.
* Lỗi được thể hiện bằng văn bản, không chỉ bằng màu sắc; các hành động phá hủy luôn đòi hỏi một bước xác
  nhận tường minh thay vì chỉ một cú bấm nhầm.

## Hoạt động

Chế độ xem hoạt động là toàn bộ lịch sử những gì VOOL đã làm: các lệnh gọi công cụ cùng đầu vào và kết quả
của chúng, các phê duyệt bạn đã cấp hoặc từ chối, và các khoản thanh toán cùng biên nhận của chúng. Một
lượt bị một chốt chặn dừng lại sẽ hiển thị việc dừng và chốt chặn đó — một lời từ chối là một sự kiện được
ghi lại, không phải một khoảng trống.

## Biên nhận

Các hành động liên quan đến tiền và chi tiêu ghi **biên nhận**: những gì đã được đề xuất, những gì đã được
phê duyệt, những gì đã được gửi và những gì đã được thanh toán xong. Kết quả không xác định được ghi là không
xác định — không bao giờ được kể thành thành công hay thất bại. Đây là điều khiến
*"Kiểm tra trạng thái của nó trước khi thử lại"* có thể làm theo được: biên nhận cho bạn biết khoản thanh
toán thực sự đang ở trạng thái nào.

## Trạng thái kiểm chứng mô hình

VOOL phân biệt những gì mô hình **nói** với những gì runtime **chứng minh**:

* Kết quả công cụ được ghi lại cùng bản ghi thực thi của chúng; các khẳng định của một câu trả lời về tệp,
  thanh toán hoặc dữ liệu được ràng buộc với những bản ghi đó, và các khẳng định không có căn cứ sẽ bị giữ
  lại.
* Biên nhận mang một trạng thái kiểm chứng; khi một làn còn thử nghiệm hoặc không thể kiểm chứng trong bản
  dựng này, trạng thái nói rõ như vậy thay vì ngụ ý rằng đã có bằng chứng.

Xem [Biên nhận](../concepts/receipts.md) để hiểu khái niệm và [Lỗi](errors.md) để biết cách các sự cố
được phân loại.

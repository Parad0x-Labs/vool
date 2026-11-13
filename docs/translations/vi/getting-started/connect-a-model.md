---
description: Cách kết nối một mô hình AI với VOOL: mô hình cục bộ chạy trên chính máy tính của bạn, hoặc nhà cung cấp đám mây dùng khóa API của chính bạn.
translation: machine
source_sha256: 53f84267e6c9d76240039e6119dfb654fc0e2f479f3bd293ad48232d2df0dd01
---

# Kết nối mô hình

VOOL không đi kèm mô hình nào. Mặc định nó chọn một mô hình cục bộ, và bạn có thể chọn mô hình khác; lựa chọn đó quyết định một yêu cầu
ở lại trên máy của bạn hay rời khỏi nó.

## Cục bộ

Nếu một môi trường chạy cục bộ đã được cài và đang chạy, VOOL sẽ phát hiện và liệt kê các mô hình của nó.
Khi chọn một mô hình trong đó, chỉ báo sẽ hiển thị Cục bộ. Chi tiết thiết lập có trong
[Chạy mô hình cục bộ](../guides/local-models.md).

## Đám mây

Thêm khóa của nhà cung cấp trong **Cài đặt → Khóa API**. Khóa được lưu trong một tệp đã mã hóa trên máy tính
này (xem [Mô hình bảo mật](../trust/security.md#khóa)) và được dùng để gửi yêu cầu
**từ máy của bạn thẳng tới nhà cung cấp**.

Khi chọn một mô hình đám mây, chỉ báo sẽ hiển thị tên nhà cung cấp, và nó đổi trước khi
có bất kỳ yêu cầu nào được gửi.

Xem [Kết nối OpenRouter](../guides/openrouter.md) để biết quy trình riêng cho OpenRouter.

## Chuyển đổi

Đổi mô hình là đổi chế độ. Chỉ báo là căn cứ chính xác: nếu nó hiển thị Cục bộ,
yêu cầu tiếp theo sẽ được xử lý trên máy tính này.

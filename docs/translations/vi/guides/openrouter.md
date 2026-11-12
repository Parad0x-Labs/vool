---
description: Cách kết nối OpenRouter với VOOL để một khóa API dùng được nhiều mô hình AI đám mây.
translation: machine
source_sha256: 3a09fe6c636e2b8dd14a2d627c4653055dbb10bf8fd8221b7a3bcccf3044cb19
---

# Kết nối OpenRouter

OpenRouter cung cấp quyền truy cập tới nhiều nhà cung cấp chỉ qua một khóa. VOOL hỗ trợ kết nối một
tài khoản OpenRouter.

## Kết nối

Tạo một khóa trong tài khoản OpenRouter của bạn, dán nó vào **Cài đặt → Khóa API** và nhấn
**Lưu khóa**. VOOL kiểm tra khóa với OpenRouter một lần, rồi giữ nó trong một tệp đã mã hóa trên
máy tính này (xem [Mô hình bảo mật](../trust/security.md#khóa)).

## Thanh toán và ghi nhận

{% hint style="info" %}
Tài khoản OpenRouter của bạn vẫn là của bạn. VOOL gửi yêu cầu thẳng từ máy tính này bằng khóa của bạn.
Parad0x không làm trung gian cho lời nhắc của bạn và không trả tiền cho mức sử dụng của bạn. Mức sử dụng
VOOL thực tế có thể xuất hiện trong thống kê tổng hợp công khai của VOOL trên OpenRouter, nhưng từng lời
nhắc và danh tính riêng lẻ không được công bố.
{% endhint %}

## Ngắt kết nối

Xóa khóa trong **Cài đặt → Khóa API**. Hãy thu hồi cả khóa trong tài khoản OpenRouter của bạn nếu bạn muốn nó bị vô hiệu ở phía máy chủ.

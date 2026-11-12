---
description: Danh bạ — những người và điểm liên lạc đã lưu, với bước xem lại thay đổi bảo vệ mọi lần chỉnh sửa.
translation: machine
source_sha256: 8be5d771229a4d6c4b439af014f0407649ed694fd26f9fd880009d25f5b8a347
---

# Danh bạ

Danh sách liên hệ là một sổ địa chỉ cục bộ, có cấu trúc (con người, các điểm liên lạc và bí danh thanh
toán của họ) mà VOOL có thể dùng thay vì yêu cầu bạn gõ lại một địa chỉ. Vì sổ địa chỉ cung cấp dữ liệu
cho thanh toán và tin nhắn, các chỉnh sửa trên nó được **bảo vệ**: được xem lại, được xác nhận và được ghi
lại.

## Chỉnh sửa được bảo vệ

Thay đổi một liên hệ đã lưu là một quy trình hai bước:

1. **Xem lại** — thay đổi được chuẩn bị và hiển thị. Nếu mục đó đã thay đổi kể từ khi bạn mở nó (một cửa
   sổ khác, một thiết bị khác), thay đổi sẽ bị từ chối với thông báo *"đã thay đổi sau khi được xem lại;
   hãy xem lại lần nữa. Không có gì thay đổi."*
2. **Xác nhận** — thay đổi chỉ được ghi nhận khi có PIN hoặc mật khẩu của bạn trong màn hình xem lại.

Mọi lời từ chối đều nói rõ rằng **không có gì được lưu hay thay đổi** — vì thực sự không có gì cả. Nếu PIN
của bạn đã được chấp nhận nhưng bản thân việc lưu thất bại, thông báo nói đúng điều đó: PIN đã được chấp
nhận, hãy lưu lại trước khi xác nhận hết hạn.

## Cơ chế bảo vệ

* Mỗi liên hệ chỉ có một thay đổi tại một thời điểm; số thay đổi đang chờ có giới hạn.
* Xóa một liên hệ không lặng lẽ viết lại lịch sử: các tham chiếu tới một liên hệ đã xóa được báo là
  *"Liên hệ đó đã bị xóa"* — không bao giờ bị lặng lẽ phân giải lại.
* Việc nhập không bao giờ áp dụng một phần: nếu không đọc được nguồn, không có gì được nhập.

## Vì sao thiết kế như vậy

Một địa chỉ thanh toán gõ sai là tiền không lấy lại được. Bước xem lại tồn tại để không một lượt mô hình
nào, không một lần sửa hàng loạt nào và không một cửa sổ cũ nào có thể thay đổi việc "Alice" trỏ tới ai mà
không có một người xác nhận chính xác mục kết quả.

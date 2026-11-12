---
description: Năm chế độ, những gì mỗi chế độ cho VOOL làm mà không hỏi, và phạm vi của từng nút phê duyệt.
translation: machine
source_sha256: 1f33c8d63bcdeddbd1ac44d3b2d948b354137954ee6accb6ee18eeec8bdda1c6
---

# Quyền

VOOL quyết định những gì được phép chạy trước khi bất cứ thứ gì chạy. Mô hình có thể yêu cầu một hành
động; chế độ bạn đã chọn và câu trả lời của bạn quyết định nó có diễn ra hay không. Việc đọc được xử lý
khác với việc thay đổi, và bí mật, thanh toán cùng cài đặt bảo mật được bảo vệ ở mọi chế độ.

## Các chế độ

Chọn chế độ trong menu cạnh ô nhập tin nhắn. Một cuộc trò chuyện mới bắt đầu ở chế độ **Thủ công**.

| Chế độ | Chạy mà không hỏi | Hỏi trước | Không bao giờ |
| --- | --- | --- | --- |
| Thủ công | Đọc và liệt kê tệp, lệnh an toàn, và tra cứu web công khai chỉ đọc | Mọi thay đổi: sửa tệp, lệnh, hành động mạng, cài đặt, Git, triển khai và tin nhắn | Đọc bí mật đã lưu |
| Soát các chỉnh sửa | Giống Thủ công | Giống Thủ công. Mỗi đợt chỉnh sửa được hiển thị như một thay đổi có thể xem xét trước khi được áp dụng | Đọc bí mật đã lưu |
| Kế hoạch | Đọc và liệt kê tệp, và lệnh an toàn | Không có gì: nó chỉ đọc và đề xuất một kế hoạch | Mọi thay đổi, mọi hành động mạng |
| Tự động | Đọc, tạo và sửa tệp, lệnh, hành động mạng và trình duyệt, và nhà cung cấp mô hình | Xóa, ghi đè tệp hiện có, cài đặt phần phụ thuộc, thay đổi cài đặt nhà cung cấp, commit, push, merge và reset Git, triển khai, gửi tin nhắn, thanh toán, và thay đổi cài đặt | Đọc bí mật đã lưu, thay đổi cài đặt bảo mật, và mọi hành động mà VOOL không xác định được tác động |
| Bỏ qua quyền | Mọi thứ trừ bốn nhóm được bảo vệ | Thanh toán | Đọc bí mật đã lưu, thay đổi cài đặt bảo mật, và mọi hành động mà VOOL không xác định được tác động |

Một chế độ, một kỹ năng hay một plugin chỉ có thể thu hẹp những gì được đưa ra. Không thứ nào trong số đó
cấp một hành động mà chế độ không cho phép.

## Trả lời một yêu cầu cấp quyền

Khi một hành động cần đến bạn, một thanh **Cần quyền** mở ra phía trên ô nhập tin nhắn. Nó nêu chính xác
hành động: dòng lệnh sẽ chạy, hoặc các tệp sẽ thay đổi, thay đổi đó có thể hoàn tác hay không, và bản xem
trước các chỉnh sửa. Không có gì chạy cho đến khi bạn trả lời, và yêu cầu vẫn chờ bạn ngay cả khi VOOL khởi
động lại. Một yêu cầu đã hết thời gian sẽ hiện là đã hết hạn và không thể được phê duyệt.

Thanh này chỉ đưa ra những lựa chọn phù hợp với hành động:

| Nút | Những gì nó cho phép |
| --- | --- |
| Cho phép một lần | Đúng một hành động chính xác này. |
| Cho phép cho tác vụ này | Lặp lại đúng hành động chính xác đó trong tác vụ này, ở cùng chế độ. Được đưa ra cho các hành động không phải là sửa tệp. |
| Cho phép tất cả *N* thay đổi đã lập kế hoạch cho yêu cầu này | Các thay đổi tệp mà yêu cầu này đã lập kế hoạch, đúng các tệp được liệt kê dưới Xem chi tiết, và không gì khác. |
| Cho phép sửa vùng làm việc trong cuộc trò chuyện này | Đọc, tạo tệp mới và sửa bên trong không gian làm việc của cuộc trò chuyện này, trong tám giờ hoặc cho đến khi VOOL khởi động lại hoặc cuộc trò chuyện bị xóa. |
| Cho phép trong dự án này | Đọc và ghi tệp bên trong thư mục của dự án. Quyền này được giữ cho dự án, kể cả sau khi khởi động lại và trong các cuộc trò chuyện mới. |
| Xem chi tiết | Hiển thị toàn bộ yêu cầu trước khi bạn quyết định. |
| Từ chối | Hành động không chạy. |

Dù bạn cho phép gì, việc xóa, di chuyển, ghi đè, lệnh, hành động mạng, bí mật và thanh toán vẫn hỏi riêng
mỗi lần, trừ khi chính chế độ cho phép chúng.

## Bỏ qua quyền

Chế độ Bỏ qua quyền chạy các công cụ có sẵn mà không hỏi từng lần, trong một phạm vi và một khoảng thời
gian bạn chọn:

* **Phạm vi:** chỉ tác vụ hiện tại, phiên trò chuyện này, hoặc dự án này.
* **Thời lượng:** 15 hoặc 30 phút, 1, 2, 4 hoặc 8 giờ, một độ dài tùy chỉnh từ 15 đến 1440 phút, hoặc
  *Cho đến khi tôi tắt*, chỉ cho cuộc trò chuyện này.

Bỏ qua quyền có thời hạn hoạt động trong mọi cuộc trò chuyện. *Cho đến khi tôi tắt* cần cuộc trò chuyện được
gắn với một thư mục dự án, và thư mục đó không được là toàn bộ thư mục home của bạn, gốc của ổ đĩa, hay một
thư mục chứa dữ liệu riêng của VOOL. Khi bỏ qua quyền đang bật, một biểu ngữ hiển thị phạm vi và thời điểm
kết thúc, và nút **×** trên đó tắt nó ngay lập tức; cuộc trò chuyện trở về chế độ Thủ công. Xóa cuộc trò
chuyện cũng kết thúc chế độ bỏ qua quyền của nó. Bỏ qua quyền không bao giờ cấp quyền truy cập bên ngoài
không gian làm việc và không thể để một mô hình tự nới rộng quyền của chính nó. Thanh toán vẫn hỏi, còn bí
mật và cài đặt bảo mật vẫn nằm ngoài tầm với.

## Thanh toán và chi tiêu

Phê duyệt một hành động không bao giờ phê duyệt một khoản thanh toán. Thanh toán đi qua quy trình đề xuất
và phê duyệt riêng của ví, với các giới hạn của ví, ở mọi chế độ. Các lệnh gọi mô hình trả phí bị giới hạn
chi tiêu và chốt chặn giá của bạn giữ lại trước khi nhà cung cấp được liên hệ. Xem
[Ví](../guides/wallet.md) và [Giới hạn chi tiêu và chốt chặn giá](../guides/spending-limits.md).

## Khi một hành động bị từ chối

Một hành động bị từ chối không thay đổi gì, và thông báo cho biết điều gì bị từ chối và vì sao. Các mã bạn
có thể thấy là [`permission_denied`](../ERROR_BOOK.md#permission-denied),
[`confinement_refusal`](../ERROR_BOOK.md#confinement-refusal) cho một thay đổi bên ngoài thư mục được
phép, và [`tool_unavailable`](../ERROR_BOOK.md#tool-unavailable) cho một công cụ mà chế độ này không cung
cấp. Xem [Lỗi và mã lỗi](../reference/errors.md).

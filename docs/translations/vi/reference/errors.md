---
description: Cách đọc một lỗi của VOOL, liệu đã có điều gì xảy ra hay chưa, và nơi giải thích mọi mã lỗi.
translation: machine
source_sha256: ccb01d4c778254a055fd67f46241c670aac90cfc83f168b923a19cef1f949de2
---

# Lỗi và mã lỗi

Khi có gì đó thất bại, VOOL nói bằng lời lẽ dễ hiểu chuyện gì đã xảy ra, điều gì có thể đã xảy ra rồi,
và cần làm gì tiếp theo. Mỗi sự cố cũng có một mã cố định. Bạn tìm thấy mã trong phần chi tiết kỹ thuật
bên dưới một lỗi, trong dòng lệnh (`wallet_export_refused: …`), và trong các phản hồi API. Trang này
giải thích cách đọc một lỗi. [Sổ tay lỗi](../ERROR_BOOK.md) giải thích mọi mã.

## Đọc một lỗi

Hãy kiểm tra ba điều trước khi thử lại:

1. **Chuyện gì đã xảy ra.** Thông báo lỗi. Nó không bao giờ chứa đường dẫn nội bộ hay bí mật.
2. **Điều gì có thể đã xảy ra rồi.** Phần lớn các lần từ chối dừng lại trước khi có gì được gửi đi hay
   thay đổi, và thông báo nói rõ như vậy. Một số sự cố không thể biết một hành động đã hoàn tất hay chưa,
   chẳng hạn hết thời gian chờ sau khi một yêu cầu đã được gửi đi. Khi đó thông báo nói rằng kết quả không
   xác định và yêu cầu bạn kiểm tra trước khi thử lại. Một khoản thanh toán không bao giờ bị gửi hai lần
   do vô tình: lần gửi thứ hai của cùng một khoản thanh toán sẽ bị từ chối với
   [`wallet_duplicate_payment`](../ERROR_BOOK.md#wallet-duplicate-payment).
3. **Cần làm gì tiếp theo.** Một bước cụ thể.

Ý nghĩa nằm ở mã. Một trạng thái HTTP như 403 hay 429 chỉ cho biết yêu cầu đã gặp chuyện gì trên đường
đi, và hai lần từ chối khác nhau có thể có chung một trạng thái. Hãy đọc mã, không phải trạng thái.

## Mã lỗi và Sổ tay lỗi

Một mã không bao giờ bị đổi tên và không bao giờ được dùng lại cho một ý nghĩa khác.
[Sổ tay lỗi](../ERROR_BOOK.md) được tạo ra từ chính danh mục lỗi của ứng dụng, nên các mã, thông báo và
bước tiếp theo trong đó đúng y như những gì ứng dụng hiển thị. Mỗi mã có một mục riêng: hãy tìm trên trang
mã đúng như bạn nhận được, hoặc liên kết tới mục đó bằng địa chỉ của sổ tay, `#`, và mã với dấu gạch dưới
thay bằng dấu gạch ngang, chẳng hạn
[`wallet_broadcast_failed`](../ERROR_BOOK.md#wallet-broadcast-failed).

Các mã bạn có khả năng gặp nhất:

| Mã | Tiêu đề | Điều gì có thể đã xảy ra rồi |
| --- | --- | --- |
| [`permission_denied`](../ERROR_BOOK.md#permission-denied) | Bị từ chối quyền | Không có gì bị thay đổi. |
| [`provider_unavailable`](../ERROR_BOOK.md#provider-unavailable) | Không thể kết nối nhà cung cấp | Không có gì được gửi đi. |
| [`provider_exhausted`](../ERROR_BOOK.md#provider-exhausted) | Đã đạt giới hạn của nhà cung cấp | Yêu cầu đã đến nhà cung cấp. |
| [`timeout`](../ERROR_BOOK.md#timeout) | Hết thời gian chờ | Không xác định: hãy kiểm tra trước khi thử lại. |
| [`cancelled`](../ERROR_BOOK.md#cancelled) | Đã hủy | Không xác định: một phần có thể đã chạy rồi. |
| [`credential_failure`](../ERROR_BOOK.md#credential-failure) | Không thể đọc thông tin xác thực đã lưu | Không có gì được gửi đi hay thay đổi. |
| [`unknown`](../ERROR_BOOK.md#unknown) | Lỗi không mong đợi | Không xác định: hãy kiểm tra trước khi thử lại. |
| [`wallet_quote_mismatch`](../ERROR_BOOK.md#wallet-quote-mismatch) | Phê duyệt không còn khớp với bản xem trước | Không có gì được ký hay gửi đi. |
| [`wallet_broadcast_failed`](../ERROR_BOOK.md#wallet-broadcast-failed) | Không thể phát đi khoản thanh toán | Không xác định: hãy kiểm tra trạng thái của khoản thanh toán trước khi thử lại. |
| [`wallet_duplicate_payment`](../ERROR_BOOK.md#wallet-duplicate-payment) | Khoản thanh toán đã được đề xuất hoặc gửi | Không có gì mới được gửi đi; khoản thanh toán trước đó đã có trong hồ sơ. |

## Lỗi và các kết quả khác

VOOL tách biệt những trường hợp này, và cách diễn đạt của nó cũng vậy:

* **Một lỗi.** Có gì đó đã thất bại. Mã và thông báo cho biết đó là gì, và cần làm gì.
* **Đang chờ bạn.** Một yêu cầu cấp quyền hoặc một phê duyệt thanh toán đang mở. Chưa có gì chạy, và sẽ
  không có gì chạy cho đến khi bạn trả lời. Xem [Quyền](../concepts/permissions.md).
* **Bị dừng bởi giới hạn chi tiêu hoặc chốt chặn giá.** Lời từ chối xảy ra trước khi liên hệ với nhà cung
  cấp, và thông báo không bao giờ ngụ ý rằng nhà cung cấp đã được hỏi. Xem
  [Giới hạn chi tiêu và chốt chặn giá](../guides/spending-limits.md).
* **Đã hủy.** Bạn, hoặc VOOL, đã dừng nó trước khi nó hoàn tất. Một phần có thể đã chạy rồi, nên hãy kiểm
  tra những gì đã thay đổi trước khi bắt đầu lại.
* **Không thể thực hiện ở đây.** Bản dựng này hoặc máy này không làm được việc đó, và thông báo nêu ra
  những gì làm được: một đề nghị thanh toán có cơ chế mà VOOL không thể kiểm tra từ đầu đến cuối, hoặc một
  phần bổ sung tùy chọn chưa được cài đặt.
* **Kết quả không xác định.** Hành động có thể đã hoàn tất hoặc chưa, chẳng hạn một khoản thanh toán mà
  việc phát đi chưa bao giờ được xác nhận. Hãy kiểm tra trạng thái của nó trước. VOOL không bao giờ thanh
  toán lại một cách mù quáng.

## Lỗi có mã riêng

Một vài phần của VOOL giải thích sự cố của chúng bằng các mã ngắn riêng, trên màn hình nơi sự cố xảy ra,
và không có trong Sổ tay lỗi: tệp đính kèm (ví dụ một tệp quá lớn, hoặc một thông tin đăng nhập được tìm
thấy trong một tệp được dán), cập nhật ứng dụng, thư mục dự án, chọn mô hình, giới hạn chi tiêu, danh bạ,
plugin và kỹ năng, nhập bằng giọng nói, dữ liệu đã lưu và báo cáo lỗi. Thông báo của chúng cũng nói chuyện
gì đã xảy ra và cần làm gì theo cùng cách đó. Xem
[Cuộc trò chuyện và tệp đính kèm](../concepts/chats-and-attachments.md),
[Cập nhật và chuyển đổi dữ liệu](updates-migration.md) và
[Báo cáo lỗi và quyền riêng tư](../trust/bug-reporting.md).

## Một mã bạn không nhận ra

Một mã không có trong Sổ tay lỗi thì không đến từ phiên bản VOOL này. Hãy kiểm tra phiên bản nào đã hiển
thị nó, rồi tìm trong [Sổ tay lỗi](../ERROR_BOOK.md) của phiên bản đó. Nếu nó thực sự đến từ phiên bản
này, hãy báo cáo nó kèm mã đúng như được viết, và kèm mã tham chiếu (`fault-` và mười hai ký tự) khi phản
hồi có kèm theo. Một sự cố mà VOOL không thể phân loại sẽ được hiển thị là
[`unknown`](../ERROR_BOOK.md#unknown), không bao giờ là một nguyên nhân được đoán.
Xem [Báo cáo lỗi và quyền riêng tư](../trust/bug-reporting.md).

## Khắc phục các triệu chứng cụ thể

Xem [Khắc phục sự cố](../help/troubleshooting.md) cho các vấn đề về cài đặt, mô hình và nền tảng, và
[Sao lưu và khôi phục](../help/backup-and-recovery.md) để lấy lại các cuộc trò chuyện, bộ nhớ và ví của
bạn.

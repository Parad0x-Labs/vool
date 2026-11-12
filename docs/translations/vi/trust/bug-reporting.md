---
description: Báo cáo lỗi một cách an toàn — bản nháp đã được che thông tin, phê duyệt đúng từng byte, và những gì không bao giờ rời khỏi máy của bạn.
translation: machine
source_sha256: 4e64e6313c1f6a8be6784a1f35cedc87b81a5661fdb548c6821109f935db818f
---

# Báo cáo lỗi và quyền riêng tư

## Công cụ báo cáo lỗi tích hợp

Hãy dùng công cụ báo cáo lỗi trong ứng dụng (hoặc CLI `python -m apps.vool_cli bug-report`). Nó không bao
giờ tự gửi bất cứ thứ gì — quy trình được cố ý chia thành ba giai đoạn:

1. **Bản nháp** — một báo cáo được dựng cục bộ từ nhật ký và dữ liệu chẩn đoán gần đây, và được che thông
   tin ngay trong lúc dựng. *"Chưa có gì được gửi — một bản nháp cục bộ được tạo trước."*
2. **Xem trước** — bạn thấy **chính xác từng byte** sẽ được gửi, cùng mã băm của nội dung gửi và một bản
   tóm tắt che thông tin liệt kê những gì đã bị loại bỏ. Các trường bạn không muốn gửi (nhật ký, cờ, ghi
   chú tái hiện, tệp đính kèm) có thể được loại bỏ; bất kỳ chỉnh sửa nào cũng đặt lại phê duyệt.
3. **Phê duyệt** — việc gửi đòi hỏi bạn phê duyệt tường minh đúng những byte đó. Nếu nội dung gửi thay
   đổi, phê duyệt không còn ràng buộc nữa và bạn phải phê duyệt lại.

Sau khi bạn phê duyệt, **Open on GitHub** mở một trang báo cáo đã điền sẵn trên kho
[vool-reports](https://github.com/Parad0x-Labs/vool-reports) công khai trong trình duyệt của bạn. Hãy đọc
lại nó ở đó một lần nữa rồi nhấn Submit. Bản thân VOOL không gửi gì cả, và không cần token GitHub nào.
**Copy report** và **Save a local copy** hoàn toàn không cần phê duyệt: chúng đưa cho bạn cùng những byte
đã được làm sạch và không có gì rời khỏi máy của bạn. **My reports** liệt kê các bản nháp của bạn và mỗi
bản đã rời đi bằng cách nào.

## Báo cáo một câu trả lời sai

Nhấn **Báo cáo** hoặc 👎 bên dưới bất kỳ câu trả lời nào, hoặc bôi đen phần sai rồi nhấn **Báo cáo**. Báo
cáo chỉ trích dẫn phần đó (hoặc toàn bộ câu trả lời). Câu hỏi của bạn không được đưa vào trừ khi bạn đánh
dấu ô chọn. Biên nhận của câu trả lời được kèm theo: mô hình, nhà cung cấp, token, thời gian và những bước
nào đã chạy, không bao giờ kèm nội dung của các bước đó. Một câu trả lời bạn đã yêu cầu VOOL quên thì không
thể được báo cáo.

## Những gì được thêm vào để giúp điều tra

Cấu hình máy tính của bạn: phiên bản hệ điều hành, chip, bộ nhớ RAM, GPU và phiên bản máy chủ mô hình cục
bộ. Ngoài ra còn có một bước kiểm tra tình trạng ngắn: máy chủ mô hình cục bộ có phản hồi hay không, và
dung lượng ổ đĩa trống dưới dạng một khoảng. Không bao giờ có tên máy tính, tên người dùng, số sê-ri hay địa
chỉ mạng của bạn. Bạn có thể loại bỏ bất kỳ mục nào trong số đó trong bản xem trước.

## Những gì việc che thông tin loại bỏ

Một cách tất định, trước cả khi bạn thấy bản nháp: thông tin đăng nhập và tiêu đề xác thực, đường dẫn home
và tên người dùng, tên máy tính của bạn, email, địa chỉ IP, PIN và mã dùng một lần, số thẻ và số tài khoản
ngân hàng (IBAN), số điện thoại, địa chỉ ví, các thông tin cá nhân đã lưu trong hồ sơ của bạn, bí mật có
gắn nhãn và các khối dữ liệu có entropy cao — và bên trên tất cả còn có một lượt quét bí mật cuối cùng.
VOOL không nhận ra được tên hay sự việc riêng tư được gõ trong các câu bình thường, vì vậy hãy đọc bản xem trước. Dấu vết ngăn xếp (stack trace) được dựng lại từ một danh sách cho phép, không dán nguyên văn.
Bằng chứng sự cố trong báo cáo chỉ mang **các mã có kiểu xác định** — không bao giờ có ngữ cảnh nội bộ hay
chuỗi nguyên nhân.

Mỗi giá trị bị loại bỏ được thay bằng một dấu đánh dấu. Một email, một người, một thư mục home, một địa chỉ
IP, một số điện thoại, một địa chỉ ví, tên máy tính của bạn hoặc một thông tin từ hồ sơ của bạn giữ một dấu
đánh dấu có số thứ tự trong suốt báo cáo, chẳng hạn `[EMAIL_1]` và `[EMAIL_2]` cho hai địa chỉ khác nhau,
để báo cáo vẫn dễ đọc.
Mọi khóa, mật khẩu, PIN, số thẻ hay số tài khoản ngân hàng bị loại bỏ đều hiện là `[SECRET_REMOVED]`, không
bao giờ được đánh số. Danh sách liên kết một dấu đánh dấu với giá trị thật chỉ được giữ trong bộ nhớ RAM
của VOOL trong lúc bạn làm việc với báo cáo, nhờ đó một lần chỉnh sửa vẫn giữ nguyên các dấu đánh dấu. Danh
sách này không bao giờ được lưu hay gửi đi, và nó bị quên khi bạn xóa báo cáo, sau một giờ không dùng đến,
hoặc khi VOOL khởi động lại.

## Những gì VOOL không bao giờ làm

* Không có bất kỳ hình thức tự động báo cáo ra bên ngoài nào.
* Không có đường ống đo từ xa (telemetry) nào chờ sẵn sau một cài đặt.
* Các vấn đề bảo mật đi qua kênh riêng tư trong
  [chính sách bảo mật](https://github.com/Parad0x-Labs/vool/security/policy) của kho mã — không bao giờ qua
  một issue công khai.

## Tóm tắt quyền riêng tư

* Khi đã chọn một mô hình cục bộ, lời nhắc và tệp được xử lý trên máy của bạn.
* Các lệnh gọi đám mây dùng khóa API bạn cung cấp, được lưu cục bộ; khóa không bao giờ nằm trong báo cáo.
* Chi tiết đầy đủ về luồng dữ liệu có trong [Xử lý dữ liệu](../trust/data-handling.md).

---
translation: machine
source_sha256: 671f147e5d9649b6c2b7c2d391ea085120e783a8ace29f92ac8c3d5ecb4407eb
---
# Khám phá kỹ năng và kiểm tra Eyebrow

Trang Kỹ năng và Plugin có bốn thẻ: **Duyệt**, **Bộ sưu tập kỹ năng**, **Tiện ích của tôi** và **Bảo mật**. Duyệt liệt kê các kỹ năng của chính VOOL trước, gồm 53 kỹ năng VOOL chỉ có văn bản đi kèm ứng dụng, sau đó là các tiện ích đã được xem xét trong danh mục, và có thể tra cứu một kho GitHub công khai tại một bản sửa đổi (revision) được ghim. Bộ sưu tập kỹ năng liệt kê các kỹ năng của đội ngũ VOOL trước, phía trên mục biên tập viên chọn và các kỹ năng nổi bật. Tìm kiếm bỏ qua chữ hoa chữ thường và các ký tự phân cách thông dụng; các nút chọn danh mục và sắp xếp giúp thu hẹp danh sách. Nguồn, giấy phép, bản sửa đổi và yêu cầu đều hiển thị trước khi tải về.

## Lần dùng đầu tiên

1. Lưu một khóa API Eyebrow trong thẻ Bảo mật, cũng có trong Cài đặt → Khóa API. Kho thông tin đăng nhập hiện có quản lý khóa này. Việc lưu không kiểm tra kết nối hay quét bất cứ thứ gì; chỉ nút Kiểm tra kết nối mới liên hệ với Eyebrow một cách tường minh.
2. Trong Duyệt, chọn một kỹ năng và chấp thuận việc tải nội dung công khai của nó về và gửi nội dung đó tới Eyebrow. Việc này tiêu tốn hạn mức quét của dịch vụ theo gói của bạn. Không có lần thử lại tự động nào.
3. Đọc báo cáo trả về. Chỉ một phản hồi HTTP thành công không có nghĩa là kết quả sạch. Danh mục ban đầu này chỉ cho phép cài đặt khi có báo cáo `pass` đầy đủ, không có phát hiện nào và không vi phạm chính sách nào. Nút Từ chối bỏ bản tải về đang chờ mà không cài đặt nó.
4. Chấp nhận việc cài đặt như một bước riêng. VOOL giữ nguyên giấy phép, cài đặt thông qua vòng đời plugin hiện có của nó, xác minh đúng gói đã được xem xét và bật kỹ năng. Các quyền hiện có vẫn chi phối mọi hành động mà kỹ năng có thể gợi ý.

Một lần quét là đánh giá của một nhà cung cấp dịch vụ về những byte cụ thể, không phải là sự bảo đảm về độ an toàn, tính tương thích hay chất lượng câu trả lời. Việc được đưa vào danh mục không phải là chứng nhận cho nhà phát hành.

## Ranh giới

Nội dung chỉ đến từ các kho GitHub công khai tại một bản sửa đổi cố định dài 40 ký tự, với các tệp kỹ năng và tệp giấy phép được ghim bằng SHA-256. Phiên bản này không hỗ trợ kho riêng tư, không giải nén tệp lưu trữ, không có tập lệnh cài đặt và không có trình nhập plugin thực thi/MCP. Chỉ tệp SKILL.md độc lập được gửi tới Eyebrow; các cuộc trò chuyện cục bộ, tệp trong không gian làm việc, thông tin đăng nhập và nội dung giấy phép không được gửi đi để quét.

Các bản tải về vẫn bất hoạt cho đến khi được xem xét và chấp nhận. Các biên nhận được xác thực cục bộ ràng buộc báo cáo với nội dung đã ghim. Việc cài đặt từ chối các lần xem xét đã hết hạn (24 giờ), các tệp đang chờ đã bị sửa đổi và các thư mục đích đã tồn tại. Runtime kiểm tra trạng thái vòng đời và các byte đã xem xét trước khi thêm một kỹ năng được quản lý vào ngữ cảnh của mô hình. Tệp được thêm vào, liên kết tượng trưng hoặc nội dung bị thay đổi sẽ khiến kỹ năng đó không khả dụng. Phần hướng dẫn được chèn trọn vẹn hoặc được ghi lại một cách tường minh là đã bị bỏ qua vì không đủ ngân sách lời nhắc; các hướng dẫn đã quét không bị cắt bớt một cách lặng lẽ.

Các bước kiểm tra này không cách ly mã độc đã đang chạy dưới quyền người dùng hệ điều hành. Chúng không áp dụng cho các plugin đã được cài thủ công từ trước. Biên nhận quét không cấp thêm quyền thực thi mới hay quyền hạn nào đối với ví.

## API và xử lý lỗi

`GET /api/addons` trả về danh mục và trạng thái cục bộ mà không kiểm tra kết nối hay quét. `POST /api/addons` hỗ trợ `save_key`, `remove_key`, `test_key`, `scan`, `install` và `reject`. Các yêu cầu thay đổi phải là yêu cầu loopback, JSON và từ trình duyệt cùng nguồn gốc (same-origin); chỉ các trường riêng của từng thao tác được chấp nhận. URL nguồn và đường dẫn do bên gọi cung cấp đều bị từ chối.

Máy khách dùng yêu cầu nguồn kỹ năng nội tuyến của Eyebrow v1 tại `/v1/scan`, với xác thực bearer, một nguồn gốc HTTPS cố định, không chuyển hướng, kích thước phản hồi có giới hạn và không tự động thử lại. `/v1/version` được dùng cho bước kiểm tra kết nối tường minh. Tài liệu tham khảo API: https://eyebrow.cc/api/reference .

Các lỗi có kiểu xác định thường gặp gồm:

| Mã | Ý nghĩa / cách khắc phục |
| --- | --- |
| `eyebrow_key_missing` / `eyebrow_unauthorized` | Lưu hoặc thay khóa. |
| `eyebrow_quota` | Xem lại hạn mức dịch vụ trước khi thử lại. |
| `eyebrow_timeout` / `eyebrow_unreachable` | Không tự động thử lại; một lần quét bị quá thời gian vẫn có thể tiêu tốn hạn mức. |
| `eyebrow_invalid_report` / `eyebrow_no_coverage` | Bằng chứng chưa đầy đủ; không có gì được bật. |
| `addon_source_changed` / `addon_changed` | Các byte đã ghim hoặc đã xem xét khác đi; đừng kích hoạt chúng. |
| `review_untrusted` / `review_expired` | Hãy lấy một lần xem xét mới trước khi cài đặt. |
| `addon_already_installed` | Các tệp hiện có được giữ nguyên. Việc thay thế tự động chưa được triển khai. |

## Hạn chế hiện tại và kiểm chứng

Hiện không có cập nhật tự động, trình nhập danh mục tùy ý hay quy trình sửa chữa/cài lại tại chỗ cho một gói hiện có đã bị thay đổi. Việc khôi phục các lần xem xét đang chờ sau khi khởi động lại ứng dụng chưa có trong giao diện. Các chuỗi giao diện mới hiện dùng tiếng Anh làm phương án dự phòng; không khẳng định đã dịch đầy đủ.

Các bài kiểm thử tự động chạy vòng đời thực, việc nạp kỹ năng, các trình xử lý API và giao diện trình duyệt được phục vụ, với các bản tải về công khai và phản hồi của Eyebrow được giả lập (mock) một cách tường minh. Chúng chứng minh hành vi của runtime và giao diện, không chứng minh Eyebrow đang hoạt động thực tế hay năng lực của mô hình. Một lần quét thật có dùng khóa và việc chấp nhận trên ứng dụng đóng gói gốc vẫn là các bước kiểm tra khi phát hành. Không cần khóa hay lệnh gọi dịch vụ trả phí nào để chạy các bài kiểm thử này.

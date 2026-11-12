---
description: Không gian làm việc là thư mục dự án mà VOOL làm việc trong đó. Bên ngoài nó, VOOL có thể đọc các thư mục hiển thị trong thư mục home của bạn và chỉ thay đổi tệp trong Màn hình nền, Tải về và Tài liệu.
translation: machine
source_sha256: 33b648a0a55f99b1c96365553942911862672eafa55e94bd45f18ce566d5fcea
---

# Không gian làm việc

Không gian làm việc là thư mục dự án mà một cuộc trò chuyện làm việc trong đó. VOOL đọc và thay đổi tệp
ở đó, và hỏi trước mỗi khi chế độ hoạt động của bạn yêu cầu (xem [Công cụ](tools.md)).

## Thêm và gỡ bỏ

Chọn một thư mục bằng **Chọn một thư mục dự án…** trong cuộc trò chuyện, hoặc bằng **+ Dự án** ở thanh bên
trò chuyện. Xóa một dự án khiến VOOL quên những gì nó đã học trong dự án đó và chuyển các cuộc trò chuyện
của dự án về lại Chung. Các tệp của bạn không bị động đến.

## Các thư mục khác của bạn

Bên ngoài không gian làm việc, công cụ tệp của VOOL cũng có thể truy cập thư mục home của bạn, với các
giới hạn cố định:

| Thư mục | VOOL có thể làm gì |
| --- | --- |
| Màn hình nền, Tải về, Tài liệu | Đọc, và thay đổi tệp, hỏi trước theo chế độ hoạt động của bạn |
| Hình ảnh, Phim (trên Linux là Video), Nhạc | Chỉ đọc: liệt kê, tìm, mở và đo kích thước, không bao giờ thay đổi |
| Các thư mục hiển thị khác trong thư mục home của bạn, chẳng hạn `~/Projects` | Chỉ đọc |
| Thư mục ẩn (chẳng hạn `~/.ssh`), dữ liệu ứng dụng và hệ thống (`Library`, `Applications`, `AppData`), thư mục dữ liệu riêng của VOOL | Không bao giờ |

VOOL chỉ tìm trong các thư mục này khi một yêu cầu cần đến, ví dụ "tìm tệp PDF thuế của tôi" hoặc
"video nào trong Phim lớn nhất". Nó không lập chỉ mục ổ đĩa của bạn trong nền.

## Lời khuyên về phạm vi

* Hãy trỏ không gian làm việc vào một dự án, không phải vào thư mục home của bạn.
* Hãy giữ các tệp thông tin đăng nhập, dữ liệu khóa và kho mật khẩu trong trình quản lý mật khẩu hoặc
  trong một thư mục ẩn, đừng để rải rác trong Màn hình nền, Tải về, Tài liệu hay một thư mục dự án.
* Hãy dùng một dự án riêng cho mỗi phần việc để ngữ cảnh của việc này không lọt sang việc khác.

{% hint style="warning" %}
Bất cứ thứ gì VOOL đọc, trong thư mục dự án hay trong các thư mục ở thư mục home của bạn, đều có thể được
đưa vào những gì gửi tới nhà cung cấp của bạn khi một mô hình đám mây được chọn. Hãy giới hạn phạm vi một
cách có chủ ý.
{% endhint %}

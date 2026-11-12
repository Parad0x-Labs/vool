---
description: VOOL lưu cấu hình của nó ở đâu và các cài đặt có ý nghĩa gì.
translation: machine
source_sha256: 3bbb23b29003bdd8c973c2d88a34faf974f6436405b7c90da4d0307c0962f887
---

# Cấu hình

## Vị trí

VOOL giữ cài đặt, cuộc trò chuyện, bộ nhớ và bản ghi của nó trong một thư mục runtime home duy nhất:

| Bản cài đặt | Runtime home |
| --- | --- |
| Ứng dụng macOS | `~/Library/Application Support/VOOL/runtime` |
| Gói tar Linux và bản cài từ mã nguồn | `~/.vool_runtime` |
| Windows (thử nghiệm, cài từ mã nguồn) | `%LOCALAPPDATA%\VOOL` |

Đặt `VOOL_HOME` trước khi khởi động VOOL sẽ khiến VOOL dùng thư mục đó thay thế. Nhật ký khởi động in ra
thư mục home đang được dùng.

Khóa **không** được ghi vào tệp cài đặt hay nhật ký. Chúng được giữ ở dạng mã hóa trong một tệp thông tin
đăng nhập riêng trong runtime home này, chỉ tài khoản người dùng của bạn đọc được; các khóa mà VOOL đã giữ
trong Chuỗi khóa (Keychain) của macOS trên máy Mac thì vẫn ở đó. Xem
[Mô hình bảo mật](../trust/security.md#khóa).

## Cài đặt

Thay đổi cài đặt trong ứng dụng ở mục **Cài đặt**. Những cài đặt quyết định VOOL được làm gì:

* **Mô hình** — mô hình đang dùng, quyết định một lượt là CỤC BỘ hay ĐÁM MÂY.
* **Không gian làm việc** — thư mục dự án mà một cuộc trò chuyện làm việc trong đó; xem
  [Không gian làm việc](../concepts/workspaces.md) để biết các thư mục khác mà VOOL có thể truy cập.
* **Chế độ hoạt động** — được chọn trong menu cạnh ô nhập tin nhắn, không phải ở đây. Thủ công hỏi trước
  mọi thay đổi; Tự động cho các chỉnh sửa và lệnh thường ngày chạy, còn việc xóa, ghi đè, cài đặt, chi
  tiêu và thay đổi cài đặt vẫn hỏi. Xem [Quyền](../concepts/permissions.md).
* **Truy cập web và tiền điện tử** — cả hai đều tắt cho đến khi bạn bật chúng.

VOOL không có cài đặt đo từ xa (telemetry) cho sản phẩm vì nó không gửi dữ liệu đo từ xa nào của sản phẩm.

## Đặt lại

Thoát VOOL và di chuyển runtime home sang chỗ khác. Các tệp trong không gian làm việc không bị động đến;
VOOL khởi động lại với một hồ sơ mới. Các khóa đã lưu của bạn cũng bị chuyển đi theo, nên hãy thêm lại
chúng trong **Cài đặt → Khóa API**. Các khóa được giữ trong Chuỗi khóa của macOS vẫn ở đó cho đến khi bạn
xóa chúng trong Truy cập Chuỗi khóa (Keychain Access).

---
description: Cách VOOL bảo vệ tệp và khóa API của bạn — ranh giới không gian làm việc, nơi lưu thông tin đăng nhập, và những gì một plugin có thể truy cập.
translation: machine
source_sha256: 4397ca0e7b494efacb90aa546eae10800b9a58558ba7c611c0131f4410c2fe3a
---

# Mô hình bảo mật

## Ranh giới

* **Giới hạn thư mục** khoanh vùng quyền truy cập tệp. VOOL đọc và thay đổi tệp trong thư mục dự án của cuộc
  trò chuyện. Bên ngoài thư mục đó, VOOL có thể đọc các thư mục hiển thị trong thư mục home của bạn và chỉ
  thay đổi tệp trong Màn hình nền, Tải về và Tài liệu; thư mục ẩn, dữ liệu ứng dụng và hệ thống, và dữ liệu
  riêng của VOOL không bao giờ được đọc. Xem [Không gian làm việc](../concepts/workspaces.md).
* **Kho thông tin đăng nhập** giữ các khóa. Khóa không được ghi vào tệp cấu hình hay nhật ký.
* **Xác nhận công cụ** đặt giới hạn cho các thay đổi. Ở chế độ Thủ công, mọi thay đổi đều hỏi trước; ở chế độ Tự
  động, các chỉnh sửa và lệnh thường ngày có thể chạy tự động, còn việc xóa, ghi đè, cài đặt, chi tiêu và
  thay đổi cài đặt vẫn hỏi. Xem [Công cụ](../concepts/tools.md).

## Mạng cục bộ của bạn

Theo mặc định, VOOL chỉ lắng nghe trên máy tính này. Khi bạn đặt nó lắng nghe trên mạng cục bộ của bạn,
chỉ máy tính này được coi là bạn. Cài đặt, tệp, khóa, công cụ và các tính năng riêng tư chỉ phản hồi máy
tính này.

Một thiết bị khác trong mạng chỉ nhận được trò chuyện thông thường. Nó cũng có thể đọc một trang web công
khai mỗi lần. Trang đó phải nằm trên internet công cộng: các địa chỉ trên máy tính này hoặc trong mạng cục
bộ của bạn đều bị từ chối, và mỗi lần chuyển hướng đều được kiểm tra theo cùng cách. Tìm kiếm web, nghiên
cứu, trình duyệt, báo giá, thanh toán và các khóa trả phí của bạn chỉ hoạt động trên máy tính này.

## Cầu nối Discord

Cầu nối Discord chỉ trả lời chủ sở hữu của nó. Người dùng Discord đầu tiên gửi cho nó một lệnh sẽ trở
thành chủ sở hữu. Để tự chọn chủ sở hữu, hãy đặt `VOOL_DISCORD_OWNER_USER_ID` thành ID người dùng Discord
của bạn; hãy làm vậy với bất kỳ kênh nào mà người khác có thể viết vào. Những người khác sẽ được thông báo
một lần rằng VOOL này đã bị khóa cho chủ sở hữu của nó, và tin nhắn của họ không được xử lý. Ngay cả tin
nhắn Discord của chủ sở hữu cũng chịu cùng các giới hạn như một thiết bị khác trong mạng.

## Khóa

VOOL giữ các khóa nhà cung cấp của bạn trong một tệp đã mã hóa trong runtime home của nó trên máy tính này
(xem [Cấu hình](../reference/configuration.md)), chỉ tài khoản người dùng của bạn đọc được. Các khóa mà
VOOL đã giữ trong Chuỗi khóa (Keychain) của macOS trên máy Mac vẫn đọc được ở đó. Một khóa được dùng từ
máy của bạn để kết nối tới nhà cung cấp của bạn. Nó không được gửi tới Parad0x Labs, và không có điểm cuối
nào do Parad0x vận hành trên đường đi của yêu cầu.

{% hint style="info" %}
Tệp đã mã hóa được bảo vệ bằng quyền truy cập tệp của tài khoản người dùng của bạn, không phải bằng một
mật khẩu riêng. Hãy luôn bật mã hóa ổ đĩa (FileVault trên Mac) để một ổ đĩa bị mất hoặc bị đánh cắp không
làm lộ khóa của bạn.
{% endhint %}

## Plugin

Một plugin chạy với quyền truy cập của runtime. Đó là điều làm cho plugin hữu ích và cũng là lý do nên
đọc chúng trước khi cài. Xem [Kỹ năng và plugin](../guides/skills-and-plugins.md).

## Báo cáo sự cố

Hãy báo cáo các vấn đề bảo mật một cách riêng tư qua
[GitHub Security Advisories](https://github.com/Parad0x-Labs/vool/security/advisories/new),
không bao giờ trong một issue công khai. Hãy kèm theo phiên bản, nền tảng và các bước để tái hiện.

{% hint style="warning" %}
Các ứng dụng macOS chỉ được ký ad-hoc và chưa được Apple công chứng; gói tar cho Linux không được ký. Xem
[Trạng thái phát hành](release-status.md).
{% endhint %}

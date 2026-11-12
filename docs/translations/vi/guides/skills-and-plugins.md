---
description: Mở rộng VOOL bằng kỹ năng và plugin.
translation: machine
source_sha256: a86860b8624e3c97c81e813ec92e3998c687a87d7bc43b7960c489cdcd309196
---

# Kỹ năng và plugin

Một **kỹ năng** là một bộ hướng dẫn được đóng gói cho một công việc lặp lại. Một **plugin** thêm công cụ
mới vào runtime.

## Đi kèm so với cài từ bên ngoài

VOOL đi kèm một **thư viện kỹ năng gốc** — các kỹ năng có sẵn cùng chính ứng dụng — và, trong các bản
dựng hiện tại, plugin **VOOL Database**. Bạn cũng có thể tự cài kỹ năng và plugin của riêng mình.

| | Đi kèm (phát hành cùng VOOL) | Cài từ bên ngoài |
| --- | --- | --- |
| Nằm ở đâu | Bên trong ứng dụng | Thư mục plugin của bạn (đặt trong Cài đặt) |
| Cập nhật | Cùng với ứng dụng | Khi bạn cập nhật gói |
| Có thể tắt | Có — từng mục riêng | Có — từng mục riêng |
| Cấp quyền | **Không** | **Không** |

Hai quy tắc áp dụng cho cả hai nguồn:

* **Kỹ năng dạy; chúng không bao giờ cấp quyền.** Các công cụ mà một kỹ năng khai báo là một tín hiệu
  *thu hẹp* (ngân sách công cụ của quy trình làm việc), không bao giờ là việc cấp bất cứ thứ gì. Bộ kiểm
  soát quyền không đọc kỹ năng.
* **Một danh mục duy nhất.** Danh mục mà bảng điều khiển hiển thị và các gói mà runtime nạp là cùng một
  danh sách. Một kỹ năng xuất hiện trong danh sách không chứng minh rằng nó nạp được — mỗi mục mang trạng
  thái thực tế của nó (khả dụng, đã tắt, thiếu điều kiện tiên quyết, hợp đồng không hợp lệ) cùng lý do.

## Tìm kỹ năng

Trang Kỹ năng và Plugin liệt kê các kỹ năng của chính VOOL trước. **Duyệt** có sẵn 53 kỹ năng VOOL dùng được
ngay. Chúng chỉ gồm văn bản: mỗi kỹ năng là một bộ hướng dẫn, đi kèm ứng dụng, nên Duyệt liệt kê chúng
mà không cần tra cứu trực tuyến. Các kỹ năng VOOL dùng công cụ của VOOL được liệt kê trong thẻ
**Bộ sưu tập kỹ năng**, nơi các kỹ năng của đội ngũ VOOL đứng đầu. Hãy xem xét một kỹ năng trước khi bật
nó.

## Trạng thái khả dụng

Một kỹ năng có thể hiện là không khả dụng kèm một lý do có kiểu xác định: thiếu điều kiện tiên quyết (ví
dụ, kỹ năng cơ sở dữ liệu cần plugin cơ sở dữ liệu được bật), một khả năng không có trên máy này, một hợp
đồng không hợp lệ, hoặc bị bạn tắt. Lý do được hiển thị cho từng mục, và việc tắt áp dụng cho từng mục và
vẫn giữ nguyên sau khi khởi động lại.

## Danh tính trùng lặp

Nếu hai gói khai báo cùng một danh tính plugin (chẳng hạn một gói đi kèm và một bản sao đã cài), gói đi
kèm được giữ lại **một cách tất định** và bản sao bị loại được báo cáo trong danh mục — không bao giờ bị
che khuất một cách lặng lẽ.

## Tự cài đặt

Cài từ danh mục trong ứng dụng, hoặc trỏ VOOL tới một thư mục plugin của riêng bạn. Các gói mang một tệp
kê khai (`plugin.json`) khai báo các công cụ của chúng; một tệp kê khai có phần khai báo quyền không khớp
với lớp hành vi đã khai báo sẽ bị từ chối khi nạp.

{% hint style="danger" %}
Hãy xem xét toàn bộ gói trước khi xác minh. Một lần xem xét là bằng chứng về những byte cụ thể, không
phải giấy chứng nhận an toàn hay sự cấp quyền.
{% endhint %}

## Phê duyệt gói trong v0.7

Việc xác minh bao gồm mọi tệp và thư mục của gói, kể cả tệp thực thi của trình xử lý, thư viện được đóng
kèm (vendored), tệp khóa phụ thuộc, tài liệu tham chiếu, tài nguyên và bit chế độ thực thi. Thêm, xóa,
đổi tên hoặc thay đổi bất kỳ thứ nào trong số này sẽ làm mất hiệu lực phê duyệt. Liên kết tượng trưng
(symlink), liên kết cứng (hardlink), tệp đặc biệt, chế độ đặc quyền, lần đọc không ổn định và bản phân
phối quá cỡ đều bị từ chối. Giới hạn thu nhận hiện tại là 4.096 mục, 64 MiB mỗi tệp và 128 MiB mỗi gói.
Không có thư mục bộ nhớ đệm hay thư mục trạng thái nào được bỏ qua bên trong một gói.

Các phê duyệt chỉ dựa trên siêu dữ liệu đã lưu trước đây không kích hoạt gói trong phiên bản này. Hãy xem
xét toàn bộ bản phân phối, xác minh lại một cách tường minh, rồi bật nó. Các bản cập nhật vẫn bị tắt cho
đến khi được xác minh và bật; phê duyệt không bao giờ được lặng lẽ chuyển sang. Các tiện ích được quản lý
cũng cần một lần xem xét mới, có xác thực, từ cửa hàng khi một biên nhận cũ hơn không ràng buộc toàn bộ
bản phân phối; việc xác minh thông thường không thể thay thế biên nhận đó.

`requires` nêu tên các plugin phụ thuộc đã cài. Việc xác minh ghim các mã băm phê duyệt đệ quy của chúng;
mỗi phần phụ thuộc phải đã được xác minh và bật. Một phần phụ thuộc bị thay đổi, cập nhật, tắt hoặc thu
hồi sẽ khiến các gói phụ thuộc vào nó trở nên không khả dụng, kể cả qua các công cụ đã lưu đệm. Bước tiếp
nhận kiểm tra lại trạng thái bật và thu hồi cho toàn bộ bao đóng đã thu nhận sau khi băm. Xác minh lại một
phần phụ thuộc không lặng lẽ phê duyệt các byte mới của nó cho gói cha: hãy xem xét và xác minh lại mọi
gói cha nữa. Mỗi `approval_digest` ràng buộc mã băm gói của nó và các phê duyệt phần phụ thuộc, nên một
phần phụ thuộc bắc cầu bị thay đổi sẽ làm thay đổi phê duyệt đó ngay cả khi các tệp riêng của gói cha vẫn
giữ nguyên. Phê duyệt phụ thuộc vòng tròn không thể kích hoạt. Bao đóng phụ thuộc được giới hạn ở 64 gói.

Mỗi lần gọi đã đăng ký đều kiểm tra lại trạng thái vòng đời và thu nhận các bản sao chỉ đọc riêng của gói
và bao đóng phụ thuộc đã ghim của nó. Hợp đồng công cụ phải khớp với tệp kê khai đã thu nhận. Trình xử lý
đã đăng ký khởi chạy từ bản thu nhận đó; sửa mã nguồn sau khi thu nhận không thể thay thế trình xử lý đó
hay các tệp đi kèm của nó. Các quan sát thực thi bao gồm mã băm của gói, của phê duyệt đệ quy và của các
phần phụ thuộc. Các gói đã đăng ký đòi hỏi cách ly thực sự bằng nhân hệ điều hành (kernel) ngay cả khi
một bên gọi cấp thấp cục bộ đã chọn một thiết lập ghi đè yếu hơn. Các máy chủ (host) không có khả năng thu nhận
bộ mô tả an toàn kiểu no-follow hoặc cách ly bằng nhân hệ điều hành sẽ từ chối các gói đã đăng ký. Thời
gian chờ của lần gọi được giới hạn trong khoảng 1–300 giây; nhóm tiến trình thuộc về lần gọi đó bị dừng
trước khi bản sao thực thi của nó bị xóa.

Hãy giữ đầu ra lâu dài, bộ nhớ đệm và nhật ký trong `PLUGIN_SCRATCH`, không đặt cạnh mã thực thi. Các công
cụ chỉ đọc không nhận quyền ghi. Các công cụ khác có thể ghi vào vùng scratch nhưng không thể sửa đổi bản
sao thực thi của chúng. `VOOL_PLUGIN_DEPENDENCIES` là một ánh xạ JSON từ danh tính phần phụ thuộc đã khai
báo tới các thư mục phụ thuộc đã thu nhận. Hãy đóng gói các thư viện bên thứ ba khác cùng tệp khóa của
chúng bên trong gói; chỉ riêng một tệp khóa không chứng thực một môi trường bên ngoài. Trình thông dịch của máy chủ (host) và
các thư viện hệ thống vẫn là một phần của nền tảng được tin cậy, thay vì được coi là các byte của
gói bên thứ ba đã được xem xét. Bước kiểm tra tính toàn vẹn này không chứng nhận hành vi, không chứng thực
mã tùy ý được tải về hoặc nạp từ bên ngoài bản phân phối đã phê duyệt, không làm trung gian cho mọi tác
động bên trong và không thay thế bước phê duyệt gốc của ví.

## Viết một kỹ năng

Một kỹ năng là một thư mục chứa một tệp hướng dẫn và mọi tệp mà nó tham chiếu tới:

```text
my-skill/
├── SKILL.md
└── templates/
```

Soạn bản nháp trong ứng dụng (`skill.create` chuẩn bị nó mà không kích hoạt gì), xác thực nó, rồi cài
đặt để kích hoạt — ba bước tách biệt có chủ ý, để việc soạn không bao giờ là một thay đổi hành vi ngầm.
Danh mục và định dạng soạn kỹ năng vẫn đang được hoàn thiện cho bản phát hành công khai.

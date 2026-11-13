---
description: Mở rộng VOOL bằng kỹ năng và plugin.
translation: machine
source_sha256: 4dd2a317a7b10ee024616f6e0c27bd73087c44334f29e2c5f60f69773f4e54e8
---

# Kỹ năng và plugin

Một **kỹ năng** là một bộ hướng dẫn được đóng gói cho một công việc lặp lại. Một **plugin** thêm công cụ,
một nhà cung cấp mô hình hoặc một trình quét tiện ích. Plugin cũng có thể đi kèm kỹ năng.

## Đi kèm so với cài từ bên ngoài

VOOL đi kèm một **thư viện kỹ năng gốc** — các kỹ năng có sẵn cùng chính ứng dụng — và, trong các bản
dựng hiện tại, plugin **VOOL Database**. Bạn cũng có thể tự cài kỹ năng và plugin của riêng mình.

| | Đi kèm (phát hành cùng VOOL) | Cài từ bên ngoài |
| --- | --- | --- |
| Nằm ở đâu | Bên trong ứng dụng | Thư mục plugin của bạn (quản lý từ cửa sổ plugin trong VOOL, không phải từ Cài đặt) |
| Cập nhật | Cùng với ứng dụng | Khi bạn cập nhật gói |
| Có thể tắt | Chưa có nút nào trong các cửa sổ của ứng dụng | Có — từ cửa sổ plugin |
| Cấp quyền | **Không** | **Không** |

Hai quy tắc áp dụng cho cả hai nguồn:

* **Kỹ năng dạy; chúng không bao giờ cấp quyền.** Các công cụ mà một kỹ năng khai báo là một tín hiệu
  *thu hẹp* (ngân sách công cụ của quy trình làm việc), không bao giờ là việc cấp bất cứ thứ gì. Bộ kiểm
  soát quyền không đọc kỹ năng.
* **Một danh mục duy nhất.** Danh mục mà bảng điều khiển hiển thị và các gói mà runtime nạp là cùng một
  danh sách. Một kỹ năng xuất hiện trong danh sách không chứng minh rằng nó nạp được — mỗi mục mang trạng
  thái thực tế của nó (khả dụng, đã tắt, thiếu điều kiện tiên quyết, hợp đồng không hợp lệ) cùng lý do.

## Tìm kỹ năng

Trang Kỹ năng và Plugin liệt kê các kỹ năng của chính VOOL trước. **Duyệt** có sẵn 75 kỹ năng VOOL dùng được ngay. Mỗi kỹ năng là một bộ hướng dẫn, đi kèm ứng dụng, nên Duyệt liệt kê chúng mà không cần tra cứu trực tuyến. 22 kỹ năng trong số đó dùng công cụ của VOOL (email, ghi chú, tệp, PDF, ví của bạn và nhiều thứ khác) ở những lượt chúng được chọn: công cụ của một kỹ năng chỉ thu hẹp những gì VOOL vốn đã cung cấp cho yêu cầu của bạn, một công cụ đang tắt vẫn tắt, và mọi lệnh gọi vẫn hỏi như mọi khi. Mục **Nguồn và chi tiết kỹ thuật** của kỹ năng liệt kê các công cụ nó dùng. Thẻ **Bộ sưu tập kỹ năng** liệt kê các kỹ năng của đội ngũ VOOL trước. Hãy xem xét một kỹ năng trước khi bật nó.

Cách thêm một kỹ năng từ Duyệt tùy thuộc vào ai đã làm ra nó:

* **Kỹ năng do VOOL làm** (75 kỹ năng ở trên): nhấn **Thêm kỹ năng này**. VOOL kiểm tra trên máy tính của bạn rằng tệp đúng là tệp đã đi kèm ứng dụng và đọc nó để tìm các chỉ dẫn rủi ro, rồi cho bạn xem kết quả. Không cần khóa và không có gì được gửi đi đâu cả. Nếu tệp không khớp, VOOL từ chối nó và cho bạn biết.
* **Kỹ năng từ người làm khác** (nhà phát hành khác, hoặc một liên kết GitHub bạn dán vào): nhấn **Tải về và quét**. VOOL tải kỹ năng công khai về ở một phiên bản cố định và kiểm tra nó trên máy tính của bạn. Nếu bạn đã bật một plugin quét, plugin đó cũng nhận nội dung kỹ năng và báo cáo của nó được thêm bên cạnh báo cáo của chính VOOL. Đọc kết quả, rồi chọn có thêm kỹ năng hay không: nếu một trong hai bước kiểm tra báo có vấn đề, VOOL chỉ thêm nó sau khi bạn chấp nhận rủi ro cho phiên bản đó. Không cần khóa, trừ khi plugin quét của bạn yêu cầu.

Bạn cũng có thể tự làm kỹ năng của mình ngay trong trò chuyện mà không cần khóa nào: xem [Tự làm kỹ năng của bạn](../../../guides/make-your-own-skill.md).

Bạn cũng có thể hỏi trong bất kỳ cuộc trò chuyện nào: *tôi có những kỹ năng nào?* sẽ liệt kê chúng, và *làm sao để thêm một kỹ năng?* sẽ giải thích cả hai cách.

## Bật hoặc tắt một kỹ năng

Mở **Kỹ năng và Plugin**, rồi **Tiện ích của tôi**, rồi **Kỹ năng**. Mỗi kỹ năng hiển thị **Bật** hoặc **Tắt** cùng một nút **Bật** hoặc **Tắt**. Một kỹ năng bạn đã tắt sẽ không được dùng trong bất kỳ cuộc trò chuyện nào cho đến khi bạn bật lại. Các tệp của nó vẫn được giữ.

Thẻ **Built-in skills** trong **Plugin** không có nút riêng: hãy bật hoặc tắt từng kỹ năng của nó trong **Kỹ năng**.

## Xem kỹ năng nào đã định hình câu trả lời

Khi các chỉ dẫn của một kỹ năng là một phần trong cách VOOL viết câu trả lời, một dòng ngắn bên dưới câu trả lời sẽ cho biết điều đó, ví dụ **Skill đã dùng: Browser**. Dòng này xuất hiện ngay sau câu trả lời, trong khi cuộc trò chuyện đang mở; các câu trả lời được nạp lại sau đó từ lịch sử chưa hiển thị nó. Các quy tắc về phong cách trả lời đi kèm hầu hết các câu trả lời không được nêu tên ở đó.

## Kỹ năng để xây dựng VOOL

Thư viện cũng chứa các kỹ năng mà đội ngũ VOOL dùng để xây dựng VOOL: rà soát mã, chạy kiểm thử, kiểm tra phát hành, sửa pipeline và những việc tương tự. Chúng không được liệt kê trong trò chuyện hay trên trang Kỹ năng, và chúng không định hướng các câu trả lời hằng ngày. Nhà phát triển có thể hiển thị và dùng chúng bằng cách khởi động VOOL với `VOOL_DEVELOPER_SKILLS=1`.

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

## Plugin trong Tiện ích

Mở **Tiện ích → Tiện ích của tôi → Plugin**. Xem xét một thư mục cục bộ hoặc một thư mục GitHub công khai tại đúng một commit. Việc cài đặt thu nhận toàn bộ gói và giữ nó ở trạng thái tắt. Các plugin đã ký từ cửa hàng dùng chuỗi registry hiện có và cũng được cài ở trạng thái tắt. Hãy xem xét bảng quyền truy cập riêng trước khi bật công cụ hoặc dịch vụ. Lưu khóa dịch vụ không tạo ra yêu cầu nào; kiểm tra kết nối và làm mới danh sách mô hình là những thao tác tường minh.

Tắt sẽ gỡ các công cụ và dừng các lệnh gọi đang chạy. Gỡ cài đặt sẽ tắt trước, xóa các tệp và vùng scratch thuộc về plugin, mặc định xóa các khóa đã lưu và giữ lại lịch sử. Bản cập nhật và cài lại cần một lần xem xét mới và bắt đầu ở trạng thái tắt. Một bản cập nhật từ kho hoặc nhà phát hành khác không giữ các khóa đã lưu. UsePod và Eyebrow không còn là một phần của VOOL, và VOOL không cung cấp plugin cho cả hai. Một khóa UsePod hoặc Eyebrow đã lưu vẫn nằm trên máy tính này và VOOL không dùng nó; Tiện ích sẽ thông báo điều này cho đến khi bạn đóng thông báo. Các cuộc trò chuyện từng dùng một mô hình UsePod sẽ không có mô hình nào được chọn sau khi nâng cấp, và Tự động sẽ không chuyển sang mô hình đám mây trong các cuộc trò chuyện đó; một thông báo sẽ cho biết điều này cho đến khi bạn đóng nó.

Xem [Viết plugin](../../../guides/plugin-authoring.md) và [Kiểm tra tiện ích](addon-security.md).

## Phê duyệt toàn bộ gói


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
chúng bên trong gói; chỉ riêng một tệp khóa không chứng thực một môi trường bên ngoài. Trình thông dịch của runtime và
các thư viện hệ thống vẫn là một phần của nền tảng được tin cậy, thay vì được coi là các byte của
gói bên thứ ba đã được xem xét. Bước kiểm tra tính toàn vẹn này không chứng nhận hành vi, không chứng thực
mã tùy ý được tải về hoặc nạp từ bên ngoài bản phân phối đã phê duyệt, không làm trung gian cho mọi tác
động bên trong và không thay thế bước phê duyệt gốc của ví.

Trên macOS, mã plugin chạy trong sandbox của macOS. Khóa chỉ đến được trình xử lý qua đầu vào riêng của nó, không bao giờ qua môi trường của nó, vì trên Mac bất kỳ chương trình nào của cùng người dùng, kể cả mã plugin, đều có thể đọc môi trường khởi động của chương trình khác. Một máy chủ kết nối cần khóa trong môi trường của nó chỉ chạy trong môi trường chạy Linux cách ly tùy chọn, do bạn tự chuẩn bị; VOOL không bao giờ khởi động một máy ảo (VM) hay tải về một ảnh hệ thống. Nếu không có môi trường đó, plugin ấy vẫn tắt và cho biết lý do. Linux dùng bwrap, bao gồm một không gian tên tiến trình riêng và `/proc`.

Trên macOS, một công cụ khai báo mạng chỉ có thể kết nối tới một proxy riêng mà VOOL khởi động cho mỗi lệnh gọi, và proxy đó chỉ kết nối tới các máy chủ đã khai báo. Trên Linux và trong môi trường chạy cách ly, các tiến trình plugin không có mạng. Các yêu cầu của nhà cung cấp và trình quét gốc dùng lớp truyền tải HTTPS riêng tới máy chủ đã khai báo trên mọi nền tảng.

## Viết một kỹ năng

* Để làm một kỹ năng bằng cách yêu cầu trong trò chuyện, xem [Tự làm kỹ năng của bạn](../../../guides/make-your-own-skill.md).
* Để tự viết, lint, cài đặt và kiểm thử một tệp SKILL.md, xem [Xây dựng và kiểm thử một kỹ năng](../../../guides/build-and-test-a-skill.md).

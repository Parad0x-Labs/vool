---
translation: machine
source_sha256: 4596ad3e82d6936445beafa8e30a334325d70a8a0f1ee052eda3c43ae5118244
---
# Kiểm tra tiện ích

Tiện ích giữ việc duyệt, kiểm tra và cài đặt tách biệt nhau. Một lần quét mô tả những byte cụ thể; đó không phải là bảo đảm an toàn, sự chứng thực cho nhà phát hành, quyền khi chạy hay sự phê duyệt của ví.

Duyệt liệt kê các kỹ năng của chính VOOL trước, gồm 75 kỹ năng VOOL đi kèm ứng dụng (22 kỹ năng trong số đó dùng công cụ của VOOL, và các công cụ này chỉ thu hẹp những gì VOOL vốn đã cung cấp), sau đó là các tiện ích đã được xem xét trong danh mục. Bộ sưu tập kỹ năng liệt kê các kỹ năng của đội ngũ VOOL trước, phía trên mục biên tập viên chọn và các kỹ năng nổi bật.

## Kỹ năng do VOOL làm: bước kiểm tra cục bộ

75 kỹ năng do VOOL làm được phát hành bên trong ứng dụng, mỗi kỹ năng được ghim bằng SHA-256 trong thư mục riêng của VOOL và được xem xét khi VOOL phát hành. Với các kỹ năng này, **Thêm kỹ năng này** chỉ chạy bước kiểm tra cục bộ của VOOL: không cần khóa, không tốn hạn mức quét và không có yêu cầu mạng nào.

1. VOOL đọc kỹ năng và giấy phép từ bản sao riêng của ứng dụng và từ chối chúng nếu SHA-256 của chúng không khớp với giá trị đã ghim (*This VOOL skill's file doesn't match the copy that came with VOOL, so it was not added.*).
2. Bước kiểm tra cục bộ của VOOL đọc kỹ năng để tìm các chỉ dẫn rủi ro và hiển thị kết quả. Một kết quả sạch có thể được thêm bằng **Thêm và bật**; bất cứ thứ gì bị gắn cờ đều cần bạn đồng ý riêng và tường minh cho đúng phiên bản đó. **Từ chối** sẽ loại bỏ nó.
3. Sau đó việc cài đặt đi theo cùng vòng đời như các kỹ năng khác: đúng các tệp đó được cài, xác minh và bật. Kỹ năng đã cài được đối chiếu với SHA-256 đã ghim mỗi lần nó được nạp; một tệp bị thay đổi sẽ khiến kỹ năng không khả dụng.

Bước kiểm tra cục bộ là một lần đọc tĩnh các byte cố định, không phải bảo đảm về an toàn hay chất lượng câu trả lời. Kỹ năng của những người làm khác không bao giờ đi theo con đường này.

## Kỹ năng công khai

Chọn một kỹ năng GitHub công khai tại một bản sửa đổi (revision) cố định và đồng ý tải nội dung của nó về. VOOL kiểm tra nó trên thiết bị này bằng `core/local_scan.py`. Khi không có plugin quét nào được bật, trang sẽ giải thích rằng các bước kiểm tra chỉ diễn ra trên thiết bị này. Con đường này không cần khóa của trình quét.

Một plugin quét đã bật cũng nhận nội dung kỹ năng công khai khi có yêu cầu quét. Việc này có thể tiêu tốn hạn mức dịch vụ của nó. Các cuộc trò chuyện cục bộ, tệp riêng trong không gian làm việc và các thông tin xác thực khác không được gửi đi. Lưu khóa và mở Tiện ích không tạo ra yêu cầu kiểm tra kết nối hay yêu cầu quét nào. Không có việc tự động thử lại yêu cầu.

Đọc các phát hiện và chấp nhận việc cài đặt một cách riêng biệt. Việc xác thực báo cáo đòi hỏi phạm vi bao phủ đầy đủ và các phát hiện hợp lệ. Một báo cáo đạt không có phát hiện hay vi phạm chính sách nào. Việc chấp nhận rủi ro được đưa ra riêng sẽ ràng buộc với phiên bản đã xem xét; nó không bao giờ thay đổi báo cáo hay cấp thêm quyền truy cập. Các lần xem xét đã hết hạn, byte trong vùng chuẩn bị bị thay đổi, liên kết và thư mục đích bị xung đột đều bị từ chối. Biên nhận ràng buộc toàn bộ bản phân phối đã cài. Các tệp bị thay đổi sẽ rút lại khả năng dùng khi chạy. Hướng dẫn của kỹ năng được chèn nguyên vẹn hoặc được bỏ qua một cách tường minh khi không vừa ngân sách lời nhắc.

## Trình quét tùy chọn

Trình quét từ xa là tùy chọn. Khi không có plugin quét nào được bật, bước kiểm tra của chính VOOL trên thiết bị này quyết định một kỹ năng có thể được cài hay không; các phiên bản trước thì đòi hỏi một lần quét từ xa.

Để thêm một trình quét, hãy cài plugin quét từ người làm ra nó qua mục Plugin, lưu khóa của riêng bạn, xem xét quyền truy cập rồi bật nó. Phần khai báo của nó nêu một đường dẫn cho việc kiểm tra kết nối tường minh và một đường dẫn cho việc quét được yêu cầu, và lớp truyền tải plugin gốc dùng chung sẽ gửi các yêu cầu đó. VOOL không có ứng dụng khách trình quét tích hợp sẵn hay thiết lập khóa trình quét tập trung, và không cung cấp plugin quét nào của riêng mình. Một khóa Eyebrow đã lưu từ VOOL phiên bản cũ vẫn nằm trên máy tính này và không được dùng. Các id `vool-plugin-eyebrow` và `vool-plugin-usepod` đã được giữ chỗ; một gói khác nhận một trong hai id này sẽ bị từ chối trước khi xem xét.

Bước kiểm tra của chính VOOL trên thiết bị này luôn chạy. Báo cáo của plugin quét được thêm bên cạnh nó: chỉ cần một trong hai phát hiện vấn đề là việc cài đặt bị chặn, và báo cáo của plugin phải nêu đúng nội dung đã được kiểm tra. Một plugin quét không bao giờ có thể tự mình phê duyệt một lần cài đặt.

Một trình quét đã bật mà thiếu khóa hoặc khóa bị từ chối sẽ thất bại một cách rõ ràng. VOOL không bao giờ lặng lẽ thay lần quét từ xa được yêu cầu đó bằng một kết quả cục bộ đạt. Hết thời gian chờ, lỗi dịch vụ, chuyển hướng, phản hồi quá cỡ và báo cáo không đầy đủ đều khiến việc cài đặt vẫn chưa được chấp nhận. Một yêu cầu bị hết thời gian chờ vẫn có thể đã tiêu tốn hạn mức dịch vụ.

## Gói thực thi và xem xét của cửa hàng

Plugin dùng một lần xem xét đầy đủ gói từ thư mục cục bộ hoặc gói GitHub công khai được ghim, tách biệt với trình nhập kỹ năng chỉ có văn bản. Việc cài đặt không chạy mã nào của gói và giữ gói ở trạng thái tắt. Plugin từ cửa hàng còn cần biên nhận đã ký hiện có và mã băm của toàn bộ bản phân phối. Việc bật đòi hỏi một bảng quyền truy cập riêng, bao gồm công cụ, tác động, đúng các máy chủ (host), ô thông tin xác thực và máy chủ đi kèm.

Mã thực thi chỉ nhận các khóa do chính nó khai báo khi được gọi. Nó không thể đọc ô của plugin khác, khóa mô hình trả phí của chủ sở hữu, hay quyền ký, hạn mức, phê duyệt hoặc biên nhận của ví. Đầu ra của nó vẫn bị coi là không đáng tin. Byte thay đổi sẽ rút lại sự đồng ý; tắt sẽ dừng các lệnh gọi. Cách ly không được hỗ trợ sẽ khiến việc thực thi bị từ chối. Xem [Viết plugin](../../../guides/plugin-authoring.md) để biết các giới hạn khi chạy.

Nếu VOOL không đọc được hồ sơ plugin của mình, các gói mà nó không có hồ sơ sẽ ngừng cung cấp kỹ năng, trừ những gói đi kèm VOOL, và tệp bị hỏng được giữ bên cạnh tệp mới. Nếu nó không đọc được các lựa chọn bật và tắt của bạn, mọi plugin đều được coi là tắt cho đến khi bạn bật lại.

## API cục bộ và truyền tải

`GET /api/addons` đọc danh mục hiện có. Các thao tác thay đổi tại `POST /api/addons` đòi hỏi quyền truy cập cục bộ của chủ sở hữu, JSON, yêu cầu trình duyệt cùng nguồn gốc và các trường riêng cho từng thao tác. Các cổng bật/tắt plugin và vòng đời (`POST /api/plugins/enable`, `POST /api/plugins/lifecycle`) áp dụng cùng quy tắc JSON và cùng nguồn gốc. Việc tải nguồn công khai về dùng đúng bản sửa đổi và các blob đã xác minh có giới hạn kích thước. Dịch vụ gốc đòi hỏi các máy chủ HTTPS công khai đã khai báo, xác thực TLS, ghim các địa chỉ công khai đã phân giải, từ chối chuyển hướng, giới hạn phản hồi và dừng lại khi plugin bị tắt.

Các dịch vụ HTTPS giả lập cục bộ có thể kiểm chứng việc truyền tải và xử lý lỗi mà không cần lệnh gọi trả phí. Chúng không chứng minh độ tin cậy của dịch vụ thực tế, việc tính phí, năng lực của mô hình hay việc cài đặt bên trong các ứng dụng khác. Những điều đó cần được kiểm chứng riêng.

---
description: Cách VOOL ghi nhớ mọi thứ giữa các phiên, và bộ nhớ đó nằm ở đâu.
translation: machine
source_sha256: 65af63e755076fe33675d7a5d6afa7f0c5664b77f716ffc0dc1f0f358e67f667
---

# Bộ nhớ

Bộ nhớ là cách VOOL mang các dữ kiện từ phiên này sang phiên khác. Nó được lưu dưới dạng tệp trên máy
của bạn, trong thư mục dữ liệu của VOOL, ở định dạng dễ đọc.

## Nó nhớ tốt đến mức nào

Trên 150 câu hỏi chưa từng được dùng từ bộ điểm chuẩn hội thoại dài LoCoMo, bộ nhớ đi kèm VOOL 0.7 trả
lời đúng 123 câu (82,0%). Trong vòng so sánh trực tiếp trên cùng bộ câu hỏi, với cùng một mô hình trả lời
cho cả ba, VOOL đạt 124 (82,7%), OpenClaw 107 (71,3%) và Hermes 94 (62,7%), trong khi gửi ít hơn từ 3,5
đến 5,4 lần số token mỗi câu hỏi. Mọi lần chạy đều được công bố:
[điểm chuẩn bộ nhớ](https://github.com/Parad0x-Labs/vool/tree/main/docs/benchmarks).

## Đặc điểm

* **Trên ổ đĩa, ở dạng mở.** Bộ nhớ là các tệp bạn có thể đọc. `MEMORY.md` là bộ mặt dễ đọc; các dữ
  kiện phía sau nó được giữ trong một tệp mục nằm cạnh nó.
* **Cục bộ.** Bộ nhớ không được đồng bộ lên máy chủ.
* **Một kho cho mỗi thư mục dữ liệu VOOL.** Bộ nhớ không được chia theo không gian làm việc — nó được giữ
  cho toàn bộ bản cài đặt, nên nó không phải là ranh giới cách ly theo từng dự án.

## Quản lý bộ nhớ

Các ghi chú bạn tự thêm vào `MEMORY.md` được giữ nguyên. Các dữ kiện VOOL đã ghi được xóa thông qua
thao tác quên của VOOL, không phải bằng cách xóa tệp: thao tác quên ghi lại kho mục và bản phản chiếu
trong một bước, rồi đọc lại cả hai để xác nhận văn bản đã biến mất. Chỉ xóa `MEMORY.md` thì không phải là
xóa dữ liệu — tệp mục vẫn giữ các dữ kiện, và bản phản chiếu được dựng lại từ đó.

Ở chế độ đám mây, phần bộ nhớ liên quan có thể là một phần của ngữ cảnh gửi tới nhà cung cấp, theo cùng
điều kiện như mọi ngữ cảnh khác. Xem [Cục bộ và đám mây](local-and-cloud.md).

Thao tác quên trong một cuộc trò chuyện cũng xóa các từ đó khỏi lịch sử chạy của cuộc trò chuyện ấy trong cơ
sở dữ liệu runtime: bản ghi lượt, điểm kiểm tra (checkpoint), các lần thử, kế hoạch tác vụ, câu trả lời đã
hoàn tất (kể cả các bản sao mà thao tác xóa câu trả lời quét qua), tri thức ứng viên, báo cáo ngữ cảnh, thông
báo, kết quả nghiên cứu mà cuộc trò chuyện tạo ra, và các tin nhắn được ghim đã trích dẫn chúng. Những bản
ghi mà các bản ghi khác phụ thuộc vào thì được giữ lại, nhưng các từ đã quên bị xóa trắng, cùng với mọi mã
băm (digest) được lưu bên cạnh chúng; phần còn lại bị xóa. Một lượt vẫn đang chạy khi lệnh quên đến không
thể ghi lại các từ đó: các sự kiện của nó bị xóa trắng và, khi nó kết thúc, các bản ghi của nó bị xóa thêm
lần nữa. Sau đó nhật ký ghi trước (write-ahead log) của cơ sở dữ liệu được gộp lại để các bản sao cũ không
còn nằm trong tệp `-wal`. Bản ghi của cuộc trò chuyện khác không bị động đến. Một tin nhắn đang chờ chưa
được gửi thì được giữ lại, và khi đó lệnh quên sẽ báo rằng nó không thể được áp dụng đầy đủ. Một bảng mà
không bước xóa nào biết đến sẽ được nêu tên trong câu trả lời, kèm lời cho biết thử lại cũng sẽ không xóa
được nó.

Các từ được so sánh không phân biệt chữ hoa chữ thường, và một chữ số viết bằng hệ chữ khác vẫn là chữ số
đó ("１２" dạng toàn độ rộng là 12). Một số đã quên chỉ khớp ở nơi nó đứng như một số riêng: các chữ cái
và chữ số viết liền với nó không được chứa chữ số nào khác, và nó không được nối tiếp qua `.`, `,`, `:`,
`/`, `-` hoặc `_` sang một chữ số khác. Quên "12" sẽ xóa "mã 12", "12kg" và "B12", nhưng không xóa
"phòng 312", "3.12", "12.5", "12:30" hay "2026-10-12", và quên "200" không xóa "1,200". Trong lịch sử
chạy, một con số trần mà runtime đã đếm hoặc đo thời gian (chẳng hạn một giá trị `elapsed_ms` hoặc
`step_count`) không được so khớp.

Trình đọc bộ nhớ cũng kiểm tra một bản ghi gốc về việc quên (forget authority) lâu bền. Một thư mục bộ nhớ
mới tạo bản ghi này trước khi nhận bất kỳ lượt ghi nào. Một kho ngữ nghĩa cũ hơn chỉ được nâng cấp tại chỗ
khi không có lần xóa nào trước đó được ghi lại; các mục bộ nhớ được giữ lại của nó vẫn dùng được. Một bản
ghi gốc đã có nhưng không đọc được, một bản ghi gốc bị thiếu trong khi vẫn còn bằng chứng (witness) đã lưu,
hoặc một rào chắn quên (forget barrier) đã được ghi lại sẽ khiến trình đọc không đưa ra phần bộ nhớ bị ảnh
hưởng. Xóa các tệp bản ghi gốc không phải là cách để khôi phục nội dung đã quên.

Một nhật ký xóa đang chờ giữ lại token của nó để phục hồi. Khi bản thu hồi vĩnh viễn theo phạm vi đã tồn
tại, bước hoàn tất sẽ xóa token thừa của nhật ký nhưng vẫn giữ danh tính và trạng thái của thao tác. Bản
ghi gốc về việc thu hồi vẫn còn.

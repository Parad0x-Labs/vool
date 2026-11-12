---
description: Cuộc trò chuyện, cuộc trò chuyện riêng tư, và các tệp cùng những đoạn dán dài bạn đưa vào đó: giới hạn, những gì được giữ lại và những gì có thể bị từ chối.
translation: machine
source_sha256: 1497140fef69088f8e0210aeb8a7f6abba6ae82d5132841ce397fe60fa1bab99
---

# Cuộc trò chuyện và tệp đính kèm

Cuộc trò chuyện là một cuộc hội thoại với VOOL. Các cuộc trò chuyện được lưu trên máy tính của bạn. Một
cuộc trò chuyện có thể được gắn với một thư mục dự án, để các công cụ của nó làm việc trong thư mục đó (xem
[Không gian làm việc](workspaces.md)), và bạn có thể đưa tệp vào đó dưới dạng tệp đính kèm.

## Đính kèm tệp

Dùng **Đính kèm** cạnh ô nhập tin nhắn, hoặc kéo tệp thả vào ô đó. Mỗi tệp hiện thành một thẻ nhỏ, và
nó đi kèm tin nhắn bạn gửi tiếp theo. Dòng bên dưới ô nhập cho biết các giới hạn:

* Tối đa 8 tệp mỗi tin nhắn, 10 MB mỗi tệp và 25 MB mỗi tin nhắn.
* Tệp văn bản và tệp mã, cùng ảnh PNG, JPEG, GIF và WebP. PDF, Word (DOCX), tệp nén như ZIP, và
  video được nhận thêm khi máy tính này có những gì cần thiết để đọc chúng. Hộp chọn tệp chỉ đưa ra
  những gì máy tính này đọc được.

Những gì mô hình nhận được cũng có giới hạn: tối đa 120.000 ký tự của một tệp và 240.000 ký tự của tất
cả các tệp trong một tin nhắn. Tệp dài hơn sẽ bị cắt ở cuối một dòng, và bản ghi cho biết chỗ cắt. Nếu
mô hình bạn dùng không đọc được ảnh, thẻ tệp sẽ báo như vậy, và ảnh không được gửi tới mô hình đó.

Trước khi một ảnh được lưu, các thông tin ẩn của nó bị xóa bỏ: dữ liệu máy ảnh và vị trí (EXIF,
XMP, IPTC), và văn bản được giữ bên trong tệp PNG và WebP.

Tệp bạn chọn chỉ được dùng cho đúng tin nhắn đó. Khi câu trả lời hoàn tất, nội dung của tệp bị xóa và
một biên nhận về tệp đó được giữ lại.

## Đoạn dán dài

Khi bạn dán từ 4.000 ký tự trở lên, VOOL giữ đoạn đó làm một tài liệu của cuộc trò chuyện thay vì một tin
nhắn, đúng như khi dán. **Xem trước** hiển thị phần đầu của nó. Tài liệu ở lại với cuộc trò chuyện cho đến
khi bạn xóa nó hoặc xóa cuộc trò chuyện. Các câu hỏi sau đó trong cùng cuộc trò chuyện có thể dùng nó: VOOL
đưa cho mô hình tối đa 60.000 ký tự từ các tài liệu trước đó của cuộc trò chuyện, mới nhất trước, và cho
biết chỗ nó cắt.

## Khi một tệp bị từ chối

Tệp bị từ chối không bao giờ bị bỏ đi một cách lặng lẽ. Thẻ tệp của nó vẫn ở đó và cho biết lý do, và tin
nhắn không thể được gửi cho đến khi bạn xóa hoặc thử lại tệp đó: *Trước khi gửi, hãy xóa hoặc thử lại tệp
đính kèm bị lỗi.* Các lý do thường gặp:

* **Quá lớn, hoặc quá nhiều tệp.** Hãy bỏ bớt, hoặc gửi chúng trong hai tin nhắn.
* **Không phải loại VOOL đọc được, hoặc không đúng như tên của nó.** Một tệp tên `.txt` nhưng thực ra là
  ảnh hoặc chương trình, hoặc một tệp PDF mà máy tính này không có trình đọc, sẽ bị từ chối kèm lý do.
* **Có thông tin đăng nhập bên trong.** Một tệp hoặc đoạn dán có vẻ chứa một khóa hoặc token còn hiệu
  lực, chẳng hạn một khối khóa riêng tư hoặc một access token, sẽ bị từ chối và không có gì được lưu. Hãy
  xóa phần bí mật, rồi đính kèm hoặc dán lại.

Những lần từ chối này có mã ngắn riêng, hiển thị cùng thông báo. Chúng không có trong
[Sổ tay lỗi](../ERROR_BOOK.md), vốn đề cập các lỗi khác của ứng dụng.

## Cuộc trò chuyện riêng tư

Hãy nói "private chat", "don't record this" hoặc "off the record", và cuộc trò chuyện sẽ ngừng lưu kể từ
tin nhắn đó: không tin nhắn, không tóm tắt, không bộ nhớ. Một biểu ngữ hiển thị *Trò chuyện riêng tư:
không có gì được lưu*, và mỗi tin nhắn riêng tư mang một chú ma nhỏ. Tin nhắn riêng tư không quay lại sau
khi tải lại trang, và VOOL không thể nhớ lại chúng sau này. Hãy nói "turn recording back on", hoặc nhấn
**Bật lại ghi**, để lưu lại cuộc trò chuyện từ tin nhắn tiếp theo. Một cuộc trò chuyện riêng tư vẫn riêng
tư sau khi VOOL khởi động lại.

## Mang cuộc trò chuyện theo bạn

**Xuất** ở thanh tiêu đề của cuộc trò chuyện lưu toàn bộ cuộc trò chuyện dưới dạng văn bản hoặc Markdown, gồm mọi tin
nhắn, với tệp đính kèm chỉ được liệt kê theo tên và không bao giờ kèm nội dung của chúng. Để chuyển một
cuộc trò chuyện sang máy tính khác, hoặc giữ một bản sao có chữ ký, hãy xem
[Sao lưu và khôi phục](../help/backup-and-recovery.md).

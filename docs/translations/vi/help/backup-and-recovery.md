---
description: Những gì bạn có thể lưu, chuyển và khôi phục trong phiên bản VOOL này: cuộc trò chuyện, bộ nhớ, khóa và ví.
translation: machine
source_sha256: 3b2f11975a8cf5f5ab2e07e8b4a3083a9598de9e301965c493408005aa5893fa
---

# Sao lưu và khôi phục

Mọi thứ VOOL lưu giữ đều nằm trên máy tính của bạn, trong một thư mục hồ sơ duy nhất (xem
[Cấu hình](../reference/configuration.md) để biết vị trí). Việc cập nhật hay cài lại ứng dụng không bao
giờ động đến thư mục đó. VOOL không có bản sao dữ liệu của bạn trên đám mây và không có cách sao lưu toàn bộ
hồ sơ trong một bước. Dưới đây là những cách để lưu và lấy lại những gì quan trọng.

## Cuộc trò chuyện

| Bạn muốn | Dùng | Bạn nhận được |
| --- | --- | --- |
| Một bản sao để đọc hoặc chia sẻ | **Xuất** ở thanh tiêu đề của cuộc trò chuyện | Toàn bộ cuộc trò chuyện dưới dạng văn bản hoặc Markdown. Tệp đính kèm chỉ được liệt kê theo tên. |
| Một bản sao để đưa trở lại VOOL | Cài đặt, Sao lưu và phục hồi, **Xuất một cuộc trò chuyện** | Một cuộc trò chuyện dưới dạng tệp `.voolsession`, được máy tính này ký. |
| Lấy lại một cuộc trò chuyện từ tệp đó | Cài đặt, Sao lưu và phục hồi, **Nhập một cuộc trò chuyện** | Cuộc trò chuyện đó, được thêm vào cạnh các cuộc trò chuyện khác của bạn. |
| Các cuộc trò chuyện của bạn từ ứng dụng AI khác | Cài đặt, Sao lưu và phục hồi, **Mang các cuộc trò chuyện từ ứng dụng AI khác sang** | Những cuộc trò chuyện đó, với tên và tin nhắn của chúng. |

**Xuất một cuộc trò chuyện** mang theo tin nhắn, biên nhận và các tham chiếu bằng chứng của cuộc trò
chuyện, không bao giờ mang suy luận nội bộ của VOOL, cài đặt, bộ nhớ hay khóa của bạn. Hãy bảo vệ tệp bằng
một cụm mật khẩu khi nó rời khỏi máy tính này: một bản xuất không có cụm mật khẩu sẽ yêu cầu bạn xác nhận
trước. Một bản xem trước hiển thị chính xác những gì sẽ được ghi trước khi có bất cứ thứ gì được ghi.

**Nhập một cuộc trò chuyện** hiển thị nội dung trong tệp và người đã ký tệp đó trước khi có bất cứ thứ gì
được thêm vào. Một tệp được ký bằng một khóa mà VOOL không biết sẽ bị từ chối trừ khi bạn chấp nhận nó, và
cuộc trò chuyện vẫn bị đánh dấu là không đáng tin cậy. Việc nhập không bao giờ thay đổi một cuộc trò chuyện
bạn đã có: nếu cùng cuộc trò chuyện đó đã có ở đây, bản sao sẽ đến như một cuộc trò chuyện mới.

**Mang các cuộc trò chuyện từ ứng dụng AI khác sang** đọc một ứng dụng trên máy tính này hoặc một tệp dữ
liệu đã xuất. Bạn đánh dấu các cuộc trò chuyện cần mang vào. Không có gì rời khỏi máy tính của bạn và các
tệp của ứng dụng kia không bao giờ bị thay đổi. VOOL giữ các cuộc trò chuyện này làm lịch sử: nó không học
từ chúng hay làm theo bất cứ điều gì được viết trong đó.

## Bộ nhớ

Cài đặt, Bộ nhớ và cá nhân hóa, **Xuất hồ sơ** lưu những gì VOOL nhớ về bạn thành một tệp có tên
`vool-memory-` kèm ngày tháng. Tệp này không bao giờ chứa khóa hay mật khẩu. Đó là một bản sao để đọc và
lưu giữ: VOOL không có chức năng nhập cho nó.

## Khóa của nhà cung cấp mô hình

Khóa của nhà cung cấp không có trong bất kỳ bản xuất nào. Trên một máy tính mới, hãy thêm lại chúng trong
Cài đặt.

## Ví

* **Một Ví VOOL được tạo cho một mạng** hiển thị một bản sao lưu khóa riêng tư trong khi thiết lập, và chỉ
  lúc đó. Hãy giữ nó ngoại tuyến: bất kỳ ai có nó đều kiểm soát được ví. Nếu bạn quên PIN hoặc mật khẩu
  của ví, **Forgot PIN or password?** trên ví trong Cài đặt, Tiền điện tử sẽ khôi phục quyền truy cập bằng
  bản sao lưu đó, và bạn chọn một PIN hoặc mật khẩu mới.
* **Túi cũ** được giữ bằng cụm từ khôi phục của nó, được hiển thị một lần khi nó được tạo.
  **Restore from phrase…** trong Cài đặt, Tiền điện tử, Tài khoản khác khôi phục nó từ cụm từ đó. Nó cũng
  có thể xuất khóa riêng tư sang Phantom hoặc MetaMask bằng **Export private key**, chức năng này chỉ mở
  bằng Touch ID hoặc mật khẩu Mac của bạn.
* Một ví bạn chỉ theo dõi, hoặc một ví bạn đã kết nối từ ứng dụng khác, không giữ khóa nào trong VOOL:
  không có gì để sao lưu ở đây.

Xem [Ví](../guides/wallet.md) để có bức tranh đầy đủ.

## Nếu một bản cập nhật thất bại

Một bản cập nhật thất bại ở bất kỳ bước nào sẽ để ứng dụng như cũ. Nếu việc chuyển dữ liệu của bạn sang
phiên bản mới thất bại, dữ liệu của bạn được đưa về như cũ và VOOL khởi động trên phiên bản trước. Xem
[Cập nhật và chuyển đổi dữ liệu](../reference/updates-migration.md).

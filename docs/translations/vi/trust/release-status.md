---
description: VOOL đang ở giai đoạn nào, nói rõ ràng.
translation: machine
source_sha256: ad4e68eba0b8f45c35cf4ce8b87f79ee04e3aaee23c83f2ff6d794fa35d56c06
---

# Trạng thái phát hành

Trang này là nguồn thông tin chính thức, được duy trì, về trạng thái phát hành. Các tài liệu khác có nhắc
qua các thông tin phát hành; khi chúng mâu thuẫn với trang này, trang này được ưu tiên.

## Giai đoạn hiện tại

VOOL đang ở giai đoạn **beta**. Bản phát hành hiện tại là
[**v0.7.7-beta**](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.7-beta), một bản phát hành GitHub
được dựng từ `main` tại `8af687c`. Bản này có bản tải xuống ứng dụng cho ba nền tảng, mỗi bản kèm một mã
kiểm tra (checksum) SHA-256 được công bố. Việc cài từ mã nguồn bằng tập lệnh khởi tạo vẫn được hỗ trợ trên
macOS, Linux và Windows; xem [Cài đặt VOOL](../getting-started/install.md).

| Tải xuống | Dành cho |
| --- | --- |
| `VOOL-0.7.7-beta-macos-arm64.dmg` | Mac dùng Apple Silicon, macOS 14 trở lên |
| `VOOL-0.7.7-beta-macos-x86_64.dmg` | Mac dùng Intel, macOS 14 trở lên |
| `vool-0.7.7-beta-linux-x86_64.tar.gz` | Linux x86_64, đã thử nghiệm trên Ubuntu 22.04 và 24.04 |

Hồ sơ phát hành của kho mã này bao gồm kênh GitHub. Các kênh tải xuống khác được vận hành riêng; hãy xác
minh mọi tệp tải xuống dựa trên mã kiểm tra được công bố trên bản phát hành GitHub.

| | Trạng thái |
| --- | --- |
| Bản tải xuống công khai | DMG macOS Apple Silicon, DMG macOS Intel, gói tar Linux x86_64 trên bản phát hành GitHub v0.7.7-beta |
| Mã kiểm tra được công bố | SHA-256 cho mọi tệp tải xuống, trên trang bản phát hành |
| Ký mã | Các ứng dụng macOS được ký ad-hoc, không phải Developer ID. Việc ký sẽ được bổ sung sau bản beta này và không phải là điều kiện để phát hành bản beta này |
| Công chứng macOS | Chưa được công chứng. macOS yêu cầu bạn xác nhận lần mở đầu tiên (xem bên dưới) |
| Windows | Bản phát hành này không có bản tải xuống cho Windows; trên Windows, VOOL được cài từ mã nguồn và ở mức thử nghiệm |
| Cập nhật | Từ 0.7.5 trở đi, VOOL tự cập nhật: khi "Tự lên mạng" đang bật, nó hiện thông báo và cài phiên bản mới chỉ với một lần nhấn. Cập nhật từ 0.7.4 hoặc 0.7.3 trên Mac: bật "Tự lên mạng" (Cài đặt > Riêng tư và Quyền) rồi nhấn Kiểm tra bản cập nhật một lần. Trên Linux, hoặc từ 0.7.2, 0.7.1 hoặc 0.7.0: tải 0.7.7 và cài đè lên phiên bản hiện tại. Các cuộc trò chuyện và thiết lập của bạn nằm trong hồ sơ của bạn, bên ngoài ứng dụng. Xem [Cập nhật và chuyển đổi dữ liệu](../reference/updates-migration.md) |
| Độ ổn định của API | Thay đổi giữa các bản dựng |

## Điều đó có ý nghĩa gì với bạn

* Ở lần khởi chạy đầu tiên của một ứng dụng Mac, macOS từ chối mở nó vì nó chưa được công chứng. Điều này
  là bình thường ở giai đoạn này, không phải dấu hiệu tệp tải xuống bị hỏng. Hãy kiểm tra mã kiểm tra, rồi
  mở **Cài đặt hệ thống → Quyền riêng tư & Bảo mật** và bấm **Vẫn mở**. Trên macOS 14, bạn có thể thay vào
  đó giữ Control khi bấm vào ứng dụng và chọn **Mở**.
* Hãy chọn bản tải xuống cho Mac khớp với chip của bạn. Ứng dụng cho Apple Silicon không chạy trên Mac
  dùng Intel và sẽ báo như vậy khi khởi chạy.
* Định dạng cấu hình có thể thay đổi giữa các bản dựng.
* Các tính năng tiền điện tử tắt cho đến khi bạn bật chúng.

## Các hạn chế của bản beta và giai đoạn thí điểm, nói rõ

| Khả năng | Trạng thái trong bản dựng này |
| --- | --- |
| Tiền điện tử và ví | Mặc định tắt. Khi bạn bật, VOOL dùng mainnet; các mạng thử nghiệm nằm trong Tùy chọn nhà phát triển. Mọi khoản thanh toán đều đi qua ví của VOOL, với các giới hạn của ví, sự phê duyệt của bạn và một biên nhận. Xem [Ví](../guides/wallet.md). |
| Chuyển tiền trên mainnet | Coin gốc, và USDT trên Solana, Ethereum, BNB Smart Chain và Arbitrum One, được gửi từ một Ví VOOL được tạo trên thiết bị này. USDC trên Solana và Base chỉ dùng cho kênh thanh toán. Một tài khoản đã kết nối hoặc chỉ xem có thể nhận và hiển thị số dư nhưng không thể gửi từ VOOL. |
| Thanh toán bằng x402 | Các trang web yêu cầu thanh toán x402 bằng USDC trên Base hoặc Base Sepolia có thể được thanh toán từ ví Crypto Pilot của bạn: một lần phê duyệt bằng PIN, trong phạm vi giới hạn của bạn. Dịch vụ thanh toán của trang web chịu phí mạng. **Cài đặt → Tiền điện tử → Payment channels** có một công tắc bật/tắt cho mỗi cách ví có thể thanh toán cho một trang web; một kênh đang tắt sẽ từ chối trước khi bất cứ thứ gì được ký. Chưa có khoản thanh toán mainnet thật nào được thực hiện theo cách này. Thanh toán qua Solana pay-kit không có trong các bản tải xuống; kênh đó hiện là không dùng được. |
| X | Từ bất kỳ cuộc trò chuyện nào: tìm bài đăng và tài khoản, Spaces, xu hướng và tin nhắn riêng (DM), và tra cứu các lần call trước đây về một token. Mọi thao tác tác động lên tài khoản của bạn đều chờ bạn đồng ý. |
| Đọc thành tiếng | Không khả dụng trong bản dựng beta này. |
| Chế độ hỏi "tăng tốc" (burst) trên đám mây | Hộp hỏi xác nhận cho từng lệnh gọi chưa được kết nối trong bản dựng này. |
| Mô hình cục bộ | Trước khi một mô hình cục bộ có thể trả lời, hãy mở **Cài đặt → Mô hình và nhà cung cấp → Mô hình cục bộ** và nhấn **Certify** cho mô hình đó một lần. |
| Mac dùng Intel | Mô hình cục bộ chạy trên bộ xử lý và chậm. Bước chứng nhận có tính đến bộ xử lý chậm hơn, nhưng một câu trả lời dài dựa vào bộ nhớ có thể bị quá thời gian chờ, vì vậy hiện tại hãy dùng mô hình đám mây trên các máy này. |
| Trò chuyện riêng tư | Hãy nói "private chat", "don't record this" hoặc "off the record", và không có gì từ cuộc trò chuyện đó được lưu. Các lệnh này hoạt động trong cả 17 ngôn ngữ của ứng dụng. |
| Quên | Quên xóa bộ nhớ của VOOL và lịch sử chạy tương ứng, để mục đó không được nhớ lại hay liệt kê lại, và xóa các từ đã quên khỏi tệp bộ nhớ cục bộ của VOOL và chỉ mục tìm kiếm của nó. VOOL giữ các từ bạn đã yêu cầu nó quên trong một danh sách ngắn trên máy tính của bạn, để nó có thể tiếp tục từ chối chúng. Sau khi bạn quên một điều gì đó trong một cuộc trò chuyện, VOOL sẽ không tiếp nhận lại cùng dữ kiện đó trong cuộc trò chuyện ấy; hãy bắt đầu một cuộc trò chuyện mới để nói lại với nó. |
| PDF dạng ảnh quét | Được đọc bằng tính năng nhận dạng văn bản có sẵn của máy Mac, nên chỉ trên Mac. |
| Thú cưng desktop | Thú cưng sống trong cửa sổ VOOL. Kéo nó ra khỏi cửa sổ không đưa nó ra màn hình nền. |
| Bộ sưu tập kỹ năng | Duyệt bộ sưu tập kỹ năng hiển thị các kỹ năng được liệt kê cho đúng phiên bản VOOL của bạn, và bạn xem xét một kỹ năng trước khi nó được cài. Đăng lên bộ sưu tập kỹ năng kiểm tra một kỹ năng bạn đã tạo trên máy tính của mình, rồi mở trang web Bộ sưu tập kỹ năng với kỹ năng đó đã được điền sẵn; bạn đăng nhập ở đó và nhấn Send, và bản thân VOOL không gửi gì cả. Chỉ văn bản của kỹ năng được gửi kèm, nên một kỹ năng có các tệp bổ sung hiện chưa thể chia sẻ. |

Việc ký Developer ID và công chứng cho macOS, cùng mã kiểm tra có chữ ký cho Linux, là các bước tiếp theo
trên lộ trình phát hành; hiện chưa có điều nào trong số đó được khẳng định.

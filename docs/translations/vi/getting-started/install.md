---
description: Cách cài trợ lý AI VOOL trên macOS (Apple Silicon hoặc Intel) hoặc Linux từ các bản tải xuống của bản phát hành, hoặc từ mã nguồn trên macOS, Windows hoặc Linux, kèm yêu cầu hệ thống và cách xác minh tệp tải xuống.
translation: machine
source_sha256: e0025d89fc56caca91923de6f1e6afe25ba6f6383a300ec3df5a7f7e3f401e55
---

# Cài đặt VOOL

VOOL 0.7.7-beta có bản tải xuống ứng dụng cho Mac dùng Apple Silicon, Mac dùng Intel và Linux x86_64 tại
[bản phát hành v0.7.7-beta](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.7-beta).
Bạn cũng có thể cài từ mã nguồn bằng tập lệnh khởi tạo trên macOS, Linux hoặc Windows.
Xem [Trạng thái phát hành](../trust/release-status.md) để biết chi tiết về ký số và cập nhật.

## Yêu cầu

| | Tối thiểu | Thoải mái |
| --- | ---: | ---: |
| Bộ nhớ RAM | 8 GB | 16 GB trở lên |
| Ổ đĩa | 2 GB cho ứng dụng | 20 GB kèm mô hình cục bộ |
| macOS | 14 Sonoma, Apple Silicon hoặc Intel ([giới hạn](#mac-dùng-intel)) | 14 Sonoma trở lên, Apple Silicon |
| Windows | 10 (64-bit) | 11 |
| Linux | glibc 2.31 | Ubuntu 22.04 trở lên |

Mô hình cục bộ cần nhiều bộ nhớ RAM hơn hẳn bản thân ứng dụng. Hướng dẫn chọn kích thước có trong
[Chạy mô hình cục bộ](../guides/local-models.md).

## Tải ứng dụng

| Máy tính của bạn | Tải xuống |
| --- | --- |
| Mac dùng Apple Silicon (M1 trở về sau) | [`VOOL-0.7.7-beta-macos-arm64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-arm64.dmg) |
| Mac dùng chip Intel | [`VOOL-0.7.7-beta-macos-x86_64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-x86_64.dmg) |
| Linux x86_64 (Ubuntu 22.04 / 24.04) | [`vool-0.7.7-beta-linux-x86_64.tar.gz`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/vool-0.7.7-beta-linux-x86_64.tar.gz) |

Để biết Mac của bạn dùng chip nào, hãy mở menu Apple và chọn **Giới thiệu về máy Mac này**. "Apple M…"
nghĩa là Apple Silicon; "Intel" nghĩa là Intel. Ứng dụng cho Apple Silicon không chạy trên Mac dùng Intel,
và sẽ báo như vậy khi bạn mở.

Hãy kiểm tra SHA-256 của tệp tải xuống trước khi mở (xem [Xác minh tệp tải xuống](#xác-minh-tệp-tải-xuống)).

### Mở lần đầu trên Mac

Ứng dụng beta chưa được Apple công chứng, nên macOS sẽ yêu cầu bạn xác nhận vào lần đầu
mở. Điều này là bình thường và chỉ xảy ra một lần.

1. Mở tệp DMG và kéo **VOOL** vào **Ứng dụng**.
2. Bấm đúp vào VOOL. macOS cho biết không thể kiểm tra ứng dụng xem có phần mềm độc hại hay không.
   Đóng thông báo đó.
3. Mở **Cài đặt hệ thống → Quyền riêng tư & Bảo mật**. Cuộn xuống thông báo về VOOL
   và bấm **Vẫn mở**. Nhập mật khẩu của bạn để xác nhận.

Trên macOS 14 Sonoma, bạn cũng có thể giữ Control khi bấm vào VOOL trong thư mục Ứng dụng, chọn **Mở**,
rồi bấm **Mở** thêm lần nữa.

### Linux

Tải gói tar và tệp `.sha256` đi kèm từ bản phát hành, kiểm tra gói, rồi chạy
trình cài đặt đi kèm. Không cần sudo hay Python của hệ thống.

```bash
sha256sum -c vool-0.7.7-beta-linux-x86_64.tar.gz.sha256
tar -xzf vool-0.7.7-beta-linux-x86_64.tar.gz
bash vool-0.7.7-beta-linux-x86_64/install.sh
~/.local/bin/vool
```

VOOL được cài vào `~/.local/share/vool`, đặt lệnh `vool` vào `~/.local/bin` và thêm
một mục vào menu ứng dụng. Bản cho máy tính mở cuộc trò chuyện trong trình duyệt của bạn; đóng trình duyệt
không làm VOOL dừng. Để gỡ VOOL, hãy chạy `bash ~/.local/share/vool/current/uninstall.sh`.
Các cuộc trò chuyện và thiết lập của bạn vẫn nằm trong hồ sơ của bạn.

Từ 0.7.5 trở đi, bản tải xuống cho Linux được cập nhật ngay trong VOOL. Phiên bản mới được cài cạnh
phiên bản cũ. Nếu nó không khởi động đúng cách, VOOL tự quay lại phiên bản trước.

### Sau khi cài

Trước khi một mô hình cục bộ có thể trả lời, hãy mở **Cài đặt → Mô hình và nhà cung cấp → Mô hình cục bộ** và nhấn **Certify** cho mô hình đó một lần. Thao tác này chạy một bước kiểm tra ngắn trên máy của bạn.

Từ 0.7.5 trở đi, VOOL tự cập nhật: khi "Tự lên mạng" đang bật, nó hiện thông báo và cài phiên bản mới chỉ với một lần nhấn. Cập nhật từ 0.7.4 hoặc 0.7.3 trên Mac: bật "Tự lên mạng" (Cài đặt > Riêng tư và Quyền) rồi nhấn "Kiểm tra bản cập nhật" một lần. Từ 0.7.4 trở về trước trên Linux, hoặc 0.7.2 trở về trước trên Mac: tải 0.7.7 và cài đè lên phiên bản hiện tại. Các cuộc trò chuyện và thiết lập của bạn
nằm trong hồ sơ của bạn, bên ngoài ứng dụng. Các tính năng tiền mã hóa mặc định đều tắt.

## Cài từ mã nguồn

Chạy tập lệnh khởi tạo cho nền tảng của bạn:

```bash
curl -fsSLo bootstrap_vool.sh https://raw.githubusercontent.com/Parad0x-Labs/vool/v0.7.7-beta/installer/bootstrap_vool.sh
bash bootstrap_vool.sh --ref v0.7.7-beta
```

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.ps1 -OutFile bootstrap_vool.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_vool.ps1
```

Các lệnh cho macOS / Linux cố định cả tập lệnh khởi tạo lẫn phần tải mã nguồn của nó ở `v0.7.7-beta`. Để chọn bản phát hành khác, hãy đổi cả thẻ trong URL của tập lệnh lẫn `--ref`. Tập lệnh khởi tạo chấp nhận tên nhánh hoặc thẻ phát hành, không chấp nhận mã băm commit.

Cài từ mã nguồn cần Python 3.11 trở lên. Nếu không tìm thấy, trình cài đặt sẽ thử
thiết lập một bản. Các bản tải xuống ứng dụng đã có sẵn Python riêng.

Trình cài đặt chuẩn bị môi trường Python, kiểm tra phần cứng của bạn, thiết lập mô hình
cục bộ và khởi chạy các dịch vụ cục bộ. Hãy chừa thời gian và dung lượng ổ đĩa cho lần tải mô hình đầu tiên.
Sau khi thiết lập, mô hình cục bộ hoạt động ngoại tuyến; công cụ web và mô hình đám mây cần
kết nối mạng.

## Mac dùng Intel

VOOL chạy trên Mac dùng Intel với macOS 14 trở lên (ví dụ một chiếc MacBook Pro 13 inch đời 2020),
từ bản tải xuống cho Intel hoặc từ mã nguồn. Trình cài đặt từ mã nguồn nhận ra CPU Intel và
cài bản dựng cho Intel của mọi phần phụ thuộc. Có hai giới hạn chỉ riêng Intel gặp phải:

* **Không huấn luyện mô hình cục bộ.** PyTorch không còn phát hành bản dựng cho macOS trên Intel kể từ 2.3, nên
  trình cài đặt bỏ qua nó và chức năng huấn luyện LoRA báo là không khả dụng. Trò chuyện, bộ nhớ,
  công cụ và mô hình đám mây không bị ảnh hưởng.
* **Mô hình cục bộ chỉ chạy trên CPU.** Các trình chạy mô hình cục bộ không có tăng tốc GPU cho đồ họa Intel,
  nên câu trả lời cục bộ chậm hơn vài lần so với trên Apple Silicon. Trên Mac 8 GB, hãy dùng mô hình đám mây
  để trò chuyện; với 16 GB, mô hình cục bộ nhỏ mặc định của trình cài đặt vẫn chạy được nhưng chậm.

Trên Mac dùng Intel, mô hình cục bộ chạy trên bộ xử lý và chậm. Một câu trả lời dài dựa vào bộ nhớ có thể bị quá thời gian chờ, vì vậy hiện tại hãy dùng mô hình đám mây trên các máy này. Bước chứng nhận mô hình cục bộ có tính đến bộ xử lý chậm hơn.

Mac dùng Intel có bản tải xuống riêng, `VOOL-0.7.7-beta-macos-x86_64.dmg`. Bản tải xuống cho Apple Silicon
không chạy trên Intel và sẽ báo như vậy khi khởi động.

## Xác minh tệp tải xuống

Mỗi tệp tải xuống trong bản phát hành đều có mã kiểm tra (checksum) SHA-256 bên cạnh. Hãy xác minh tệp trước
khi mở:

```bash
shasum -a 256 <downloaded-artifact>    # macOS
sha256sum <downloaded-artifact>         # Linux
```

```powershell
Get-FileHash -Algorithm SHA256 <downloaded-artifact>
```

So sánh kết quả với mã kiểm tra được công bố cùng bản phát hành đó. Nếu chúng khác nhau,
hãy xóa tệp và tải lại.

## Tiếp theo

Tiếp tục với [Lần chạy đầu tiên](first-run.md).

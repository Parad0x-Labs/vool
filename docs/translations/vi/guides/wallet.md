---
description: Ví Crypto Pilot tùy chọn — mạng, thanh toán x402, phí DNA và khôi phục, đúng như bản dựng này thực sự cung cấp.
translation: machine
source_sha256: 46121a5ed26be175ddd3c66b269df356e074adbb57c8e9806461b80197613cc2
---

# Ví

Ví là **tùy chọn và mặc định tắt**. Nó tồn tại để VOOL có thể giữ một lượng nhỏ tiền điện tử cho các dịch
vụ AI tính phí theo từng yêu cầu. Bạn không cần bất cứ điều gì liên quan đến nó để dùng VOOL.

Trang này mô tả những gì bản dựng hiện tại thực sự hỗ trợ. Ví đang ở giai đoạn **thí điểm**: các khả năng
được thêm vào theo từng làn, và mỗi làn dưới đây nói chính xác nó làm gì.

## Môi trường mạng

Khi Tiền điện tử được bật, VOOL chạy trên **Mainnet**: Solana, Robinhood Chain, Base, Ethereum, BNB và
Arbitrum One.
Quy trình thiết lập thông thường không bao giờ yêu cầu bạn chọn môi trường mạng.

* **Mainnet** — mặc định cho mọi bản cài đặt, kể cả các bản cài được nâng cấp có tài khoản mạng thử
  nghiệm cũ. Mọi giới hạn chi tiêu, phê duyệt và biên nhận đều áp dụng như mô tả bên dưới.
* **Test networks** — Solana Devnet, Robinhood Chain Testnet và các làn thử nghiệm khác. Chỉ dành cho nhà
  phát triển: chuyển trong **Cài đặt → Tiền điện tử → Tùy chọn nhà phát triển**.

Tài khoản, số dư và biên nhận thuộc về một môi trường và không bao giờ di chuyển giữa các môi trường; tài
khoản trên môi trường kia vẫn được liệt kê trên mạng riêng của chúng. Một yêu cầu cho một mạng thuộc môi
trường kia sẽ bị từ chối mà không có gì được tạo, đề xuất hay ký.

{% hint style="warning" %}
Tài liệu trước đây nói rằng mainnet là không thể có trong bản dựng này. Điều đó đúng trước giai đoạn thí
điểm và giờ không còn đúng nữa: các dòng mainnet hiện đã có. Điều vẫn đúng thì hẹp hơn — xem các phần
chuyển tiền và thanh toán bên dưới để biết chính xác những làn nào có trên mainnet.
{% endhint %}

## Thứ gì di chuyển trên làn nào

* **Chuyển tiền di chuyển coin gốc** (ví dụ SOL trên Solana, ETH trên Base, Ethereum hoặc Arbitrum One),
  và **USDT trên Solana, Ethereum, BNB Smart Chain và Arbitrum One**. Chuỗi là một phần của mỗi địa chỉ
  liên hệ đã lưu và mỗi lần phê duyệt. Chuyển token cũng cần coin gốc của chuỗi đó để trả phí; báo giá
  hiển thị cả hai số tiền trong phạm vi giới hạn của bạn.
* **USDC trên Solana và Base chỉ dùng được cho kênh thanh toán.** Nó không được cung cấp cho các lần gửi
  thông thường. Phần hiển thị số dư chính của ví cho thấy coin gốc, không phải toàn bộ danh mục token.
* Mọi lần chuyển tiền đều được xem trước (một **báo giá**: số tiền, mức phí tối đa, người nhận, mạng) và
  cần sự phê duyệt tường minh của bạn gắn đúng với bản xem trước đó. Thay đổi bất cứ điều gì và phê duyệt
  sẽ mất hiệu lực — không có gì được ký.
* Một cái tên khớp với nhiều liên hệ hoặc địa chỉ ví sẽ yêu cầu bạn chọn. Hãy xem lại chính xác địa chỉ và
  chuỗi. Một địa chỉ bị thay đổi hoặc một liên hệ bị đổi tên sẽ làm lần xem xét cũ mất hiệu lực.
* Trên mainnet, chỉ một Ví VOOL được tạo trên thiết bị này mới gửi được. Một ví bên ngoài mà bạn đã kết
  nối hoặc một địa chỉ chỉ xem có thể nhận và hiển thị số dư; một lần gửi hoặc một khoản thanh toán qua
  kênh thanh toán từ đó sẽ bị từ chối trước khi bất kỳ phê duyệt nào được yêu cầu, và không có gì được ký.
* Hoạt động ghi lại các khoản thanh toán thực hiện qua VOOL và biên nhận của chúng. Đó không phải là lịch
  sử đầy đủ các khoản thanh toán đến. Ví này không cung cấp thao tác hoán đổi (swap) hay chuyển cầu
  (bridge).

## Một khoản thanh toán được phê duyệt như thế nào

1. VOOL chuẩn bị một đề xuất với số tiền chính xác và mức phí tối đa.
2. Bạn phê duyệt nó bằng PIN/mật khẩu (hoặc xác nhận sự hiện diện trên thiết bị nếu có).
3. Khoản thanh toán được ký và gửi đi, và một biên nhận được ghi lại.

Phê duyệt không chuyển từ đề xuất này sang đề xuất khác. Một PIN sai không ký gì cả. Quá nhiều lần thử
sai sẽ khóa ví một thời gian — có chủ ý, với thời gian chờ được nêu rõ.

## Thanh toán tự động x402

x402 là một giao thức thanh toán theo từng yêu cầu mà một số trang web và dịch vụ AI sử dụng. **Các trang
web yêu cầu thanh toán x402 bằng USDC trên Base** (hoặc Base Sepolia, trong Tùy chọn nhà phát triển) có
thể được thanh toán từ ví Crypto Pilot của bạn: một lần phê duyệt bằng PIN, trong phạm vi giới hạn của
bạn. Dịch vụ thanh toán của trang web chịu phí mạng. **Chưa có khoản thanh toán mainnet thật nào được thực
hiện theo cách này.** Các mạng thử nghiệm khác (Solana Devnet, Ethereum Sepolia và BNB testnet) vẫn ở
trong Tùy chọn nhà phát triển. **Cài đặt → Tiền điện tử → Payment channels** liệt kê mọi cách ví có thể
thanh toán cho một trang web, mỗi cách có công tắc bật/tắt riêng; một kênh đang tắt sẽ từ chối trước khi
bất cứ thứ gì được ký. Thanh toán qua Solana pay-kit không có trong các bản tải xuống, nên kênh đó hiện là
không dùng được. **Chợ mô hình:** tùy chọn trả tiền theo từng lệnh gọi trong chợ mô hình ở Cài đặt có thể thanh toán từ ví Crypto Pilot
Solana Mainnet bằng USDC hoặc SOL. Mỗi lệnh gọi chờ sự phê duyệt của bạn trong phạm vi giới hạn ví của
bạn và hiển thị phí VOOL 0,1%. Con đường đó chưa được thử nghiệm trên một chuỗi thật.
Khi một tài nguyên trả phí dùng x402:

* Các khoản thanh toán đi qua cùng các giới hạn ví và quy trình phê duyệt.
* Thanh toán tự động bị giới hạn theo từng khoản; một tài nguyên vượt quá giới hạn sẽ **không được thanh
  toán** (bạn có thể chủ động nâng giới hạn hoặc thanh toán qua một đề xuất tường minh).
* Nếu một khoản thanh toán không thể hoàn tất, kết quả được báo là **không xác định** — hãy kiểm tra trạng
  thái của nó trước khi thử lại; VOOL sẽ không mời bạn thanh toán trùng lặp một cách mù quáng.

## Phí dịch vụ DNA

Đường thanh toán x402 trả theo từng lệnh gọi của chợ mô hình tính phí dịch vụ **0,1% (10 điểm cơ bản)**
trên mỗi khoản thanh toán cho nhà cung cấp được chấp nhận, hiển thị tách biệt với khoản thanh toán và tách
biệt với phí mạng. Không có gì khác phát sinh phí này: các lần gửi thông thường, việc tạo ví, suy luận cục
bộ và các lệnh gọi bằng khóa API của chính bạn (BYOK) **không** có phí dịch vụ. Phí tích lũy được thu theo
cách tiết kiệm (cùng với một khoản thanh toán bằng coin gốc sau đó, theo một lần phê duyệt, chỉ khi chi
phí thu có giới hạn) — không bao giờ bằng một khoản phí riêng bất ngờ.

## Khôi phục

* **Cụm từ khôi phục chỉ được hiển thị đúng một lần**, khi tạo ví.
* Khóa sao lưu của ví thí điểm cũng chỉ được đưa ra một lần trong khi thiết lập, và sau đó không có cách
  xuất nào cho nó. Nếu bạn chưa lưu nó, hãy hủy thiết lập và đừng gửi tiền tới địa chỉ đó.
* Một Ví VOOL cũ hơn, được tạo trước Crypto Pilot, có thể xuất khóa riêng tư của nó một lần để nhập vào
  một ứng dụng ví khác. Việc xuất chỉ mở bằng thông tin đăng nhập của chính máy Mac của bạn (Touch ID
  hoặc mật khẩu Mac), không bao giờ bằng PIN của ví, và khóa được hiển thị một lần mà không được lưu lại.
* Bị mất ví? Hãy khôi phục từ cụm từ bạn đã ghi lại khi tạo ví. Một bằng chứng khôi phục không khớp đúng
  với ví này sẽ bị từ chối trước khi có bất kỳ thao tác ghi nào.

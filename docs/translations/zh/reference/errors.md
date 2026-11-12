---
description: 如何读懂 VOOL 的错误、是否已经发生了什么，以及每个代码在哪里有解释。
translation: machine
source_sha256: ccb01d4c778254a055fd67f46241c670aac90cfc83f168b923a19cef1f949de2
---

# 错误和错误代码

当某件事失败时，VOOL 会用平实的话说明发生了什么、可能已经发生了什么，以及下一步该怎么做。每次失败还有一个稳定的代码。你可以在错误下方的技术详情中、在命令行中（`wallet_export_refused: …`）以及在 API 响应中找到它。本页说明如何读懂一个错误。[错误手册](../ERROR_BOOK.md)解释了每一个代码。

## 读懂错误

在重试之前，先检查三件事：

1. **发生了什么。** 也就是消息本身。它绝不会包含内部路径或机密。
2. **可能已经发生了什么。** 大多数拒绝会在发送或更改任何内容之前停下，消息也会这样说明。有些失败无法知道某个操作是否已经完成，例如请求发出之后发生的超时。这时消息会说明结果未知，并请你在重试之前先检查。付款绝不会因意外而发送两次：同一笔付款的第二次发送会被 [`wallet_duplicate_payment`](../ERROR_BOOK.md#wallet-duplicate-payment) 拒绝。
3. **下一步该怎么做。** 一个具体的步骤。

含义由代码承载。像 403 或 429 这样的 HTTP 状态只说明请求在途中的情况，两种不同的拒绝也可能共用同一个状态。请看代码，而不是状态。

## 代码与错误手册

代码绝不会被重命名，也绝不会被另作他用。[错误手册](../ERROR_BOOK.md)由应用自己的错误目录生成，所以其中的代码、消息和后续步骤与应用显示的完全一致。每个代码都有自己的一节：按你看到的原样在页面中搜索该代码，或者用手册的地址、`#` 和把下划线换成连字符的代码来链接到该节，例如 [`wallet_broadcast_failed`](../ERROR_BOOK.md#wallet-broadcast-failed)。

你最可能看到的代码：

| 代码 | 标题 | 可能已经发生了什么 |
| --- | --- | --- |
| [`permission_denied`](../ERROR_BOOK.md#permission-denied) | 权限被拒绝 | 没有更改任何内容。 |
| [`provider_unavailable`](../ERROR_BOOK.md#provider-unavailable) | 无法连接提供方 | 没有发送任何内容。 |
| [`provider_exhausted`](../ERROR_BOOK.md#provider-exhausted) | 已达到提供方上限 | 请求已经到达提供方。 |
| [`timeout`](../ERROR_BOOK.md#timeout) | 已超时 | 未知：重试之前请先检查。 |
| [`cancelled`](../ERROR_BOOK.md#cancelled) | 已取消 | 未知：其中一部分可能已经运行。 |
| [`credential_failure`](../ERROR_BOOK.md#credential-failure) | 无法读取已存储的凭据 | 没有发送或更改任何内容。 |
| [`unknown`](../ERROR_BOOK.md#unknown) | 意外错误 | 未知：重试之前请先检查。 |
| [`wallet_quote_mismatch`](../ERROR_BOOK.md#wallet-quote-mismatch) | 批准与预览不再匹配 | 没有签署或发送任何内容。 |
| [`wallet_broadcast_failed`](../ERROR_BOOK.md#wallet-broadcast-failed) | 无法广播付款 | 未知：重试之前请先检查该付款的状态。 |
| [`wallet_duplicate_payment`](../ERROR_BOOK.md#wallet-duplicate-payment) | 付款已提出或已发送 | 没有发送任何新内容；之前的付款已有记录。 |

## 错误与其他结果

VOOL 会把这些区分开，措辞也是如此：

* **错误。** 某件事失败了。代码和消息会说明是什么，以及该怎么做。
* **等待你处理。** 有一个权限请求或付款批准尚未处理。什么都没有运行，在你回答之前也不会运行。见[权限](../concepts/permissions.md)。
* **被支出上限或价格保护拦下。** 拒绝发生在联系提供方之前，消息绝不会暗示已经询问过提供方。见[花费限额与价格保护](../guides/spending-limits.md)。
* **已取消。** 你或 VOOL 在它完成之前停止了它。其中一部分可能已经运行，所以再次开始之前，请先检查哪些内容发生了变化。
* **此处无法进行。** 这个版本或这台电脑无法做到，消息会点出哪些可以做到：例如 VOOL 无法端到端检查其方案的付款报价，或尚未安装的可选附加功能。
* **结果未知。** 该操作可能已经完成，也可能没有，例如广播始终没有得到确认的付款。请先检查其状态。VOOL 绝不会盲目地再次付款。

## 有自己代码的错误

VOOL 的一些部分会在发生失败的界面上，用自己的简短代码解释失败，这些代码不在错误手册中：附件（例如文件太大，或在粘贴的文件中发现凭据）、应用更新、项目文件夹、模型选择、支出上限、联系人、插件和技能、语音输入、已存储的数据，以及错误报告。它们的消息同样会说明发生了什么以及该怎么做。见[聊天与附件](../concepts/chats-and-attachments.md)、[更新与迁移](updates-migration.md)和[错误报告与隐私](../trust/bug-reporting.md)。

## 你不认识的代码

不在错误手册中的代码并非来自这个版本的 VOOL。请检查是哪个版本显示了它，然后在那个版本的[错误手册](../ERROR_BOOK.md)中搜索。如果它确实来自这个版本，请按原样报告该代码，如果响应中包含参考编号（`fault-` 加十二个字符），也请一并报告。VOOL 无法归类的失败会显示为 [`unknown`](../ERROR_BOOK.md#unknown)，绝不会显示为猜测的原因。见[错误报告与隐私](../trust/bug-reporting.md)。

## 排查具体症状

安装、模型和平台问题见[故障排除](../help/troubleshooting.md)；找回聊天、记忆和钱包见[备份与恢复](../help/backup-and-recovery.md)。

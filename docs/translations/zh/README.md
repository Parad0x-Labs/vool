---
description: VOOL 是一款桌面 AI 助手，可以在你的电脑上本地运行 AI 模型，也可以用你自己的密钥连接云端模型。免费且注重隐私。提供 Mac（Apple Silicon 和 Intel）和 Linux 版下载；Windows 为实验性支持，需从源码安装。
translation: machine
source_sha256: c31e5a047068e09c01bbd2e2c90cbbed0b70e78926847a5b737f9e0e625ab1dd
---

# VOOL

VOOL 是一款桌面 AI 助手。它作为应用程序安装在你的电脑上，可以在这台电脑上本地运行 AI 模型，也可以用你自己的 API 密钥连接云端 AI 提供方。它会为自己执行的每一项操作保留一份可读的记录。

它是免费的，使用本地模型时可以离线工作。提供 Mac（Apple Silicon 和 Intel）和 Linux x86_64
版下载；Windows 为实验性支持，需从源码安装。

本文档介绍如何安装 VOOL、如何连接模型，以及它如何处理你的文件、记忆和密钥的基本概念。

## 表现如何

在 150 道从未用过的 LoCoMo 记忆题上，每个智能体都由同一个模型作答、由同一个 GPT-4o 评分，
VOOL 的记忆胜过了另外两个开源智能体：

| | VOOL | OpenClaw | Hermes |
| --- | --- | --- | --- |
| 答对 | **82.7%**（124 / 150） | 71.3%（107） | 62.7%（94） |
| 每题 token 数 | **9.6k** | 51.8k | 33.5k |
| 每题成本 | **$0.0033** | $0.0100 | $0.0046 |

token 用量比 OpenClaw 少 5.4 倍、比 Hermes 少 3.5 倍，每次回答的成本只有 OpenClaw 的三分之一。
VOOL 0.7 自带的记忆在同样的题目上答对了 150 道中的 123 道。每次运行、测试设置和薄弱环节都已公开：[记忆基准测试](https://github.com/Parad0x-Labs/vool/tree/main/docs/benchmarks)。

## 从这里开始

| 如果你想 | 请阅读 |
| --- | --- |
| 让 VOOL 运行起来 | [安装](getting-started/install.md) |
| 看懂第一个界面 | [首次运行](getting-started/first-run.md) |
| 给它指定模型 | [连接模型](getting-started/connect-a-model.md) |
| 控制可能发生的花费 | [花费限额](guides/spending-limits.md) |
| 了解什么会离开你的电脑 | [本地与云端](concepts/local-and-cloud.md) |

## VOOL 是什么

* **一款桌面应用程序。** 它安装在你的电脑上，不是浏览器标签页，也不是托管服务。
* **可离线工作。** 选择本地模型后，VOOL 不需要联网。
* **默认本地。** 选择本地模型后，提示词和文件都在你的电脑上处理。
* **不绑定模型。** 本地运行时和云端提供方都可以选择。云端密钥由你自己提供，并且只保存在你的电脑上。
* **可以检查。** 每次工具调用都会连同输入和结果一起记录，事后可以回看整个运行过程。

{% hint style="warning" %}
VOOL 目前为测试版，支持 Apple Silicon Mac、Intel Mac 和 Linux x86_64。Mac 版应用尚未经过
Apple 公证，因此首次打开时 macOS 会请你确认；[安装](getting-started/install.md#在-mac-上首次打开)页面介绍了操作方法。安装前请先查看[发布状态](trust/release-status.md)。
{% endhint %}

## 两种模式，说得明明白白

VOOL 只区分两种状态，界面上始终显示当前是哪一种：

1. **本地** — 工作留在这台电脑上。
2. **云端** — 所选上下文从你的电脑出发、使用你的密钥，直接发送给你选择的提供方。

不存在第三种状态，中间也没有任何由 Parad0x 运营的中转服务器。详情见[数据处理](trust/data-handling.md)。

## 获取帮助

* [常见问题](help/faq.md) — 最常被问到的问题
* [故障排除](help/troubleshooting.md) — 有东西启动不了时
* [Discord](https://discord.gg/V9NkjP3Fzz) — 向团队提问

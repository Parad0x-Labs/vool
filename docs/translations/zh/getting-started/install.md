---
description: 在 macOS（Apple 芯片或 Intel）或 Linux 上下载并安装 VOOL 0.7.0-beta。Windows 为实验性支持，需从源码安装。
---

# 安装

## 系统要求

| | 最低 | 推荐 |
| --- | --- | ---: |
| 内存 | 8 GB | 16 GB 以上 |
| 磁盘 | 应用本身 2 GB | 含本地模型 20 GB |
| macOS | 14 Sonoma，Apple 芯片或 Intel | 14 Sonoma 或更新，Apple 芯片 |
| Linux | x86_64，glibc 2.31 | Ubuntu 22.04 或 24.04 |
| Windows | 实验性，仅从源码安装 | |

## 下载

所有文件都在 [v0.7.0-beta 发布页](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.0-beta)，每个文件旁都有 SHA-256 校验和。

| 你的电脑 | 下载 |
| --- | --- |
| Apple 芯片 Mac（M1 或更新） | [`VOOL-0.7.0-beta-macos-arm64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.0-beta/VOOL-0.7.0-beta-macos-arm64.dmg) |
| Intel Mac | [`VOOL-0.7.0-beta-macos-x86_64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.0-beta/VOOL-0.7.0-beta-macos-x86_64.dmg) |
| Linux x86_64 | [`vool-0.7.0-beta-linux-x86_64.tar.gz`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.0-beta/vool-0.7.0-beta-linux-x86_64.tar.gz) |

不确定是哪种 Mac？点击苹果菜单 → **关于本机**。显示 "Apple M…" 即为 Apple 芯片，显示 "Intel" 即为 Intel。

在 Intel Mac 上，本地模型只在 CPU 上运行，可能太慢，无法通过一次性的 Certify 检查或及时回答，因此目前请在 Intel Mac 上使用云端模型。Intel 版本是在 Apple 芯片 Mac 上通过 Rosetta 测试的，尚未在 Intel Mac 上测试。

## macOS 首次打开

1. 打开 DMG，将 **VOOL** 拖入 `Applications`。
2. 双击 VOOL。该版本尚未经过 Apple 公证，macOS 会提示无法检查该应用，点击**完成**。
3. 打开**系统设置 → 隐私与安全性**，向下滚动找到 VOOL 条目，点击**仍要打开**并输入密码确认。

在 macOS 14 上，也可以按住 Control 点按 VOOL，选择**打开**。只需操作一次。

## 本地模型

使用本地模型前，请打开**设置 → Models & Providers → Local models**，为该模型点击一次 **Certify**（认证）。0.7.0 不会主动提示这一步；0.7.1 将提供更清晰的提示。

## Linux

下载 tarball 及其 `.sha256` 文件，校验后运行自带的安装程序，无需 sudo：

```bash
sha256sum -c vool-0.7.0-beta-linux-x86_64.tar.gz.sha256
tar -xzf vool-0.7.0-beta-linux-x86_64.tar.gz
bash vool-0.7.0-beta-linux-x86_64/install.sh
```

## 校验下载

```bash
shasum -a 256 VOOL-0.7.0-beta-macos-arm64.dmg   # macOS
sha256sum vool-0.7.0-beta-linux-x86_64.tar.gz   # Linux
```

将结果与发布页上公布的校验和比对。不一致就删除文件重新下载。

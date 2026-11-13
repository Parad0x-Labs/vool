---
description: 如何通过发布版下载在 macOS（Apple Silicon 或 Intel）或 Linux 上安装 AI 助手 VOOL，或在 macOS、Windows、Linux 上从源码安装，包括系统要求以及如何校验下载的文件。
translation: machine
source_sha256: a674148a99297acd314681d6a04bcf2e87e1c88bf00c9e3f25cd22a4083f2177
---

# 安装

VOOL 0.7.7-beta 在 [v0.7.7-beta 发布版](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.7-beta)中提供适用于 Apple Silicon Mac、Intel Mac 和 Linux x86_64 的应用下载。在 macOS、Linux 或 Windows 上，你也可以用引导脚本从源码安装。签名和更新的详细信息见[发布状态](../trust/release-status.md)。

## 系统要求

| | 最低 | 推荐 |
| --- | ---: | ---: |
| 内存 | 8 GB | 16 GB 或更多 |
| 磁盘 | 应用本身 2 GB | 含本地模型 20 GB |
| macOS | 14 Sonoma，Apple Silicon 或 Intel（[限制](#intel-mac)） | 14 Sonoma 或更新版本，Apple Silicon |
| Windows | 10（64 位） | 11 |
| Linux | Ubuntu 22.04，x86_64 | Ubuntu 24.04 |

本地模型需要的内存比应用本身多得多。模型大小的建议见[运行本地模型](../guides/local-models.md)。

## 下载应用

| 你的电脑 | 下载 |
| --- | --- |
| 搭载 Apple Silicon 的 Mac（M1 及更新） | [`VOOL-0.7.7-beta-macos-arm64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-arm64.dmg) |
| 搭载 Intel 芯片的 Mac | [`VOOL-0.7.7-beta-macos-x86_64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-x86_64.dmg) |
| Linux x86_64（Ubuntu 22.04 / 24.04） | [`vool-0.7.7-beta-linux-x86_64.tar.gz`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/vool-0.7.7-beta-linux-x86_64.tar.gz) |

要查看你的 Mac 用的是哪种芯片，请打开苹果菜单并选取 **关于本机**。“Apple M…”表示 Apple Silicon，
“Intel”表示 Intel。Apple Silicon 版应用无法在 Intel Mac 上运行，打开时会提示这一点。

打开之前，请先核对下载文件的 SHA-256（见[校验下载文件](#校验下载文件)）。

### 在 Mac 上首次打开

测试版应用尚未经过 Apple 公证，因此首次打开时 macOS 会请你确认。这是预期行为，只会出现一次。

1. 打开 DMG，将 **VOOL** 拖到 **应用程序** 中。
2. 连按 VOOL。macOS 会提示它无法检查该应用是否包含恶意软件。关闭这条提示。
3. 打开 **系统设置 → 隐私与安全**。向下滚动到关于 VOOL 的提示，点按 **仍要打开**。输入你的密码进行确认。

在 macOS 14 Sonoma 上，你也可以在“应用程序”文件夹中按住 Control 键点按 VOOL，选取 **打开**，然后再点按一次 **打开**。

### Linux

从发布版下载 tar 包及其 `.sha256` 文件并进行校验，然后运行附带的安装程序。不需要 sudo，也不需要系统自带的 Python。

```bash
sha256sum -c vool-0.7.7-beta-linux-x86_64.tar.gz.sha256
tar -xzf vool-0.7.7-beta-linux-x86_64.tar.gz
bash vool-0.7.7-beta-linux-x86_64/install.sh
~/.local/bin/vool
```

VOOL 安装在 `~/.local/share/vool` 中，把 `vool` 命令放到 `~/.local/bin`，并添加一个应用程序菜单项。桌面版会在浏览器中打开聊天界面；关闭浏览器并不会停止 VOOL。要移除 VOOL，请运行 `bash ~/.local/share/vool/current/uninstall.sh`。你的聊天记录和设置会保留在你的用户资料目录中。

从 0.7.5 起，在打开 **自行联网** 时，Linux 下载版会在 VOOL 内部更新。新版本会安装在旧版本旁边。如果新版本无法正常启动，
VOOL 会自动切换回旧版本。

### 安装之后

在本地模型能够回答之前，请打开 **设置 → 模型与提供方 → 本地模型**，为该模型按一次 **Certify**（如果已经记录过一次检查，按钮会显示为 **Re-run certification**）。这会在你的电脑上运行一项检查；在没有显卡的电脑上，第一次检查可能需要几分钟。

从 0.7.5 起，VOOL 会自行更新：打开“自行联网”后，它会显示通知，只需按一下即可安装新版本。在 Mac 上从 0.7.4 或 0.7.3 更新：打开“自行联网”（设置 > 隐私与权限），然后按一次“检查更新”。在 Linux 上从 0.7.4 或更早版本，或在 Mac 上从 0.7.2 及更早版本更新：下载 0.7.7，直接覆盖安装到当前版本上。你的聊天记录和设置保存在应用之外的用户资料目录中。加密货币功能默认关闭。

## 从源码安装

运行适用于你的平台的引导脚本：

```bash
curl -fsSLo bootstrap_vool.sh https://raw.githubusercontent.com/Parad0x-Labs/vool/v0.7.7-beta/installer/bootstrap_vool.sh
bash bootstrap_vool.sh --ref v0.7.7-beta
```

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.ps1 -OutFile bootstrap_vool.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_vool.ps1
```

macOS / Linux 命令会把引导脚本及其源码下载都固定到 `v0.7.7-beta`。要选择其他发布版，请同时修改脚本 URL 中的标签和 `--ref`。引导脚本接受分支名或发布标签，不接受提交哈希。

从源码安装需要 Python 3.11 或更新版本。如果找不到，安装程序会下载一个并完成配置。应用下载版自带 Python。

安装程序会准备 Python 环境、检查你的硬件、配置本地模型并启动本地服务。首次下载模型需要一些时间和磁盘空间。配置完成后，本地模型推理可以离线工作；网络工具和云端模型需要联网。

## Intel Mac

VOOL 可以在装有 macOS 14 或更新版本的 Intel Mac 上运行（例如 2020 年的 13 英寸 MacBook Pro），使用 Intel 版下载或从源码安装均可。源码安装程序会检测 Intel CPU，并为每个依赖项安装 Intel 版本。以下两项限制是 Intel 特有的：

* **不支持本地模型训练。** 自 2.3 起，PyTorch 不再发布 Intel macOS 版本，因此安装程序会跳过它，
  LoRA 训练会显示为不可用。聊天、记忆、工具和云端模型不受影响。
* **本地模型只在 CPU 上运行。** 本地模型运行器对 Intel 显卡没有 GPU 加速，因此本地回答比在
  Apple Silicon 上慢。在 8 GB 内存的 Mac 上，请用云端模型聊天；在 16 GB 上，安装程序默认的本地模型（一个 Qwen 模型）可以工作，但速度较慢。

在 Intel Mac 上，本地模型在处理器上运行，速度较慢。较长的记忆类回答可能会超时，因此目前请在这些 Mac 上使用云端模型。本地模型认证已考虑到较慢的处理器。

Intel Mac 有单独的下载文件 `VOOL-0.7.7-beta-macos-x86_64.dmg`。Apple Silicon 版下载无法在
Intel 上运行，启动时会提示这一点。

## 校验下载文件

发布版中的每个下载文件旁边都附有 SHA-256 校验和。打开文件之前请先校验：

```bash
shasum -a 256 <downloaded-artifact>    # macOS
sha256sum <downloaded-artifact>         # Linux
```

```powershell
Get-FileHash -Algorithm SHA256 <downloaded-artifact>
```

将结果与该发布版一同公布的校验和进行比较。如果两者不同，请删除该文件并重新下载。

## 下一步

继续阅读[首次运行](first-run.md)。

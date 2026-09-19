---
description: 在 macOS、Windows 或 Linux 上下载并安装 VOOL。
---

# 安装

## 系统要求

| | 最低 | 推荐 |
| --- | --- | ---: |
| 内存 | 8 GB | 16 GB 以上 |
| 磁盘 | 应用本身 2 GB | 含本地模型 20 GB |
| macOS | 14 Sonoma，Apple 芯片 | 14 Sonoma 或更新 |
| Windows | 10（64 位） | 11 |
| Linux | glibc 2.31 | Ubuntu 22.04 或更新 |

## macOS

1. 从 [vool.dev](https://vool.dev/#cta) 下载 `VOOL-0.6.0-beta-cffc5c3-macos-arm64.dmg`。需要 Apple 芯片（M1 或更新）和 macOS 14 Sonoma 或更高版本。
2. 打开后将 **VOOL** 拖入 `Applications`。
3. 首次启动时 macOS 会拒绝打开，因为该版本未经公证。打开**系统设置 → 隐私与安全性**，找到 VOOL 条目，选择**仍要打开**。

## 校验下载

```bash
shasum -a 256 VOOL-0.6.0-beta-cffc5c3-macos-arm64.dmg
```

将结果与安装包旁公布的校验和比对：[VOOL-0.6.0-beta-cffc5c3-macos-arm64.dmg.sha256](https://vool.dev/downloads/VOOL-0.6.0-beta-cffc5c3-macos-arm64.dmg.sha256)。不一致就删除文件重新下载。

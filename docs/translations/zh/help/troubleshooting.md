---
description: 最常见问题的解决方法。
translation: machine
source_sha256: 809bae11bc4bf8c2d707cfef616f80056a5d98d35b0786cc937a40526e802689
---

# 故障排除

## macOS 拒绝打开应用

这是预期的：应用尚未经过公证。请先核对校验和，然后打开 **系统设置 → 隐私与安全**，点按 **仍要打开**。在 macOS 14 上，你也可以按住 Control 键点按 VOOL，然后选取 **打开**。

## Windows

本次发布没有 Windows 版下载。Windows 需要从源码安装，且为实验性支持；见[安装指南](../getting-started/install.md#从源码安装)。

## 没有列出任何本地模型

1. 确认运行时正在运行。
2. 确认至少已拉取一个模型。
3. 确认端口与 VOOL 预期的一致。

```bash
curl http://localhost:11434/api/tags
```

如果这条命令没有任何返回，说明运行时没有启动。

## 回答很慢

通常是模型大小相对可用内存来说太大了。见[运行本地模型](../../../guides/local-models.md)。

## 错误显示代码

每次失败都有一个固定的代码。[错误和错误代码](../../../reference/errors.md)说明如何读懂它，[错误手册](../../../ERROR_BOOK.md)则针对每个代码说明发生了什么、可能已经发生了什么，以及下一步该怎么做。

## 从头开始

退出 VOOL，并删除[配置](../../../reference/configuration.md)中列出的“runtime home”文件夹。这会删除你的聊天、记忆、设置、已保存的密钥，以及 VOOL 在这台电脑上保管的任何钱包，所以请先保存你需要的内容：见[备份与恢复](../../../help/backup-and-recovery.md)。工作区中的文件不会受影响。

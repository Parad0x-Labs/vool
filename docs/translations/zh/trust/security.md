---
description: VOOL 如何保护你的文件和 API 密钥——工作区边界、凭据存储，以及插件能触及的范围。
translation: machine
source_sha256: 4397ca0e7b494efacb90aa546eae10800b9a58558ba7c611c0131f4410c2fe3a
---

# 安全模型

## 边界

* **文件夹限制** 约束文件访问。VOOL 在聊天的项目文件夹中读取和更改文件。在项目文件夹之外，VOOL 可以读取你可见的个人文件夹，但只能在 Desktop、Downloads 和 Documents 中更改文件；隐藏文件夹、应用和系统数据以及 VOOL 自己的数据绝不会被读取。见[工作区](../concepts/workspaces.md)。
* **凭据存储** 保管密钥。密钥不会被写入配置文件或日志。
* **工具确认** 约束更改。在“手动”模式下，每次更改都会先询问；在“自动”模式下，日常编辑和命令可以自动运行，而删除、覆盖、安装、花费和更改设置仍会询问。见[工具](../concepts/tools.md)。

## 你的局域网

VOOL 默认只在这台电脑上监听。当你把它设置为在局域网上监听时，只有这台电脑会被视为你本人。设置、文件、密钥、工具和私密功能只响应这台电脑。

网络上的其他设备只能进行普通聊天。它们还可以一次读取一个公开网页。该网页必须位于公共互联网上：这台电脑或你局域网上的地址会被拒绝，每次重定向也会以同样方式检查。网页搜索、研究、浏览器、报价、付款和你的付费密钥都只能在这台电脑上使用。

## Discord 桥接

Discord 桥接只回应它的主人。第一个向它发送命令的 Discord 用户会成为主人。要自己选择主人，请把 `VOOL_DISCORD_OWNER_USER_ID` 设置为你的 Discord 用户 ID；在任何其他人可以发言的频道中都要这样做。其他所有人都会被告知一次：这个 VOOL 已锁定给它的主人，他们的消息不会被处理。即使是主人的 Discord 消息，也会受到与网络上其他设备相同的限制。

## 密钥

VOOL 把你的提供方密钥保存在这台电脑上其运行时主目录中的一个加密文件里（见[配置](../reference/configuration.md)），只有你的用户账户可以读取。在 Mac 上，VOOL 之前已经保存在 macOS 钥匙串中的密钥仍然可以在那里读取。密钥从你的电脑使用，用于访问你的提供方。它不会被发送给 Parad0x Labs，请求路径中也没有任何由 Parad0x 运营的端点。

{% hint style="info" %}
这个加密文件受你用户账户的文件权限保护，而不是靠一个单独的密码。请保持磁盘加密开启（Mac 上为 FileVault），这样磁盘丢失或被盗时也不会暴露你的密钥。
{% endhint %}

## 插件

插件以运行时的访问权限运行。这正是插件有用的原因，也是它们值得在安装之前仔细阅读的原因。见[技能与插件](../guides/skills-and-plugins.md)。

## 报告问题

请通过 [GitHub Security Advisories](https://github.com/Parad0x-Labs/vool/security/advisories/new) 私下报告安全问题，绝不要在公开的 issue 中报告。请附上版本、平台和复现步骤。

{% hint style="warning" %}
macOS 应用是 ad-hoc 签名的，未经公证；Linux tar 包没有签名。见[发布状态](release-status.md)。
{% endhint %}

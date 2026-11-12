---
description: AIアシスタントVOOLを、リリースのダウンロードからmacOS（Apple SiliconまたはIntel）やLinuxに、またはソースからmacOS、Windows、Linuxにインストールする方法。システム要件とダウンロードの検証方法も説明します。
translation: machine
source_sha256: e0025d89fc56caca91923de6f1e6afe25ba6f6383a300ec3df5a7f7e3f401e55
---

# インストール

VOOL 0.7.7-betaには、Apple Silicon搭載Mac、Intel搭載Mac、Linux x86_64向けのアプリのダウンロードが
[v0.7.7-betaリリース](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.7-beta)にあります。
macOS、Linux、Windowsでは、ブートストラップスクリプトを使ってソースからインストールすることもできます。署名とアップデートの詳細は[リリース状況](../../../trust/release-status.md)をご覧ください。

## 動作環境

| | 最小 | 快適 |
| --- | ---: | ---: |
| メモリ | 8 GB | 16 GB以上 |
| ディスク | アプリに2 GB | ローカルモデル込みで20 GB |
| macOS | 14 Sonoma、Apple SiliconまたはIntel（[制限](#intel搭載mac)） | 14 Sonoma以降、Apple Silicon |
| Windows | 10（64ビット） | 11 |
| Linux | glibc 2.31 | Ubuntu 22.04以降 |

ローカルモデルは、アプリ本体よりかなり多くのメモリを必要とします。サイズの目安は[ローカルモデルを実行する](../../../guides/local-models.md)にあります。

## アプリをダウンロードする

| お使いのコンピュータ | ダウンロード |
| --- | --- |
| Apple Silicon搭載Mac（M1以降） | [`VOOL-0.7.7-beta-macos-arm64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-arm64.dmg) |
| Intelチップ搭載Mac | [`VOOL-0.7.7-beta-macos-x86_64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-x86_64.dmg) |
| Linux x86_64（Ubuntu 22.04 / 24.04） | [`vool-0.7.7-beta-linux-x86_64.tar.gz`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/vool-0.7.7-beta-linux-x86_64.tar.gz) |

お使いのMacのチップを確認するには、Appleメニューを開いて **このMacについて** を選びます。「Apple M…」なら
Apple Silicon、「Intel」ならIntelです。Apple Silicon版のアプリはIntel搭載Macでは動作せず、開くとその旨が表示されます。

開く前に、ダウンロードしたファイルのSHA-256を確認してください（[ダウンロードを検証する](#ダウンロードを検証する)を参照）。

### Macで初めて開くとき

ベータ版のアプリはまだAppleの公証を受けていないため、初めて開くときにmacOSが確認を求めます。これは想定どおりの動作で、一度だけです。

1. DMGを開き、**VOOL** を **アプリケーション** にドラッグします。
2. VOOLをダブルクリックします。macOSが、このアプリに悪質なソフトウェアが含まれていないことを確認できないと表示します。メッセージを閉じます。
3. **システム設定 → プライバシーとセキュリティ** を開きます。下にスクロールしてVOOLに関するメッセージを探し、**このまま開く** をクリックします。確認のためパスワードを入力します。

macOS 14 Sonomaでは、代わりにアプリケーションフォルダでControlキーを押しながらVOOLをクリックし、**開く** を選んでから、もう一度 **開く** をクリックする方法もあります。

### Linux

リリースからtarballとその `.sha256` ファイルをダウンロードして検証し、同梱のインストーラを実行します。sudoもシステムのPythonも必要ありません。

```bash
sha256sum -c vool-0.7.7-beta-linux-x86_64.tar.gz.sha256
tar -xzf vool-0.7.7-beta-linux-x86_64.tar.gz
bash vool-0.7.7-beta-linux-x86_64/install.sh
~/.local/bin/vool
```

VOOLは `~/.local/share/vool` にインストールされ、`vool` コマンドを `~/.local/bin` に置き、アプリケーションメニューに項目を追加します。デスクトップ版はブラウザでチャットを開きますが、ブラウザを閉じてもVOOLは止まりません。VOOLを削除するには `bash ~/.local/share/vool/current/uninstall.sh` を実行します。チャットと設定はプロファイルに残ります。

0.7.5以降、Linux版はVOOLの中からアップデートされます。新しいバージョンは古いバージョンの隣にインストールされます。正しく起動しなかった場合、VOOLは自動で元のバージョンに戻ります。

### インストール後

ローカルモデルが回答できるようにするには、**設定 → モデルとプロバイダ → ローカルモデル** を開き、そのモデルの **Certify** を一度押してください。お使いのマシンで短いチェックが実行されます。

0.7.5以降、VOOLは自動でアップデートします。「自動でオンラインに接続」がオンなら、お知らせを表示し、ワンクリックで新しいバージョンをインストールします。Macで0.7.4または0.7.3からアップデートする場合は、「自動でオンラインに接続」（設定 > プライバシーと権限）をオンにして、「更新を確認」を一度押してください。Linuxで0.7.4以前、またはMacで0.7.2以前からの場合は、0.7.7をダウンロードして現在のバージョンに上書きインストールしてください。チャットと設定はアプリの外にあるプロファイルに保存されています。暗号資産の機能は標準ではオフです。

## ソースからインストールする

お使いのプラットフォーム用のブートストラップスクリプトを実行します。

```bash
curl -fsSLo bootstrap_vool.sh https://raw.githubusercontent.com/Parad0x-Labs/vool/v0.7.7-beta/installer/bootstrap_vool.sh
bash bootstrap_vool.sh --ref v0.7.7-beta
```

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.ps1 -OutFile bootstrap_vool.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_vool.ps1
```

macOS / Linux用のコマンドは、ブートストラップスクリプトとそのソースのダウンロードの両方を `v0.7.7-beta` に固定します。別のリリースを選ぶには、スクリプトのURL内のタグと `--ref` の両方を変更してください。ブートストラップが受け付けるのはブランチ名かリリースタグで、コミットハッシュは使えません。

ソースからのインストールにはPython 3.11以降が必要です。見つからない場合、インストーラがセットアップを試みます。アプリのダウンロード版には専用のPythonが含まれています。

インストーラはPython環境を準備し、ハードウェアを確認し、ローカルモデルをセットアップして、ローカルのサービスを起動します。最初のモデルのダウンロードには時間とディスク容量が必要です。セットアップ後、ローカルモデルによる推論はオフラインで動作します。ウェブツールとクラウドモデルには接続が必要です。

## Intel搭載Mac

VOOLは、macOS 14以降のIntel搭載Mac（たとえば2020年の13インチMacBook Pro）で、
Intel版のダウンロードまたはソースから動作します。ソース版のインストーラはIntel CPUを検出し、すべての依存関係のIntel版をインストールします。Intelに固有の制限が次の二つです。

* **ローカルモデルの学習はできません。** PyTorchは2.3以降、Intel版macOS向けのビルドを公開していないため、インストーラはこれをスキップし、LoRAの学習は利用不可と表示されます。チャット、メモリー、ツール、クラウドモデルには影響しません。
* **ローカルモデルはCPUのみで動作します。** ローカルモデルの実行環境にはIntelグラフィックス向けのGPUアクセラレーションがないため、ローカルでの回答はApple Siliconより数倍遅くなります。8 GBのMacではチャットにクラウドモデルを使ってください。16 GBなら、インストーラ標準の小さなローカルモデルは動作しますが、遅くなります。

Intel搭載Macでは、ローカルモデルはプロセッサ上で動作するため遅くなります。メモリーを使う長い回答はタイムアウトすることがあるため、当面はクラウドモデルを使ってください。ローカルモデルの認定は、遅いプロセッサも考慮しています。

Intel搭載Mac用には専用のダウンロード `VOOL-0.7.7-beta-macos-x86_64.dmg` があります。Apple Silicon用のダウンロードはIntelでは動作せず、起動時にその旨が表示されます。

## ダウンロードを検証する

リリースの各ダウンロードには、隣にSHA-256チェックサムがあります。ファイルを開く前に検証してください。

```bash
shasum -a 256 <downloaded-artifact>    # macOS
sha256sum <downloaded-artifact>         # Linux
```

```powershell
Get-FileHash -Algorithm SHA256 <downloaded-artifact>
```

結果を、そのリリースと一緒に公開されているチェックサムと比べてください。一致しない場合は、ファイルを削除してもう一度ダウンロードしてください。

## 次へ

[初回起動](first-run.md)に進みます。

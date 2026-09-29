# WeChat CLI

一个免费、在本机运行的微信数据命令行工具。

> **本地发行候选版 0.3.0rc1。**公开源码仓库：[Puss-M/wechat-cli](https://github.com/Puss-M/wechat-cli)。候选版尚未发布到 PyPI 或 npm；npm 当前可安装的版本是 0.2.4，不包含朋友圈命令。

## 导出自己的朋友圈

朋友圈命令读取当前电脑微信缓存的数据。它会根据本机微信数据目录和联系人记录识别当前账号，再与每条朋友圈记录的作者核对；无法确认账号时会停止导出，不会导出其他作者的帖子。输出可包含文字、时间、图片地址、地点和分享链接信息。实际范围受微信本机缓存影响。

### 环境与安装

- 本候选版首发目标为 Windows + 微信 4.x。
- 需要 Python 3.10 或更新版本；候选版使用 Python 3.12 验证。
- 其他系统、微信版本和电脑还没有独立验证。

解压候选源码 ZIP，在 PowerShell 中进入源码目录后运行：

~~~powershell
py -3.12 -m pip install .
wechat-cli --version
wechat-cli --help
wechat-cli moments --help
~~~

本工具免费并采用 Apache-2.0 许可。每位使用者都在自己的电脑上安装，并读取自己的微信数据。安装时会下载普通 Python 依赖；微信数据在本机处理。

首次导出前，请启动 Windows 微信并运行 `wechat-cli init`。该命令会检测本机微信数据库，并从运行中的 `Weixin.exe` 进程读取信息以提取数据库密钥，然后把配置和密钥保存在 `~/.wechat-cli/`。完成初始化后才能运行朋友圈命令。

### 导出文字和元数据

~~~powershell
wechat-cli moments --format json --output .\my-moments.json
wechat-cli moments --format markdown --output .\my-moments.md
~~~

为避免覆盖文件，输出文件已存在时命令会停止。再次导出前请更换文件名或自行移走旧文件。

### 保存可查看的图片

有两种可选方式：

- --download-images 会向当前账号自己朋友圈记录中的图片地址发起网络请求。
- --decode-images 离线读取微信本机图片缓存，只处理能由自己帖子 ID 和图片 ID 映射的缓存文件。

先下载一张验证：

~~~powershell
wechat-cli moments --download-images --download-limit 1 --image-output .\my-moment-images --format json --output .\my-moments-with-images.json
~~~

离线解码缓存图片：

~~~powershell
wechat-cli moments --decode-images --auto-image-key --image-limit 1 --image-output .\my-own-images
~~~

密钥会优先根据本机元数据和映射到自己帖子的图片缓存推导。如果微信版本不适用该规则，可选用本机 wx_key 扩展，或通过 --image-key-file 指定密钥。密钥探测不会读取其他朋友圈图片。输出目录中会生成图片和 manifest 清单，记录成功与失败的文件。

确认样例图片正常后，去掉数量限制即可处理所有可映射的图片。缓存不存在、记录缺少图片 ID 或帖子未缓存时，图片可能无法导出。

### 其他命令

候选版也保留原有的聊天记录、联系人、收藏和统计命令。运行 wechat-cli --help 可查看命令列表。本候选版新增的验证主要针对朋友圈命令。

## 隐私与使用

默认导出在本机离线完成。只有主动使用 --download-images 时，工具才会请求自己帖子里的图片地址。本工具不会发送、修改、点赞或评论微信消息。请只处理自己有权访问的数据，并遵守适用法律和微信相关条款。

## 许可

Apache-2.0，详见 LICENSE。现有数据库支持基于 [wechat-decrypt](https://github.com/ylytdeng/wechat-decrypt)。

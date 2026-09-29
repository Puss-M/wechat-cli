# WeChat CLI

A free, local command-line tool for your own WeChat data.

> **Local release candidate 0.3.0rc1.** The public source repository is [Puss-M/wechat-cli](https://github.com/Puss-M/wechat-cli). This candidate has not been published to PyPI or npm. The npm release currently available is 0.2.4 and does not contain the Moments command.

## Moments export

The Moments command reads the current account data cached on this computer. It identifies the account from the local WeChat data directory and contact records, then checks each timeline record's author. It stops if it cannot confirm the account and does not export other authors' posts. Results can include text, time, image URLs, locations, and shared-link metadata. Coverage depends on what WeChat has cached locally.

### Requirements and installation

- Windows with WeChat 4.x is the target for this candidate.
- Python 3.10 or later is required. Python 3.12 was used for candidate validation.
- Other operating systems, WeChat versions, and computers have not been independently verified.

Extract the source candidate ZIP, open PowerShell in its folder, and run:

~~~powershell
py -3.12 -m pip install .
wechat-cli --version
wechat-cli --help
wechat-cli moments --help
~~~

The CLI is free under Apache-2.0. Each user installs it on their own computer and reads their own WeChat data. Installation downloads ordinary Python packages; WeChat data is processed locally.

Before the first data export, start WeChat for Windows and run `wechat-cli init`. It detects the local WeChat database and reads the running `Weixin.exe` process to derive database keys, then saves the configuration and keys under `~/.wechat-cli/`. This step is required before running the Moments command.

### Export text and metadata

~~~powershell
wechat-cli moments --format json --output .\my-moments.json
wechat-cli moments --format markdown --output .\my-moments.md
~~~

The command refuses to overwrite an existing output file. Rename or remove the old export before running the same command again.

### Save pictures so they can be viewed

There are two optional methods:

- --download-images makes network requests to image URLs found in verified posts from the current account.
- --decode-images works offline with local WeChat image cache files whose names map to the current account’s own post and image IDs.

Download one image first:

~~~powershell
wechat-cli moments --download-images --download-limit 1 --image-output .\my-moment-images --format json --output .\my-moments-with-images.json
~~~

For offline cache decoding:

~~~powershell
wechat-cli moments --decode-images --auto-image-key --image-limit 1 --image-output .\my-own-images
~~~

The decoder derives the key from local metadata and cache files mapped to the current account’s own posts. If the installed WeChat build uses a different scheme, it can optionally use the local wx_key extension or a key supplied with --image-key-file. Key discovery does not read other Moments image files. Output includes a manifest listing successful and failed files.

After confirming a sample image, remove the limit to process all matching images. A missing cache file, missing image ID, or uncached post can result in an image being omitted.

### Other commands

The candidate also contains the existing chat, contact, favorites, and statistics commands. Run wechat-cli --help to list them. This candidate’s additional verification focuses on the Moments command.

## Privacy and use

By default, export is local and offline. The optional --download-images flag sends requests to the image URLs in your own posts. The tool does not send, modify, like, or comment on WeChat messages. Use it only with data you are authorized to access and follow the applicable laws and WeChat terms.

## License

Apache-2.0. See LICENSE. The existing database support builds on [wechat-decrypt](https://github.com/ylytdeng/wechat-decrypt).

# PaddleOCR 官方 API：30 页批次、3 页并发、断点续跑

本地不运行模型。使用官方 `paddleocr.AsyncPaddleOCRClient` 调用
`PaddleOCR-VL-1.6` 的 spotting 模式，为原 PDF 添加可搜索、可复制的隐藏原文文字层。

## 运行

```bash
cd pdf_ocr
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp config.example.json config.json
# 在 config.json 中填写 token
python pdf_ocr.py "输入.pdf"
```

中断后再次运行同一命令即可续跑。最终结果在程序打印的工作区内：`已完成.pdf`。
也可指定新的外部输出路径：

```bash
python pdf_ocr.py "输入.pdf" "输出_可搜索.pdf"
```

只复制原件并切分、不调用 API：

```bash
python pdf_ocr.py "输入.pdf" --prepare-only
```

## 工作区

根目录默认为 `ocr_workspace/`，可用 `--workspace 路径` 修改。
以输入文件的 SHA-256 内容哈希为子目录名；相同内容即使改名也复用同一目录。

- `原件.pdf`：原 PDF 的完整副本，统一名称，不修改。
- `已完成.pdf`：已完整通过的批次，按原顺序合并。
- `工作.pdf`：当前最多 30 页，逐页完成后替换为带文字层的页。
- `未处理.pdf`：当前批次之后的剩余页面。
- `进度.json`：批次进度和首次运行参数。
- `pages/`：每页的 API 任务编号、识别结果及已处理 PDF。

开始时没有已完成页，因此不生成零页的 `已完成.pdf`。
没有剩余页时不生成 `未处理.pdf`；全部完成后只保留 `已完成.pdf` 和原件、缓存。
PDF 不支持本程序保存零页文件。

每批 30 页（不足则处理到末尾），最多 3 页同时请求 API。
整批成功后，推进批次进度、合并到已完成并抽取下一批。
PDF 操作在一个事件循环中串行执行，仅 API 上传和等待并发。

## 恢复与限制

- 每页提交后立即保存任务编号；重启优先查询原任务，不重新提交。
- 识别结果先保存，再写文字层；中断后可复用结果。
- 页面文件和进度文件使用临时文件加原子替换，派生的三个 PDF 可从原件和逐页缓存重建。
- Ctrl+C 后进度保留；再次运行同一命令恢复。不支持同时运行同一文件的两个工作流。
- 部分页失败时保存本批其他成功页，停止进入下一批；已失败/过期的服务器任务需要人工处理缓存或另开工作区，避免自动重复计费。
- 网络读取错误有限重试同一个任务；提交请求不自动重试，限流/配额耗尽时停止。
- 服务端接受请求后、任务编号落盘前若进程被杀，仍可能重复提交，不能保证绝对一次调用。
- 续跑的 `--dpi`、`--max-new-tokens`、`--force` 必须与首次一致；变更可另设工作区。
- 已有文字的页面默认跳过 API。混合页面若也要识别扫描区域，首次运行加 `--force`，可能有重复文字。
- `--poll-timeout 600` 设置每页等待秒数。`--max-new-tokens 8192` 设置 VL 生成上限。
- 零文字框页面保存原页并打印提示；应核查空白或识别失败情况。
- 隐藏文字按轴对齐包围框放置，倾斜、竖排、弯曲文字选择位置可能不准确。
- API 必须返回 `spotting_res`；缺少坐标明确报错，不用 Markdown 猜测位置。
- 拆分合并面向页面内容，目录书签、文档级表单和签名不会完整保留。
- 页面图片上传到官方服务，消耗 API 配额。令牌从外置 config.json 读取后提供给 SDK，不写入工作区。

依赖安装：`python -m pip install -U -r requirements.txt`。
令牌：https://aistudio.baidu.com/account/accessToken
SDK：https://www.paddleocr.ai/main/version3.x/inference_deployment/serving/paddleocr_official_api/python.html

验证：`python -m unittest test_workflow.py`。使用模拟 API 检查并发、切分、续跑和合并顺序，不消耗配额。
本次优先实现命令行工作流，拖放应用尚未打包。

## Mac 应用与 token 配置

打包结果为 `dist/PDF-OCR.app`（Apple Silicon / arm64）。
把 `PDF-OCR.app` 和同目录的 `config.json` 一起放在可写文件夹中。
双击应用，点击“设置 API Token”保存令牌，再选择 PDF 开始；
也可以把 PDF 拖到 Finder 中的应用图标上。
不支持将文件直接拖入窗口里的日志文本区域。

配置文件内容：

```json
{
  "token": "你的 AI Studio Access Token"
}
```

应用不内嵌 token。首次没有令牌时不会自动请求 API。
工作区保存到 `config.json` 所在目录的 `ocr_workspace/`。
运行中关闭应用会中断任务，已保存的页和任务编号在下次复用。

命令行默认读取脚本旁的 `config.json`，也可指定 `--config 路径`。
JSON 中 token 明文保存，请保留在自己的电脑上，不分享配置文件。

重新打包：

```bash
python -m pip install pyinstaller
python -m PyInstaller --noconfirm PDF-OCR.spec
```

应用使用本机临时签名，不包含 Apple Developer ID 签名和公证。

## 可调批次和并发

界面提供每批页数（默认 30）和并发数（默认 3），均须为正整数。运行时输入框暂时禁用。
命令行也可用 `--batch-size 30 --concurrency 3`。并发修改可用于续跑；已有当前批次的页数保持原边界。
程序文件集中在 `PDF-OCR-程序/`，原输入文档保留在上级目录。图标源脚本为 `make_icon.py`。

## 下载

Mac 应用见 [GitHub Releases](https://github.com/sdloghub/pdf_ocr/releases)。GitHub Actions 自动测试、打包并发布应用。发布包只带空白 token 模板，不包含 OCR 工作区。

## 输出模式

界面可选择完整 PDF（默认）或分卷 PDF。分卷上限严格为 32,000,000 字节（32 MB），按最终导出文件大小检查，保持页序和文字层。分卷存放于工作区 `分卷_32MB/`，附带页码范围和校验清单。完整结果仍保留在工作区供恢复及重新导出；切换输出方式不重新调用 OCR。单页本身超过上限时明确报错，保留完整结果，不会悄悄降低画质。
命令行：`python pdf_ocr.py 输入.pdf --output-mode split32`。

#!/usr/bin/env python3
"""调用 PaddleOCR 官方 API（VL-1.6），为 PDF 添加原文隐藏文字层。"""
import argparse
import json
import math


def add_text_layer(page, result, image_width, image_height):
    import pymupdf

    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict):
        raise RuntimeError("API 未返回可用于定位文字的 prunedResult。")
    result = result.get("res", result)
    spotting = result.get("spotting_res")
    if spotting is None:
        raise RuntimeError("API 未返回 spotting_res。请确认官方服务支持 prompt_label='spotting'，不能仅靠 Markdown 精确生成文字层。")
    texts, polygons = spotting["rec_texts"], spotting["rec_polys"]
    if len(texts) != len(polygons):
        raise RuntimeError("spotting 文字和坐标数量不一致。")
    sx, sy = page.rect.width / image_width, page.rect.height / image_height
    font = pymupdf.Font("china-s")
    # 嵌入同一字体，确保测量宽度与实际 PDF 字形宽度一致。
    page.insert_font(fontname="ocrfont", fontbuffer=font.buffer)
    count = 0
    for text, polygon in zip(texts, polygons):
        text = str(text).replace("\n", " ").replace("\r", " ").strip()
        if not text:
            continue
        points = [(float(x), float(y)) for x, y in polygon]
        if len(points) < 3 or not all(math.isfinite(v) for p in points for v in p):
            raise RuntimeError("无效的 spotting 坐标。")
        x0, y0 = min(p[0] for p in points), min(p[1] for p in points)
        x1, y1 = max(p[0] for p in points), max(p[1] for p in points)
        rect = pymupdf.Rect(x0 * sx, y0 * sy, x1 * sx, y1 * sy) & page.rect
        if rect.is_empty:
            continue
        fontsize = rect.height / (font.ascender - font.descender)
        width = font.text_length(text, fontsize=fontsize)
        if width <= 0:
            continue
        baseline = pymupdf.Point(rect.x0, rect.y0 + font.ascender * fontsize)
        page.insert_text(
            baseline, text, fontname="ocrfont", fontsize=fontsize,
            render_mode=3,  # 不可见，但可搜索和复制
            morph=(baseline, pymupdf.Matrix(rect.width / width, 1)), overlay=True,
        )
        count += 1
    return count


def convert(*args, **kwargs):
    from ocr_workflow import convert as run
    return run(*args, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_pdf")
    parser.add_argument("--config", help="token 配置文件，默认脚本旁 config.json")
    parser.add_argument("output_pdf", nargs="?", help="可选最终输出路径")
    parser.add_argument("--output-mode", choices=["single", "split32"], default="single", help="完整 PDF 或每份不超过 32 MB 的分卷")
    parser.add_argument("--workspace", default="ocr_workspace", help="持久化工作区根目录")
    parser.add_argument("--prepare-only", action="store_true", help="只复制切分，不调用 API")
    parser.add_argument("--batch-size", type=int, default=30, help="每批 PDF 页数")
    parser.add_argument("--concurrency", type=int, default=3, help="同时处理的 API 页数")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--poll-timeout", type=float, default=600, help="每页等待 API 完成的最长秒数")
    parser.add_argument("--max-new-tokens", type=int, default=8192)
    parser.add_argument("--force", action="store_true", help="也识别已有文字的页面，可能产生重复文字")
    args = parser.parse_args()
    try:
        settings = vars(args)
        config = settings.pop("config")
        if not args.prepare_only:
            from app_config import load_token
            load_token(config)
        convert(**settings)
    except KeyboardInterrupt:
        parser.exit(130, "已停止。再次运行同一命令即可续跑。\n")
    except (ValueError, FileNotFoundError, FileExistsError, RuntimeError) as exc:
        parser.exit(1, f"错误：{exc}\n")


if __name__ == "__main__":
    main()

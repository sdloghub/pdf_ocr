"""可恢复的 30 页批次、3 页 API 并发工作流。"""
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil

import pymupdf

from pdf_ocr import add_text_layer


def atomic_json(path, value):
    tmp = path.with_suffix('.json.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save_pdf(doc, path):
    tmp = path.with_suffix('.tmp.pdf')
    doc.save(tmp, garbage=4, deflate=True)
    os.replace(tmp, path)


def page_file(work, index, suffix):
    return work / 'pages' / f'{index + 1:06d}.{suffix}'


def snapshot(work, state):
    """派生 PDF 可随时从不可变原件和逐页缓存重建。"""
    start, total = state['completed'], state['total']
    end = min(start + state.get('batch_size', 30), total)
    with pymupdf.open(work / '原件.pdf') as original:
        for name, first, last, use_cache in (
            ('已完成.pdf', 0, start, True),
            ('工作.pdf', start, end, True),
            ('未处理.pdf', end, total, False),
        ):
            path = work / name
            if first == last:
                path.unlink(missing_ok=True)  # PDF 不能保存零页文档
                continue
            with pymupdf.open() as out:
                for index in range(first, last):
                    cached = page_file(work, index, 'pdf')
                    if use_cache and cached.exists():
                        with pymupdf.open(cached) as page_doc:
                            out.insert_pdf(page_doc)
                    else:
                        out.insert_pdf(original, from_page=index, to_page=index)
                save_pdf(out, path)


async def process_batch(work, state, client, options):
    semaphore = asyncio.Semaphore(state.get('concurrency', 3))
    start = state['completed']
    end = min(start + state.get('batch_size', 30), state['total'])

    async def process(index):
        async with semaphore:
            cached = page_file(work, index, 'pdf')
            if cached.exists():
                print(f'第 {index + 1} 页：使用已保存页面', flush=True)
                return
            checkpoint = page_file(work, index, 'json')
            record = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
            with pymupdf.open(work / '原件.pdf') as original, pymupdf.open() as one:
                one.insert_pdf(original, from_page=index, to_page=index)
                page = one[0]
                rotation = page.rotation
                page.set_rotation(0)
                if not state['settings']['force'] and page.get_text().strip():
                    page.set_rotation(rotation)
                    save_pdf(one, cached)
                    snapshot(work, state)
                    print(f'第 {index + 1} 页：已有文字，保存并跳过 API', flush=True)
                    return
                if 'result' not in record:
                    if 'job_id' not in record:
                        image = page_file(work, index, 'png')
                        pix = page.get_pixmap(dpi=state['settings']['dpi'], colorspace=pymupdf.csRGB, alpha=False)
                        pix.save(image)
                        job = await client.submit_document_parsing(
                            model='PaddleOCR-VL-1.6', file_path=str(image), options=options,
                        )
                        record = {'job_id': job.job_id, 'width': pix.width, 'height': pix.height}
                        atomic_json(checkpoint, record)
                        image.unlink(missing_ok=True)
                        print(f'第 {index + 1} 页：任务已提交并保存', flush=True)
                    else:
                        print(f'第 {index + 1} 页：继续等待已提交任务', flush=True)
                    # 仅重试读取同一任务，不因超时重新提交。
                    for attempt in range(3):
                        try:
                            result = await client.wait_document_parsing_result(record['job_id'])
                            break
                        except Exception as exc:
                            if type(exc).__name__ not in ('NetworkError', 'RequestTimeoutError', 'ServiceUnavailableError') or attempt == 2:
                                raise
                            await asyncio.sleep(2 ** (attempt + 1))
                    if len(result.pages) != 1:
                        raise RuntimeError(f'第 {index + 1} 页：API 返回页数异常')
                    record['result'] = result.pages[0].pruned_result
                    atomic_json(checkpoint, record)
                count = add_text_layer(page, record['result'], record['width'], record['height'])
                if count == 0:
                    print(f'第 {index + 1} 页：返回零个文字框，请核查此页', flush=True)
                page.set_rotation(rotation)
                save_pdf(one, cached)
            snapshot(work, state)
            print(f'第 {index + 1} 页：完成，已保存 {count} 个文字框', flush=True)

    # 某页失败时，允许本批其他页面完成并落盘，随后停止进入下一批。
    outcomes = await asyncio.gather(*(process(i) for i in range(start, end)), return_exceptions=True)
    failures = [(start + i + 1, exc) for i, exc in enumerate(outcomes) if isinstance(exc, BaseException)]
    if failures:
        pages = ', '.join(str(i) for i, _ in failures)
        raise RuntimeError(f'页面 {pages} 失败，已保存其他页。再次运行可续跑。首个错误：{failures[0][1]}')
    state['completed'] = end
    atomic_json(work / '进度.json', state)
    snapshot(work, state)
    print(f'批次合并完成：{end}/{state["total"]} 页', flush=True)


async def run_batches(work, state, poll_timeout):
    from paddleocr import AsyncPaddleOCRClient, PaddleOCRVLOptions
    options = PaddleOCRVLOptions(
        use_doc_orientation_classify=False, use_doc_unwarping=False,
        use_layout_detection=False, prompt_label='spotting',
        max_new_tokens=state['settings']['max_new_tokens'],
        restructure_pages=False, visualize=False,
    )
    async with AsyncPaddleOCRClient(poll_timeout=poll_timeout) as client:
        while state['completed'] < state['total']:
            await process_batch(work, state, client, options)



def split_output(source, destination, limit=32_000_000):
    """按最终 PDF 字节数分卷，保留页序；单页超限时明确失败。"""
    from tempfile import mkdtemp
    source, destination = Path(source), Path(destination)
    fingerprint = digest(source)
    if destination.exists():
        manifest_path = destination / '分卷清单.json'
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            if manifest['source_sha256'] == fingerprint and manifest['limit'] == limit:
                if all((destination / f['name']).stat().st_size <= limit and digest(destination / f['name']) == f['sha256'] for f in manifest['files']):
                    return destination
        raise FileExistsError(f'分卷目录已存在且不能复用：{destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(mkdtemp(prefix='.pdf-split-', dir=destination.parent))
    try:
        with pymupdf.open(source) as original:
            start, files = 0, []
            while start < len(original):
                def encode(end):
                    with pymupdf.open() as part:
                        part.insert_pdf(original, from_page=start, to_page=end - 1)
                        return part.tobytes(garbage=4, deflate=True)
                best = encode(start + 1)
                if len(best) > limit:
                    raise ValueError(f'第 {start + 1} 页单独导出仍超过 {limit / 1_000_000:g} MB；无法在保持页面内容的条件下分卷。完整 PDF 已保留。')
                end = start + 1
                lo, hi = end + 1, len(original)
                while lo <= hi:
                    mid = (lo + hi) // 2
                    candidate = encode(mid)
                    if len(candidate) <= limit:
                        best, end = candidate, mid
                        lo = mid + 1
                    else:
                        hi = mid - 1
                name = f'分卷_{len(files) + 1:03d}.pdf'
                path = temporary / name
                path.write_bytes(best)
                assert path.stat().st_size <= limit
                files.append(dict(name=name, first_page=start + 1, last_page=end, bytes=len(best), sha256=digest(path)))
                start = end
            atomic_json(temporary / '分卷清单.json', dict(source_sha256=fingerprint, limit=limit, files=files))
        os.replace(temporary, destination)
        return destination
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)

def convert(input_pdf, output_pdf=None, *, workspace='ocr_workspace', dpi=300,
            poll_timeout=600, max_new_tokens=8192, force=False, prepare_only=False, batch_size=30, concurrency=3, output_mode="single"):
    if isinstance(batch_size, bool) or isinstance(concurrency, bool) or not isinstance(batch_size, int) or not isinstance(concurrency, int) or batch_size < 1 or concurrency < 1:
        raise ValueError('批次页数和并发数必须为正整数')
    if output_mode not in ('single', 'split32'):
        raise ValueError('未知输出模式')
    source = Path(input_pdf).resolve()
    if dpi <= 0 or poll_timeout <= 0 or max_new_tokens <= 0:
        raise ValueError('分辨率、超时及生成上限必须为正数')
    identity = digest(source)
    work = Path(workspace).resolve() / identity
    work.mkdir(parents=True, exist_ok=True)
    with (work / '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('同一 PDF 已有工作流运行，不能同时启动两次')
        settings = dict(dpi=dpi, max_new_tokens=max_new_tokens, force=force)
        progress = work / '进度.json'
        if progress.exists():
            state = json.loads(progress.read_text())
            if state['settings'] != settings:
                raise ValueError('续跑参数与首次运行不同，请使用相同参数或另指定 --workspace')
            if digest(work / '原件.pdf') != identity:
                raise RuntimeError('工作区原件被修改，停止处理')
        else:
            with pymupdf.open(source) as doc:
                if not doc.is_pdf or doc.needs_pass or len(doc) == 0:
                    raise ValueError('需要非加密且至少有一页的 PDF')
                total = len(doc)
            tmp = work / '原件.tmp.pdf'
            shutil.copyfile(source, tmp)
            if digest(tmp) != identity:
                raise RuntimeError('复制期间源文件发生变化，请重新运行')
            os.replace(tmp, work / '原件.pdf')
            (work / 'pages').mkdir(exist_ok=True)
            state = dict(version=1, sha256=identity, total=total, completed=0, settings=settings)
            atomic_json(progress, state)
        # 新批次可使用新页数；未完成批次继续保持原边界。
        old_end = min(state['completed'] + state.get('batch_size', 30), state['total'])
        active_batch = any(page_file(work, i, 'json').exists() or page_file(work, i, 'pdf').exists() for i in range(state['completed'], old_end))
        if active_batch and batch_size != state.get('batch_size', 30):
            print('当前批次已有进度，将保留原批次页数以确保续跑一致。', flush=True)
        else:
            state['batch_size'] = batch_size
        state['concurrency'] = concurrency
        atomic_json(progress, state)
        print(f'工作区：{work}', flush=True)
        snapshot(work, state)
        target = Path(output_pdf).resolve() if output_pdf else work / '已完成.pdf'
        if target != work / '已完成.pdf' and (target.exists() or target == source or target.is_relative_to(work)):
            raise ValueError('指定输出路径已存在或位于内部工作区，请选择新的外部输出路径')
        if prepare_only:
            print('准备完成，未调用 API。', flush=True)
            return work
        if state['completed'] < state['total']:
            if not os.environ.get('PADDLEOCR_ACCESS_TOKEN'):
                raise ValueError('请设置 PADDLEOCR_ACCESS_TOKEN；工作区已保存')
            asyncio.run(run_batches(work, state, poll_timeout))
        if output_mode == 'single' and target != work / '已完成.pdf':
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_suffix('.tmp.pdf')
            shutil.copyfile(work / '已完成.pdf', temp)
            os.replace(temp, target)
        if output_mode == 'split32':
            destination = work / '分卷_32MB' if output_pdf is None else target.with_name(target.stem + '_分卷_32MB')
            result = split_output(work / '已完成.pdf', destination)
            print(f'分卷输出完成（每份不超过 32 MB）：{result}', flush=True)
        else:
            print(f'全部完成：{target}', flush=True)
        return work

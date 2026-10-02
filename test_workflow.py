import asyncio
import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pymupdf
import ocr_workflow as flow


class FakeClient:
    def __init__(self):
        self.submitted = []
        self.waited = []
        self.active = 0
        self.peak = 0
        self.fail = True

    async def submit_document_parsing(self, **kwargs):
        index = int(Path(kwargs['file_path']).stem)
        self.submitted.append(index)
        return SimpleNamespace(job_id=str(index))

    async def wait_document_parsing_result(self, job):
        self.waited.append(int(job))
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(.001)
            if self.fail and job == '7':
                raise RuntimeError('模拟中断')
            return SimpleNamespace(pages=[SimpleNamespace(pruned_result={
                'spotting_res': {'rec_texts': [], 'rec_polys': []}})])
        finally:
            self.active -= 1


class WorkflowTest(unittest.TestCase):
    def test_batches_resume_concurrency_and_order(self):
        with TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            source = root / 'input.pdf'
            with pymupdf.open() as doc:
                for index in range(65):
                    page = doc.new_page(width=80, height=80)
                    page.draw_rect(pymupdf.Rect(5, 5, 20, 20), fill=(index/65, 0, 0))
                    if index == 0:
                        page.insert_text((5, 50), 'existing')
                doc.save(source)
            work = flow.convert(source, workspace=root/'work', dpi=72, prepare_only=True)
            self.assertEqual(len(pymupdf.open(work/'工作.pdf')), 30)
            self.assertEqual(len(pymupdf.open(work/'未处理.pdf')), 35)
            state = json.loads((work/'进度.json').read_text())
            client = FakeClient()
            with patch.object(flow, 'add_text_layer', return_value=0):
                with self.assertRaises(RuntimeError):
                    asyncio.run(flow.process_batch(work, state, client, None))
                self.assertEqual(state['completed'], 0)
                self.assertEqual(client.peak, 3)
                self.assertEqual(len(client.submitted), 29)
                client.fail = False
                asyncio.run(flow.process_batch(work, state, client, None))
                self.assertEqual(len(client.submitted), 29, '续跑不能重新提交已记录任务')
                self.assertEqual(client.waited.count(7), 2)
                self.assertEqual(state['completed'], 30)
                self.assertEqual(len(pymupdf.open(work/'已完成.pdf')), 30)
                self.assertEqual(len(pymupdf.open(work/'工作.pdf')), 30)
                self.assertEqual(len(pymupdf.open(work/'未处理.pdf')), 5)
                asyncio.run(flow.process_batch(work, state, client, None))
                self.assertEqual(len(pymupdf.open(work/'工作.pdf')), 5)
                self.assertFalse((work/'未处理.pdf').exists())
                asyncio.run(flow.process_batch(work, state, client, None))
                self.assertEqual(state['completed'], 65)
                self.assertFalse((work/'工作.pdf').exists())
                with pymupdf.open(source) as original, pymupdf.open(work/'已完成.pdf') as final:
                    self.assertEqual(len(final), 65)
                    for before, after in zip(original, final):
                        self.assertEqual(before.get_pixmap().samples, after.get_pixmap().samples)
            renamed = root/'renamed.pdf'
            renamed.write_bytes(source.read_bytes())
            self.assertEqual(flow.convert(renamed, workspace=root/'work', dpi=72, prepare_only=True), work)
            with self.assertRaises(ValueError):
                flow.convert(source, workspace=root/'work', dpi=300, prepare_only=True)

    def test_saved_result_resume_and_rotated_text(self):
        with TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            source = root / 'rotated.pdf'
            with pymupdf.open() as doc:
                page = doc.new_page(width=300, height=200)
                page.set_rotation(90)
                doc.save(source)
            work = flow.convert(source, workspace=root/'work', dpi=72, prepare_only=True)
            state = json.loads((work/'进度.json').read_text())
            record = {'job_id': 'saved', 'width': 300, 'height': 200, 'result': {
                'spotting_res': {'rec_texts': ['中文 Hello 123'],
                'rec_polys': [[[20,30],[280,30],[280,60],[20,60]]]}}}
            flow.atomic_json(flow.page_file(work, 0, 'json'), record)
            class NoNetwork:
                def __getattr__(self, name):
                    raise AssertionError('保存的结果不应再次调用 API')
            asyncio.run(flow.process_batch(work, state, NoNetwork(), None))
            with pymupdf.open(source) as original, pymupdf.open(work/'已完成.pdf') as result:
                self.assertEqual(result[0].rotation, 90)
                self.assertIn('中文 Hello 123', result[0].get_text())
                self.assertEqual(original[0].get_pixmap().samples, result[0].get_pixmap().samples)

    def test_custom_batch_and_concurrency(self):
        with TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            source = root / 'input.pdf'
            with pymupdf.open() as doc:
                for _ in range(9): doc.new_page(width=80, height=80)
                doc.save(source)
            work = flow.convert(source, workspace=root/'work', dpi=72, prepare_only=True, batch_size=4, concurrency=2)
            state = json.loads((work/'进度.json').read_text())
            self.assertEqual(len(pymupdf.open(work/'工作.pdf')), 4)
            self.assertEqual(len(pymupdf.open(work/'未处理.pdf')), 5)
            client = FakeClient()
            client.fail = False
            with patch.object(flow, 'add_text_layer', return_value=0):
                asyncio.run(flow.process_batch(work, state, client, None))
                self.assertEqual(client.peak, 2)
                self.assertEqual(state['completed'], 4)
                self.assertEqual(len(pymupdf.open(work/'工作.pdf')), 4)
                asyncio.run(flow.process_batch(work, state, client, None))
                self.assertEqual(len(pymupdf.open(work/'工作.pdf')), 1)
                asyncio.run(flow.process_batch(work, state, client, None))
                self.assertEqual(state['completed'], 9)


if __name__ == '__main__':
    unittest.main()

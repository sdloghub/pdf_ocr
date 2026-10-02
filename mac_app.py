"""Mac GUI：可将 PDF 拖到 Finder 中的应用图标，或点击选择文件。"""
import contextlib
import json
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

from app_config import default_config, load_token
from ocr_workflow import convert


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('PDF OCR')
        self.root.geometry('760x520')
        self.busy = False
        self.events = queue.Queue()
        tk.Label(self.root, text='将 PDF 拖到应用图标，或点击“选择 PDF”。中断后再次选择同一 PDF 即可续跑。', wraplength=700).pack(pady=12)
        settings = tk.Frame(self.root)
        settings.pack(pady=8)
        self.batch_size = tk.StringVar(value='30')
        self.concurrency = tk.StringVar(value='3')
        tk.Label(settings, text='每批页数').pack(side='left', padx=6)
        self.batch_input = tk.Entry(settings, textvariable=self.batch_size, width=8)
        self.batch_input.pack(side='left', padx=6)
        tk.Label(settings, text='并发数').pack(side='left', padx=6)
        self.concurrent_input = tk.Entry(settings, textvariable=self.concurrency, width=8)
        self.concurrent_input.pack(side='left', padx=6)
        row = tk.Frame(self.root)
        row.pack()
        self.choose = tk.Button(row, text='选择 PDF 并开始', command=self.pick)
        self.choose.pack(side='left', padx=8)
        tk.Button(row, text='设置 API Token', command=self.token_dialog).pack(side='left', padx=8)
        self.log = scrolledtext.ScrolledText(self.root, state='disabled', wrap='word')
        self.log.pack(fill='both', expand=True, padx=12, pady=12)
        self.root.createcommand('::tk::mac::OpenDocument', self.open_documents)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.after(100, self.poll)
        for arg in sys.argv[1:]:
            if arg.lower().endswith('.pdf'):
                self.root.after(300, lambda path=arg: self.start(path))
                break

    def write(self, text):
        self.events.put(('log', text))
        return len(text)

    def flush(self):
        pass

    def poll(self):
        while not self.events.empty():
            kind, text = self.events.get()
            if kind == 'done':
                self.busy = False
                self.choose.configure(state='normal')
                self.batch_input.configure(state='normal')
                self.concurrent_input.configure(state='normal')
            self.log.configure(state='normal')
            self.log.insert('end', text)
            self.log.see('end')
            self.log.configure(state='disabled')
        self.root.after(100, self.poll)

    def pick(self):
        path = filedialog.askopenfilename(filetypes=[('PDF', '*.pdf')])
        if path:
            self.start(path)

    def open_documents(self, *paths):
        if paths:
            self.start(paths[0])

    def token_dialog(self):
        if self.busy:
            messagebox.showinfo('任务运行中', '请在任务结束后修改 token。')
            return
        dialog = tk.Toplevel(self.root)
        dialog.title('设置 API Token')
        tk.Label(dialog, text='令牌仅保存到应用旁的 config.json').pack(padx=20, pady=10)
        field = tk.Entry(dialog, width=55, show='*')
        field.pack(padx=20, pady=10)
        def save():
            token = field.get().strip()
            if not token:
                return
            path = default_config()
            path.write_text(json.dumps({'token': token}, indent=2), encoding='utf-8')
            path.chmod(0o600)
            dialog.destroy()
        tk.Button(dialog, text='保存', command=save).pack(pady=10)

    def start(self, path):
        if self.busy:
            messagebox.showinfo('任务运行中', '当前 PDF 完成后再选择其他文件。')
            return
        try:
            batch_size = int(self.batch_size.get())
            concurrency = int(self.concurrency.get())
            if batch_size < 1 or concurrency < 1:
                raise ValueError('每批页数和并发数必须为正整数。')
            config = load_token()
        except ValueError as exc:
            messagebox.showerror('请检查配置', str(exc))
            return
        self.busy = True
        self.choose.configure(state='disabled')
        self.batch_input.configure(state='disabled')
        self.concurrent_input.configure(state='disabled')
        def worker():
            try:
                with contextlib.redirect_stdout(self):
                    convert(path, workspace=config.parent/'ocr_workspace', batch_size=batch_size, concurrency=concurrency)
            except Exception as exc:
                self.write(f'任务停止：{exc}\n再次选择同一文件可续跑。\n')
            finally:
                self.events.put(('done', '\n任务结束。\n'))
        threading.Thread(target=worker, daemon=True).start()

    def close(self):
        if self.busy and not messagebox.askyesno('停止任务', '关闭应用会中断当前任务，已保存进度可续跑。是否关闭？'):
            return
        self.root.destroy()


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        import pymupdf
        from paddleocr import AsyncPaddleOCRClient, PaddleOCRVLOptions
        from pdf_ocr import add_text_layer
        assert PaddleOCRVLOptions(prompt_label='spotting').to_payload()['promptLabel'] == 'spotting'
        with pymupdf.open() as doc:
            page = doc.new_page(width=300, height=200)
            before = page.get_pixmap().samples
            add_text_layer(page, {'spotting_res': {'rec_texts': ['中文 Hello 123'], 'rec_polys': [[[20,30],[280,30],[280,60],[20,60]]]}}, 300, 200)
            assert '中文 Hello 123' in page.get_text()
            assert before == page.get_pixmap().samples
        print('打包自检通过：SDK、PDF 字体和隐藏文字层可用。')
    else:
        App().root.mainloop()

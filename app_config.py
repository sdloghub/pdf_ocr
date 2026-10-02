"""外置 token 配置，读取后提供给官方 SDK。"""
import json
import os
from pathlib import Path
import sys


def default_config():
    if getattr(sys, 'frozen', False):
        executable = Path(sys.executable).resolve()
        return executable.parents[3] / 'config.json'
    return Path(__file__).resolve().parent / 'config.json'


def load_token(path=None):
    path = Path(path) if path else default_config()
    if not path.exists():
        raise ValueError(f'找不到配置文件：{path}')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError) as exc:
        raise ValueError('config.json 读取失败，请检查 JSON 格式。') from exc
    token = data.get('token') if isinstance(data, dict) else None
    if not isinstance(token, str) or not token.strip() or token == '请填入你的 AI Studio Access Token':
        raise ValueError(f'请先填写 {path} 中的 token。')
    os.environ['PADDLEOCR_ACCESS_TOKEN'] = token.strip()
    return path

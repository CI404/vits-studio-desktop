"""``cached_path`` 的最小桩：MeloTTS 只在"自动下载权重"时用它。

我们给的是显式本地路径，所以这里只需要让 import 通过、并在被真正调用时
给出明确报错。
"""
from pathlib import Path


def cached_path(url_or_filename, cache_dir=None, **kwargs):
    p = Path(url_or_filename)
    if p.exists():
        return str(p)
    raise FileNotFoundError(
        f"cached_path stub: {url_or_filename} 不是本地文件；请直接用 config_path/ckpt_path 指定本地权重"
    )


def get_cache_dir(*args, **kwargs):
    return str(Path.home() / ".cache" / "dshat_stub")

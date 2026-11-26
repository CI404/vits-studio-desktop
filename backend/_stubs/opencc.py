"""``opencc`` 的兼容垫片（用 zhconv 实现繁简转换）。

GPT-SoVITS 中文前端用 ``opencc.OpenCC('t2s').convert(...)`` 做繁简归一。
opencc 的 C++ 扩展在 py3.12/Windows 上装不上，这里用纯 Python 的 zhconv 等价实现。
"""
import zhconv

_MAP = {
    "t2s": "zh-cn",
    "t2s.json": "zh-cn",
    "tw2sp": "zh-cn",
    "hk2s": "zh-cn",
    "s2t": "zh-tw",
    "s2tw": "zh-tw",
}


class OpenCC:
    def __init__(self, config="t2s", **kwargs):
        self.config = config

    def convert(self, text):
        return zhconv.convert(text, _MAP.get(str(self.config).lower(), "zh-cn"))


def converter(*args, **kwargs):
    return lambda t: zhconv.convert(t, "zh-cn")

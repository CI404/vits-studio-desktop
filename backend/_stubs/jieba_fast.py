"""``jieba_fast`` 的兼容垫片。

GPT-SoVITS 的中文前端用 ``jieba_fast``（C 加速版，Windows/py3.12 没有现成 wheel），
其 API 与 ``jieba`` 完全一致。这里把标准 jieba 包一层，挂到 ``jieba_fast`` 这个名字下，
再伪造一个 ``jieba_fast.posseg`` 子模块，避免源码改动。
"""
import sys
import types

import jieba
import jieba.posseg as _posseg

_mod = types.ModuleType("jieba_fast")
_mod.__dict__.update({k: v for k, v in jieba.__dict__.items() if not k.startswith("__")})
_mod.lcut = jieba.lcut
_mod.cut = jieba.cut
_mod.load_userdict = jieba.load_userdict
_mod.setLogLevel = getattr(jieba, "setLogLevel", lambda *a, **k: None)
_mod.initialize = jieba.initialize
_mod.dt = getattr(jieba, "dt", None)

_pos = types.ModuleType("jieba_fast.posseg")
_pos.__dict__.update({k: v for k, v in _posseg.__dict__.items() if not k.startswith("__")})
_pos.cut = _posseg.cut
_pos.lcut = _posseg.lcut
_mod.posseg = _pos

sys.modules["jieba_fast"] = _mod
sys.modules["jieba_fast.posseg"] = _pos

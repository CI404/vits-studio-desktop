"""``gradio`` 的最小垫片。

GPT-SoVITS 的数据预处理脚本只通过 ``tools/my_utils.py`` 用到 ``gr.Warning`` / ``gr.Info``
这类提示，CLI 流程里不需要真的 UI。这里提供同名函数，避免为了 100+ MB 的 gradio
去改整个环境。
"""


def Warning(message="", *args, **kwargs):  # noqa: N802
    print(f"[warn] {message}", flush=True)


def Info(message="", *args, **kwargs):  # noqa: N802
    print(f"[info] {message}", flush=True)


def Error(message="", *args, **kwargs):  # noqa: N802
    print(f"[error] {message}", flush=True)


class Progress:  # 占位：支持 progress(i, desc=...) 调用
    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def update(**kwargs):
    return kwargs


class Blocks:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def __getattr__(name):
    def _noop(*args, **kwargs):
        return None
    return _noop

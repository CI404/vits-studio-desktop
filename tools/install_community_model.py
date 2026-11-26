"""从 HuggingFace 一键安装社区 GPT-SoVITS 模型。

用法：
    python tools/install_community_model.py --repo 4nm1tsu/shikokumetan_GPT-SoVITS
    python tools/install_community_model.py --repo Richopera/Yangyang-gpt-sovits-v2proplus --name yangyang
    python tools/install_community_model.py --repo X --s2 SoVITS_weights_v2Pro/a.pth --s1 GPT_weights_v2Pro/a.ckpt

做的事：
  1. 列仓库里的权重文件，按大小自动挑出「s2 (SoVITS)」和「s1 (GPT)」（可用参数覆盖）；
  2. 下载到 community_models/<name>/；
  3. 读 s2 前 2 字节判断版本（v1/v2/v3/v4/v2Pro/v2ProPlus…）；
  4. 写 model.json（含 sovits_version / ref_lang），参考音若缺失会提示补一个。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://hf-mirror.com"
DEST = Path(r"<PROJECT_ROOT>\community_models")
HEAD2VERSION = {
    b"00": "v1", b"01": "v2", b"02": "v3", b"03": "v3lora",
    b"04": "v4lora", b"05": "v2Pro", b"06": "v2ProPlus",
    b"07": "v5dev", b"08": "v5turbo",
}


def api(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def head2version(path: Path) -> str:
    with path.open("rb") as fh:
        head = fh.read(2)
    if head in HEAD2VERSION:
        return HEAD2VERSION[head]
    if head == b"PK":
        size = path.stat().st_size
        return "v1" if size < 82_978 * 1024 else ("v2" if size < 700 * 1024 * 1024 else "v3")
    return ""


def download(repo: str, remote: str, local: Path) -> None:
    url = f"{BASE}/{repo}/resolve/main/{urllib.parse.quote(remote)}"
    local.parent.mkdir(parents=True, exist_ok=True)
    if local.exists() and local.stat().st_size > 1e6:
        print(f"  skip {local.name}")
        return
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=300) as r, local.open("wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)
            got += len(chunk)
    print(f"  OK {local.name}  {local.stat().st_size / 1e6:.1f} MB")


def pick_files(repo: str) -> tuple[str, str]:
    info = api(f"{BASE}/api/models/{repo}?blobs=true")
    files = [(s["rfilename"], s.get("size") or 0) for s in info.get("siblings", [])]

    def is_sovits(fn: str) -> bool:
        p = fn.lower()
        name = p.split("/")[-1]
        if not p.endswith((".pth", ".pt")):
            return False
        if "log" in p or name.startswith(("g_", "d_", "disc")):
            return False
        return "sovits" in p or "/s2" in p or "_s2" in p or "s2g" in name

    def is_gpt(fn: str) -> bool:
        p = fn.lower()
        if not p.endswith((".ckpt", ".pt", ".pth")):
            return False
        if "log" in p:
            return False
        return "gpt" in p or p.endswith(".ckpt")

    sov = [(f, sz) for f, sz in files if is_sovits(f)]
    gpt = [(f, sz) for f, sz in files if is_gpt(f)]
    if not sov:
        sov = [(f, sz) for f, sz in files if not is_gpt(f)] or files
    if not gpt:
        gpt = [(f, sz) for f, sz in files if f.lower().endswith(".ckpt")] or files
    if not sov or not gpt:
        raise SystemExit("仓库里没找到权重")
    return max(sov, key=lambda x: x[1])[0], max(gpt, key=lambda x: x[1])[0]

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--s2", default="")
    ap.add_argument("--s1", default="")
    ap.add_argument("--speaker", default="")
    ap.add_argument("--language", default="ja")
    ap.add_argument("--ref-wav", default="")
    ap.add_argument("--ref-txt", default="")
    args = ap.parse_args()

    name = args.name or args.repo.split("/")[-1].replace("GPT-SoVITS-", "").replace("-gpt-sovits", "").lower()
    dest = DEST / name
    print(f"安装 {args.repo} -> {dest}")

    s2_remote, s1_remote = (args.s2, args.s1) if args.s2 and args.s1 else pick_files(args.repo)
    print(f"  s2 = {s2_remote}\n  s1 = {s1_remote}")
    download(args.repo, s2_remote, dest / Path(s2_remote).name)
    download(args.repo, s1_remote, dest / Path(s1_remote).name)

    version = head2version(dest / Path(s2_remote).name)
    print(f"  检测版本: {version or '未知'}")

    # 参考音：用参数给的，否则借用同语言的现成模型
    ref_wav, ref_txt = dest / "ref.wav", dest / "ref.txt"
    if args.ref_wav:
        shutil.copyfile(args.ref_wav, ref_wav)
        shutil.copyfile(args.ref_txt or Path(args.ref_wav).with_suffix(".txt"), ref_txt)
    elif not ref_wav.exists():
        lend = Path(r"<PROJECT_ROOT>\models_out\Tong\ref.wav")
        if lend.exists():
            shutil.copyfile(lend, ref_wav)
            shutil.copyfile(lend.with_suffix(".txt"), ref_txt)
            print(f"  ⚠ 未提供参考音，暂借 {lend.parent.name} 的参考音（音色会受影响，建议换成角色本人的干净语音）")

    meta = {
        "speaker": args.speaker or name.capitalize(),
        "name": args.speaker or name,
        "language": args.language,
        "ref_lang": "ja",
        "finetuned": "s1+s2",
        "engine_version": version or "v2Pro",
        "sovits_version": version,
        "version": "community-1",
        "engine": "GPT-SoVITS",
        "output_sr": 32000,
        "sovits_weights": Path(s2_remote).name,
        "gpt_weights": Path(s1_remote).name,
        "ref_audio": "ref.wav",
        "ref_text": "ref.txt",
        "note": f"社区开源模型 {args.repo}",
    }
    (dest / "model.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"完成：{dest}\n在应用里点「重新扫描模型」即可出现。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

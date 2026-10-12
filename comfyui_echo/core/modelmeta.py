"""模型元信息探测：**只看文件头**，推出「底模 / 用途」。

不看权重内容、不 load 张量、不联网、不引第三方依赖（只用标准库）。

三种格式三条路：

- **`.safetensors`**：开头 8 字节是 header 长度，跟着一段 JSON。JSON 里的
  `__metadata__` 是训练器写的（LoRA 常有 `ss_sd_model_name` 等），其余键是
  **张量名 → 形状**。⚠ checkpoint 的 `__metadata__` **通常整个是空的**
  （Civitai 下的合并模型尤其），所以还要靠**张量名 + 形状**做「架构指纹」。
- **`.pt` / `.pth` / `.bin`**：torch ≥1.6 存的是 **ZIP 容器**，里面 `*/data.pkl`
  是 pickle 流。**只读这一个成员**（那几个 GB 的 `data/*` 载荷完全不碰），
  用「受限 Unpickler + 桩类」解析 —— **不 import torch**。
  顺带比 `torch.load` **更安全**：全程不 import 真实类、不执行 `__reduce__` 构造函数，
  等效于「永远 `weights_only=True` 且更严」。
- **`.gguf`**：自家魔法头 + KV（P1 只认格式，元数据留到 P2）。

⚠ 三个实测踩到的坑，改之前先读：
1. 桩类的 `args/kwargs/state` **必须是类属性** —— pickle 有时走 `cls.__new__(cls)`
   不调 `__init__`，实例上没这些属性会被 `__getattr__` 兜成桩，后面 `len()` 直接炸。
2. `persistent_load` **必须返回桩** —— zip 格式的张量存储是持久化 id 引用的，
   不给桩就会去解那几个 GB 的载荷（本机有个 2.5GB 的 SAM）。
3. `find_class` 只放行白名单，且 **`collections.OrderedDict` 必须放行** ——
   state_dict 就是它，不放行什么都读不出来。
"""
from __future__ import annotations

import json
import os
import pickle
import re
import sqlite3
import struct
import zipfile

from . import _bridge as B

__all__ = ["probe", "probe_all", "load_all", "set_user", "clear_cache",
           "family_options", "purpose_options", "TORCH_EXTS", "ST_EXTS"]

# 和备注共用一个库（`data/model_notes.sqlite3`），但**自建一张表**，不动 Echo 那四张。
_DB = os.path.join(B.DATA_DIR, "model_notes.sqlite3")

ST_EXTS = {".safetensors"}
TORCH_EXTS = {".pt", ".pth", ".bin", ".ckpt"}

#: 探测结果表。⚠ 主键是 (root_key, rel_path)：`rel_path` 单独做键的话，
#: 换一个 ComfyUI 包、同名相对路径会互相覆盖。
_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_meta (
    root_key  TEXT NOT NULL,
    rel_path  TEXT NOT NULL,
    size      INTEGER DEFAULT 0,
    mtime     INTEGER DEFAULT 0,
    fmt       TEXT DEFAULT '',
    domain    TEXT DEFAULT '',
    arch      TEXT DEFAULT '',
    family    TEXT DEFAULT '',
    purpose   TEXT DEFAULT '',
    variant   TEXT DEFAULT '',
    detail    TEXT DEFAULT '',
    probe_src TEXT DEFAULT '',
    extra     TEXT DEFAULT '',
    ver       INTEGER DEFAULT 0,
    PRIMARY KEY (root_key, rel_path)
)
"""

# ---------------------------------------------------------------------------
# 归一化表
# ---------------------------------------------------------------------------
#: 归一化表 / 指纹规则的**版本号**。⚠ 改了 `_FAMILY` / `_SPEED` / `_fingerprint` / `_ARCH_MAP`
#: 里任何一条，**必须把这个数字加一** —— 否则缓存按 (size, mtime) 判定"文件没变"，
#: 旧结论会一直留着，你会以为改的规则根本没生效（实测踩过）。
RULES_V = 6

#: 底模家族。顺序 = 优先级。⚠ `animagine` 必须排在 `anima` **前面**，
#: 否则 "animagine" 会被 anima 那条正则先吃掉。
_FAMILY = (
    (re.compile(r"animagine", re.I), "Animagine"),
    # ⚠ 不能用 `\banima\b`：Python 的 `\w` 含下划线和数字，所以 `anima8Step10` / `anima14`
    #   这两个名字里 `anima` 后面紧跟着数字，`\b` 根本不成立（实测漏了 2 个 Anima）。
    #   改成"前面不是字母、后面也不是字母" —— 这样 `animagine`（另有规则，且排在前面）
    #   与 `animation` 都不会被误吞。
    (re.compile(r"(?<![A-Za-z])anima(?![A-Za-z])", re.I), "Anima"),
    (re.compile(r"illustrious|iluust|hassaku|wainsfw|noobai|naixl|"
                r"anyillustrious", re.I), "Illustrious"),
    (re.compile(r"(?<![A-Za-z])pony(?![A-Za-z])|ponyxl", re.I), "Pony"),
    (re.compile(r"qwen", re.I), "Qwen-Image"),
    (re.compile(r"minimax|(?<![A-Za-z0-9])h3(?![A-Za-z0-9])", re.I), "MiniMax H3"),
    (re.compile(r"flux", re.I), "Flux"),
    (re.compile(r"sd3|stable[-_]diffusion[-_]3", re.I), "SD3"),
    (re.compile(r"(?<![A-Za-z])wan2?", re.I), "Wan"),
)

#: 文件里常见的**缩写**，按"整词"匹配（不参与子串匹配，免得误伤）。
#: `mrs-snake-fate` 这种没线索的靠它救回来一批：`Scathach-FGO-ILXL`、`-IL_v1`、
#: `bb-style-illust`、`Aura_Phantasy_illu`、`ILLUSTXL_...`、`ill_bible_black_full`。
_ABBR = {
    "il": "Illustrious", "ilxl": "Illustrious", "illust": "Illustrious",
    "illustxl": "Illustrious", "illu": "Illustrious", "ill": "Illustrious",
    "iluust": "Illustrious",
}
_TOKEN_SPLIT = re.compile(r"[-_.\s()\[\]{},+]+")


#: 用途：**加速** 是唯一能从文件名可靠判出来的用途（角色 / 风格判不了，留给用户）。
#: ⚠ 边界**不能用 `\b`** —— Python 的 `\w` **包含下划线**，所以
#:   `minimax_h3_turbo_4step` 里 `\bturbo\b` 是**不匹配**的（`_t` 之间没有词边界）。
#:   实测踩过：这一条会漏掉一大半加速 LoRA。改用 `(?<![A-Za-z0-9])` / `(?![A-Za-z0-9])`。
#: ⚠ `hyper` 必须带右边界，否则 `Hyperrealistic_illustrious` 会被误判成加速（也实测踩过）。
#: ⚠ `\d+step` 不要求左边界：`anima8Step10` 这种把步数直接连在名字后面的很常见。
_SPEED = re.compile(
    r"(?<![A-Za-z0-9])(?:turbo|lcm|lightning|hyper|dmd2?|accel)(?![A-Za-z0-9])"
    r"|\d+\s*step", re.I)

#: 架构归一化：`modelspec.architecture` / `ss_base_model_version` 给的是 slug，
#: 直接摊到界面上很难看（`stable-diffusion-xl-v1-base` 有 104 个）。⚠ VAE 要排在最前 ——
#: `qwen_image_2.1_vae` 算是 Qwen 的 VAE，架构该记 VAE、家族记 Qwen-Image。
_ARCH_MAP = (
    (re.compile(r"vae", re.I), "VAE"),
    (re.compile(r"qwen", re.I), "Qwen-Image"),
    (re.compile(r"flux", re.I), "Flux"),
    (re.compile(r"stable-diffusion-3|sd3", re.I), "SD3"),
    (re.compile(r"stable-diffusion-xl|sdxl", re.I), "SDXL"),
    (re.compile(r"stable-diffusion-v1|stable-diffusion-2|sd1[._]?5", re.I), "SD1.5/2.x"),
)


def _norm_arch(s: str) -> str:
    for rx, name in _ARCH_MAP:
        if rx.search(s):
            return name
    return s.strip()


#: 完全没有语义的底模名（Civitai 批量下载常见）：纯数字 id / EMS-xxxxxx
_MEANINGLESS = re.compile(r"^\d+$|^ems[-\d]+(?:-ems)?$", re.I)

#: 工具类目录 → 用途（这些**没有底模概念**，只按用途分类）
_TOOL_DIRS = {
    "ultralytics": "检测", "yolo": "检测", "detection": "检测",
    "sams": "分割", "background_removal": "抠图",
    "upscale_models": "放大", "latent_upscale_models": "放大",
    "bge-m3": "文本编码", "llm": "多模态", "vae_approx": "预览加速",
    "audio_encoders": "音频编码", "frame_interpolation": "插帧",
    "optical_flow": "光流", "geometry_estimation": "几何",
}

#: 生成链路目录 → 有底模概念
_GEN_DIRS = {
    "checkpoints", "loras", "controlnet", "vae", "diffusion_models", "unet",
    "text_encoders", "clip", "clip_vision", "embeddings", "hypernetworks",
    "model_patches", "style_models", "photomaker", "gligen", "diffusers",
}


# ---------------------------------------------------------------------------
# torch pickle：受限 Unpickler + 桩类（不 import torch）
# ---------------------------------------------------------------------------
_SAFE = {
    "builtins": {"dict", "list", "set", "frozenset", "tuple", "str", "int",
                 "float", "bool", "bytes", "bytearray", "complex", "object"},
    "collections": {"OrderedDict", "defaultdict"},      # ⚠ OrderedDict 必须放行
    "collections.abc": {"Sequence"},
}


class _Stub:
    """万能桩：吞掉一切调用/属性/setstate，同时把参数留下来（能读到张量形状）。"""

    args = ()          # ⚠ 类属性，见文件头坑 1
    kwargs = {}
    state = None

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def __call__(self, *args, **kwargs):
        return _Stub(*args, **kwargs)

    def __setstate__(self, state):
        self.state = state

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Stub()

    def append(self, x):
        pass

    def extend(self, xs):
        pass

    def __repr__(self):
        return "<Stub>"


class _SafeUnpickler(pickle.Unpickler):
    def find_class(self, module, name):      # noqa: N802 (Qt/pickle 命名)
        if name in _SAFE.get(module, ()):
            return getattr(__import__(module), name)
        return _Stub                          # torch.* / ultralytics.* / numpy.* 全给桩

    def persistent_load(self, pid):
        return _Stub(pid)                     # ⚠ 别去读那几个 GB，见文件头坑 2


def _read_torch(path):
    """读 torch 存档的 pickle 部分。返回 (obj, note)；读不了返回 (None, 原因)。"""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
        if magic[:2] == b"PK":                # torch>=1.6 的 zip 容器
            with zipfile.ZipFile(path) as z:
                member = next((n for n in z.namelist()
                               if n.endswith("/data.pkl") or n == "data.pkl"), None)
                if member is None:
                    return None, "zip 里没有 data.pkl"
                with z.open(member) as fh:
                    return _SafeUnpickler(fh).load(), ""
        # 老版 torch 是裸 pickle（本机实测 0 例，先留个明确提示而不是硬解）
        return None, "非 zip 存档（torch<1.6 的 legacy 格式，暂不支持）"
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:60]}"


def _torch_temps(obj, cap: int = 4000) -> dict:
    """从桩对象里捞 `{张量名: 形状}` —— `_rebuild_tensor_v2` 的第 3 个参数就是 size。

    ⚠ 必须返回 **dict**（不是 list）：下游 `_fingerprint()` 要按键取值。
    """
    out: dict = {}

    def shape_of(stub):
        a = stub.args
        if isinstance(a, (list, tuple)) and len(a) >= 3 \
                and isinstance(a[2], (list, tuple)) and 1 <= len(a[2]) <= 6:
            return [int(d) for d in a[2]]
        return None

    def walk(o, depth=0):
        if depth > 4 or len(out) > cap:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(k, str) and isinstance(v, _Stub):
                    s = shape_of(v)
                    if s:
                        out[k] = s
                walk(v, depth + 1)
        elif isinstance(o, _Stub):
            if isinstance(o.state, (dict, list, tuple)):
                walk(o.state, depth + 1)
        elif isinstance(o, (list, tuple)):
            for v in o[:80]:
                walk(v, depth + 1)

    walk(obj)
    return out


# ---------------------------------------------------------------------------
# safetensors 头
# ---------------------------------------------------------------------------
def _read_st(path, max_json=256 * 1024 * 1024):
    """读 safetensors 头。返回 (metadata, {张量名: 形状}, note)。"""
    try:
        with open(path, "rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            if n <= 0 or n > max_json:
                return {}, {}, "header 长度异常 %d" % n
            head = json.loads(f.read(n))
    except Exception as e:
        return {}, {}, f"{type(e).__name__}: {str(e)[:60]}"
    meta = head.pop("__metadata__", None) or {}
    shapes = {k: v.get("shape") for k, v in head.items() if isinstance(v, dict)}
    return meta, shapes, ""


def _sd_gen(names: set, shapes: dict) -> str:
    """判 SD 世代（SDXL / SD2.x / SD1.5）：① 有第二个文本编码器 = SDXL ② 交叉注意力 `to_k` 的
    **输入维**：768 = SD1.5、**1024 = SD2.x**、2048 = SDXL。两种键名（裸 UNet 的
    `attn2.to_k.weight` 和 LoRA 的 `attn2_to_k.lora_*.weight`）都要认。

    ⚠ 这条要**排在「内嵌 VAE」判定前面**：checkpoint 多数带内嵌 VAE，先判 VAE 的话
      只会得到一句没用的「SD(含内嵌VAE)」，世代信息就丢了（实测踩过）。
    """
    if shapes.get("conditioner.embedders.1.model.token_embedding.weight"):
        return "SDXL"
    te0 = shapes.get("conditioner.embedders.0.model.token_embedding.weight")
    if te0:
        return {768: "SD1.5/2.x", 1024: "SD2.x", 1280: "SDXL"}.get(te0[-1], "SDXL")
    for k, v in shapes.items():
        if ("attn2.to_k.weight" in k or "attn2_to_k.lora" in k) \
                and isinstance(v, list) and v:
            if v[-1] == 2048:
                return "SDXL"
            if v[-1] == 1024:
                return "SD2.x"
            if v[-1] == 768:
                return "SD1.5/2.x"
    return ""


def _fingerprint(names: set, shapes: dict) -> str:
    """按张量名 + 形状判架构。⚠ 只做**能可靠判**的，判不出就留空让人/文件名兜。"""
    if "double_blocks.0.img_attn.qkv.weight" in names:
        return "Flux"
    if "joint_blocks.0.x_block.attn.qkv.weight" in names:
        return "SD3"
    gen = _sd_gen(names, shapes)
    # ⚠ ControlNet 的键名**不是** `control_model.` 前缀（那是 ComfyUI 加载后加的），
    #   原生导出是 `controlnet_cond_embedding.*` / `controlnet_down_blocks.*`。
    if "controlnet_cond_embedding.conv_in.weight" in names \
            or any(n.startswith("controlnet_") for n in names):
        return gen or "ControlNet"
    if gen:
        return gen                    # checkpoint / UNet / LoRA 都靠这一步
    if "encoder.conv_in.weight" in names and "decoder.conv_in.weight" in names:
        return "VAE"
    if "first_stage_model.encoder.conv_in.weight" in names:
        return "SD(含内嵌VAE)"
    if any("lora_te2_" in n for n in names):
        return "SDXL"                 # LoRA 兜底：有第二个文本编码器就是 SDXL
    return ""


#: 家族判不出来时，可以退到这些**架构**当家族用（它们本身就是有区分度的底座）。
#: ⚠ `UNet` / `ControlNet` / `VAE` / `SD(含内嵌VAE)` **不在**这里 —— 那些不是家族，
#: 塞进"底模"列只会让人困惑。
_ARCH_AS_FAMILY = {"SDXL", "SD1.5/2.x", "SD2.x", "SD3", "Flux"}


def _family_of(text: str) -> str:
    """从一段文本（底模名 / 文件名 / 相对路径）里认家族。长名字走子串，缩写走整词。"""
    for rx, name in _FAMILY:
        if rx.search(text):
            return name
    for tok in _TOKEN_SPLIT.split(text.lower()):
        if tok in _ABBR:
            return _ABBR[tok]
    return ""


def _speed_hit(name: str) -> bool:
    return bool(_SPEED.search(name))


def _tensor_count_detail(shapes: dict) -> str:
    if not shapes:
        return ""
    return "%d 张量" % len(shapes)


# ---------------------------------------------------------------------------
# 单个文件探测
# ---------------------------------------------------------------------------
def probe(abs_path: str, rel_path: str, kind: str = "") -> dict:
    """探测一个模型文件。**只读、不改动、不联网**。返回可直接入库的一行。"""
    name = os.path.basename(rel_path)
    top = rel_path.replace("\\", "/").split("/")[0].lower()
    ext = os.path.splitext(abs_path)[1].lower()

    # ⚠ 默认按「生成链路」算：只有明确列在 `_TOOL_DIRS` 里的才算工具。
    #   反过来写（默认 tool）会把 `MINIMAX_H3` / `Qwen-功能2.1` 这种自定义目录全判成工具。
    top_dir = rel_path.replace("\\", "/").split("/")[0]
    row = {"fmt": "", "domain": "tool" if top_dir.lower() in _TOOL_DIRS else "gen",
           "arch": "", "family": "", "purpose": "", "variant": "",
           "detail": "", "probe_src": "", "extra": ""}

    # 工具类目录：用途直接由目录定，且**没有底模概念**
    if top in _TOOL_DIRS:
        row["purpose"] = _TOOL_DIRS[top]

    meta, shapes, note, names = {}, {}, "", set()

    if ext in ST_EXTS:
        row["fmt"] = "safetensors"
        meta, shapes, note = _read_st(abs_path)
        names = set(shapes)
    elif ext in TORCH_EXTS:
        row["fmt"] = "torch"
        obj, note = _read_torch(abs_path)
        if obj is not None:
            if isinstance(obj, dict) and isinstance(obj.get("model"), _Stub):
                # ultralytics 存档：元数据在 model.state 里（names/yaml），外加 train_args
                st = obj["model"].state if isinstance(obj["model"].state, dict) else {}
                names = set(st)
                shapes = {}
                nm = st.get("names") if isinstance(st, dict) else None
                ya = st.get("yaml") if isinstance(st, dict) else None
                ta = obj.get("train_args") if isinstance(obj.get("train_args"), dict) else {}
                bits = []
                if isinstance(nm, dict) and nm:
                    row["detail"] = "检测 " + "/".join(str(v) for v in list(nm.values())[:6])
                if isinstance(ya, dict):
                    yf = ya.get("yaml_file") or ""
                    row["variant"] = str(ya.get("scale") or "") or ""
                    if yf:
                        bits.append(str(yf).replace(".yaml", ""))
                for k, lab in (("model", "底模"), ("epochs", "轮")):
                    if ta.get(k):
                        bits.append(("底模 " if k == "model" else "") + str(ta[k])[:40]
                                    if k == "model" else "%s轮" % ta[k])
                if ta.get("imgsz"):
                    bits.append("@%s" % ta["imgsz"])
                if obj.get("date"):
                    bits.append(str(obj["date"])[:10])
                if obj.get("version"):
                    bits.append("ultralytics %s" % obj["version"])
                row["extra"] = json.dumps({"train_args": {
                    k: str(v)[:120] for k, v in list(ta.items())[:40]}},
                    ensure_ascii=False)[:4000]
                extra_detail = " · ".join(x for x in bits if x)
                row["detail"] = " · ".join(x for x in (row["detail"], extra_detail) if x)
                row["probe_src"] = "torch-pickle"
            else:
                # 普通 state_dict（ESRGAN / SAM / bge-m3 …）
                shapes = _torch_temps(obj)
                names = set(shapes)
                row["probe_src"] = "torch-pickle"
                row["detail"] = _tensor_count_detail(shapes)
    elif ext == ".gguf":
        row["fmt"] = "gguf"
        note = "GGUF 的 KV 元数据待 P2"
    else:
        row["fmt"] = ext.lstrip(".") or "未知"

    if meta:                                   # ① 文件头 __metadata__ 最可靠
        sd = str(meta.get("ss_sd_model_name") or "").strip()
        if sd and not _MEANINGLESS.match(os.path.splitext(sd)[0]):
            row["family"] = _family_of(sd)
            if row["family"]:
                row["probe_src"] = "header-meta"
        row["variant"] = row["variant"] or str(meta.get("ss_network_dim") or "")
        arch = str(meta.get("modelspec.architecture") or "")
        base = str(meta.get("ss_base_model_version") or "")
        if arch:
            row["arch"] = _norm_arch(arch.split("/")[0])
        elif base:
            row["arch"] = _norm_arch({"sdxl_base_v1-0": "SDXL",
                                      "sdxl_a1111_compat": "SDXL"}.get(base, base))
        # ⚠ 有架构也要把来源记上：不然"家族退到架构"那条路（很多 LoRA 只有
        #   `modelspec.architecture` 没有 `ss_sd_model_name`）会显示成「依据：—」。
        if row["arch"]:
            row["probe_src"] = row["probe_src"] or "header-meta"
        try:
            row["extra"] = json.dumps(
                {k: str(v)[:200] for k, v in list(meta.items())[:40]},
                ensure_ascii=False)[:4000]
        except Exception:
            pass

    if not row["arch"] and names:              # ② 张量形状指纹
        fp = _fingerprint(names, shapes)
        if fp:
            row["arch"] = fp
            row["probe_src"] = row["probe_src"] or "tensor-fingerprint"

    if not row["family"]:                      # ③ 文件名 / 相对路径兜底
        fam = _family_of(name) or _family_of(rel_path)
        if fam:
            row["family"] = fam
            row["probe_src"] = row["probe_src"] or "filename"

    # ④ 家族还是判不出 -> 退到**架构**（只认有区分度的 SDXL/SD1.5/SD3/Flux）。
    #    很多播主只传权重不写 `ss_*`，这时"这是 SDXL 的 LoRA"就是能诚实给出的最细结论。
    #    来源仍标架构那一级，界面上靠 tooltip 说明"只判到架构"。
    if not row["family"] and row["arch"] in _ARCH_AS_FAMILY:
        row["family"] = row["arch"]

    # 用途：**只自动判「加速」** —— 角色 / 风格判不出来，留给用户手改
    # （⚠ 工具类目录的用途在最上面按目录定过，这里只补「加速」这一种）
    if _speed_hit(name):
        row["purpose"] = "加速"

    if not row["family"] and row["arch"] == "VAE":
        row["family"] = "VAE"      # 独立 VAE 没"底模"，但架构本身就是它的身份
    if not row["detail"] and row["fmt"] == "torch" and not note:
        row["detail"] = "%d 张量" % len(shapes)
    if note:
        row["detail"] = (row["detail"] + " · " if row["detail"] else "") + "⚠ " + note
    return row


# ---------------------------------------------------------------------------
# 模型类型（kind）
# ---------------------------------------------------------------------------
#: 顶层目录 -> 类型。⚠ **优先按目录第一段判**：ComfyUI 的目录名是有约定的，
#: 比在整条路径里瞎找关键词可靠得多。
_KIND_DIRS = {
    "checkpoints": "checkpoint",
    "loras": "lora",
    "controlnet": "controlnet",
    "vae": "vae",
    "vae_approx": "vae_approx",
    "diffusion_models": "diffusion",
    "unet": "diffusion",
    "text_encoders": "text_encoder",
    "clip": "text_encoder",
    "clip_vision": "clip_vision",
    "embeddings": "embedding",
    "upscale_models": "upscale",
    "latent_upscale_models": "upscale",
    "ultralytics": "detection",
    "yolo": "detection",
    "detection": "detection",
    "sams": "segmentation",
    "background_removal": "segmentation",
    "llm": "llm",
    "bge-m3": "text_encoder",
    "audio_encoders": "audio_encoder",
    "frame_interpolation": "interpolation",
    "optical_flow": "optical_flow",
    "geometry_estimation": "geometry",
    "hypernetworks": "hypernetwork",
    "style_models": "style_model",
    "photomaker": "photomaker",
    "gligen": "gligen",
    "model_patches": "patch",
    "diffusers": "diffusers",
    "configs": "config",
}

#: 顶层目录不认识时的兜底关键词。⚠ **不放 `"sd"` 这种两个字母的键** ——
#: 它会命中任何含 "sd" 的路径（Echo 原来的规则表里就有这条）。
_KIND_KEYS = (
    ("lora", ("loras", "lora", "lycoris")),
    ("controlnet", ("controlnet", "t2i_adapter", "ipadapter")),
    ("upscale", ("upscale", "esrgan", "realesrgan")),
    ("text_encoder", ("text_encoder", "clip", "t5")),
    ("vae", ("vae",)),
    ("embedding", ("embedding", "textual_inversion")),
    ("diffusion", ("diffusion_model", "unet")),
)


def refine_kind(rel_path: str) -> str:
    """模型类型：**先看顶层目录**，认不出再靠关键词，最后是 `unknown`。

    ⚠ Echo 原来的 `guess_kind()`（`echo_launcher/model/manager.py`）**兜底值是
    `"checkpoint"`** —— 于是 `bge-m3/`、`diffusion_models/`、`sams/`、`ultralytics/`、
    `LLM/` 这些**全被显示成 checkpoint**，用户看到的就是"这一列几乎全是 checkpoint，
    而且 `diffusion_models/` 里的明明不是"（2026-10-11 用户指出）。
    **兜底必须是 `unknown`** —— 不知道就别假装知道。
    """
    top = rel_path.replace("\\", "/").split("/")[0].lower()
    if top in _KIND_DIRS:
        return _KIND_DIRS[top]
    low = rel_path.lower()
    for kind, keys in _KIND_KEYS:
        if any(k in low for k in keys):
            return kind
    return "unknown"


# ---------------------------------------------------------------------------
# 缓存表（自建，不动 Echo 的四张表）
# ---------------------------------------------------------------------------
def _conn():
    os.makedirs(os.path.dirname(_DB), exist_ok=True)
    c = sqlite3.connect(_DB, timeout=10)
    c.execute(_SCHEMA)
    if "ver" not in {r[1] for r in c.execute("PRAGMA table_info(model_meta)")}:
        c.execute("ALTER TABLE model_meta ADD COLUMN ver INTEGER DEFAULT 0")  # 老库迁移
    c.commit()
    return c


def _root_key(models_root: str) -> str:
    return os.path.normcase(os.path.abspath(models_root or ""))


def load_all(models_root: str) -> dict:
    """把该根目录下已缓存的探测结果读全，返回 {rel_path: row}。"""
    try:
        with _conn() as c:
            cur = c.execute(
                "SELECT rel_path,size,mtime,fmt,domain,arch,family,purpose,"
                "variant,detail,probe_src,extra,ver FROM model_meta WHERE root_key=?",
                (_root_key(models_root),))
            cols = ("rel_path", "size", "mtime", "fmt", "domain", "arch",
                    "family", "purpose", "variant", "detail", "probe_src", "extra",
                    "ver")
            return {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}
    except Exception:
        return {}


def probe_all(models_root: str, files, report=None, force: bool = False) -> int:
    """批量探测（给 Worker 调）。按 (size, mtime) 命中缓存就跳过。返回本次新探了多少。"""
    cached = load_all(models_root)
    key = _root_key(models_root)
    done = 0
    rows = []
    total = len(files)
    for i, f in enumerate(files, 1):
        if report and (i % 5 == 1 or i == total):
            report(f"[{i}/{total}] {f.rel_path}")
        try:
            st = os.stat(f.abs_path)
            size, mtime = st.st_size, int(st.st_mtime)
        except OSError:
            continue
        old = cached.get(f.rel_path)
        # ⚠ 除了 (size, mtime)，还要比 `ver` —— 规则改了以后 `RULES_V` 会变，
        #   这时就算文件没动也必须重探，否则你会以为新规则没生效。
        if (not force and old and old.get("size") == size
                and old.get("mtime") == mtime and old.get("ver") == RULES_V):
            continue
        if old and old.get("probe_src") == "user":
            continue                     # 用户手改过的不覆盖
        r = probe(f.abs_path, f.rel_path, f.kind)
        rows.append((key, f.rel_path, size, mtime, r["fmt"], r["domain"], r["arch"],
                     r["family"], r["purpose"], r["variant"], r["detail"],
                     r["probe_src"], r["extra"], RULES_V))
        done += 1
    if rows:
        try:
            with _conn() as c:
                c.executemany(
                    "INSERT OR REPLACE INTO model_meta (root_key,rel_path,size,mtime,"
                    "fmt,domain,arch,family,purpose,variant,detail,probe_src,extra,ver)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        except Exception:
            pass
    return done


def set_user(models_root: str, rel_path: str, family: str, purpose: str) -> bool:
    """用户手改：`probe_src` 置 user，之后自动探测不再覆盖它。

    ⚠ **返回是否写成功**（原来是 `except: pass` 静默吞掉）—— 调用方会照实报"已改 N 行"，
    写失败时用户会以为改成功了、刷新一看又变回去。数据库被占用/只读时要能说出来。
    """
    key = _root_key(models_root)
    try:
        with _conn() as c:
            c.execute("INSERT OR IGNORE INTO model_meta (root_key,rel_path) VALUES (?,?)",
                      (key, rel_path))
            c.execute("UPDATE model_meta SET family=?, purpose=?, probe_src='user',"
                      " ver=? WHERE root_key=? AND rel_path=?",
                      (family, purpose, RULES_V, key, rel_path))
        return True
    except Exception:
        return False


def clear_cache(models_root: str = "") -> None:
    try:
        with _conn() as c:
            if models_root:
                c.execute("DELETE FROM model_meta WHERE root_key=?", (_root_key(models_root),))
            else:
                c.execute("DELETE FROM model_meta")
    except Exception:
        pass


def family_options(rows) -> list:
    """从结果里收集出现过的底模（用于筛选下拉），按出现次数降序。"""
    cnt: dict = {}
    for r in rows:
        v = (r.get("family") or "").strip()
        if v:
            cnt[v] = cnt.get(v, 0) + 1
    return [k for k, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))]


def purpose_options(rows) -> list:
    cnt: dict = {}
    for r in rows:
        v = (r.get("purpose") or "").strip()
        if v:
            cnt[v] = cnt.get(v, 0) + 1
    return [k for k, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))]

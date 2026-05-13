"""
photo_reader.py — 模块 B：照片 OCR / Vision 识别模块

支持引擎：
  - paddleocr: 本地 PaddleOCR（默认）
  - claude_vision: Claude Vision API

兼容性：
  - 自动检测依赖是否安装，缺失时给出明确提示
  - HEIC 转换使用临时目录，不污染源文件目录
  - Claude 模型名可通过环境变量 CLAUDE_VISION_MODEL 配置
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic"}
_DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-20250514"

# ---------------------------------------------------------------------------
# 依赖可用性检测（模块导入时执行，不阻塞 import）
# ---------------------------------------------------------------------------

def _check_dep(name: str) -> bool:
    """检测 Python 包是否可导入。"""
    try:
        __import__(name)
        return True
    except ImportError:
        return False


HAS_PADDLEOCR = _check_dep("paddleocr")
HAS_ANTHROPIC = _check_dep("anthropic")
HAS_PILLOW_HEIF = _check_dep("pillow_heif")
HAS_PYHEIF = _check_dep("pyheif")
HAS_DOTENV = _check_dep("dotenv")

# HEIC 需要 pillow-heif 或 pyheif
HAS_HEIC_SUPPORT = HAS_PILLOW_HEIF or HAS_PYHEIF

# ---------------------------------------------------------------------------
# PaddleOCR 实例缓存（模块级单例）
# ---------------------------------------------------------------------------
_ocr_instance: Any = None


def _get_ocr() -> Any:
    """懒加载 PaddleOCR 实例，模块内共享。"""
    global _ocr_instance
    if _ocr_instance is None:
        if not HAS_PADDLEOCR:
            raise RuntimeError(
                "缺少 PaddleOCR 依赖。请先安装:\n"
                "  pip install paddlepaddle paddleocr\n"
                "详见 https://paddlepaddle.org.cn/install/quick"
            )
        from paddleocr import PaddleOCR

        _ocr_instance = PaddleOCR(use_angle_cls=True, lang="ch")
        logger.info("PaddleOCR 实例已初始化")
    return _ocr_instance


# ---------------------------------------------------------------------------
# HEIC 转换辅助
# ---------------------------------------------------------------------------

def _convert_heic_to_jpeg(heic_path: str) -> str:
    """
    将 HEIC 转换为 JPEG，返回转换后文件路径。
    转换结果写入系统临时目录，不污染源文件目录。
    """
    if HAS_PILLOW_HEIF:
        from pillow_heif import register_heif_opener
        from PIL import Image

        register_heif_opener()
        img = Image.open(heic_path)
        # 写到临时目录，避免源目录只读或同名冲突
        fd, jpeg_path = tempfile.mkstemp(suffix=".jpg", prefix="photo_reader_")
        os.close(fd)
        img.save(jpeg_path, "JPEG", quality=95)
        logger.info("HEIC 已转换为 JPEG (pillow-heif): %s -> %s", heic_path, jpeg_path)
        return jpeg_path

    if HAS_PYHEIF:
        import pyheif
        from PIL import Image

        heif_file = pyheif.read(heic_path)
        img = Image.frombytes(
            heif_file.mode,
            heif_file.size,
            heif_file.data,
            "raw",
            heif_file.mode,
            heif_file.stride,
        )
        fd, jpeg_path = tempfile.mkstemp(suffix=".jpg", prefix="photo_reader_")
        os.close(fd)
        img.save(jpeg_path, "JPEG", quality=95)
        logger.info("HEIC 已转换为 JPEG (pyheif): %s -> %s", heic_path, jpeg_path)
        return jpeg_path

    raise RuntimeError(
        "无法处理 HEIC 文件，缺少转换库。请安装:\n"
        "  pip install pillow-heif   # 推荐\n"
        "  # 或\n"
        "  pip install pyheif"
    )


def _prepare_image(image_path: str) -> tuple[str, str]:
    """
    预处理图片路径。

    Args:
        image_path: 原始图片路径

    Returns:
        (可读取的图片路径, 原始文件扩展名小写不含点)
        扩展名用于后续 media_type 推断，HEIC 转换后仍返回 "heic"
        以便调用方知道原始格式。

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 不支持的格式
        RuntimeError: HEIC 转换失败
    """
    p = Path(image_path)
    if not p.exists():
        raise FileNotFoundError(f"图片文件不存在: {image_path}")

    ext = p.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"不支持的图片格式: {ext}，支持: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    orig_ext = ext.lstrip(".")

    if ext == ".heic":
        converted = _convert_heic_to_jpeg(image_path)
        # 返回转换后的路径，但扩展名仍标记为 heic（调用方需要知道原始格式来选 media_type）
        return converted, orig_ext

    return image_path, orig_ext


# ---------------------------------------------------------------------------
# Media type 推断
# ---------------------------------------------------------------------------

def _guess_media_type(ext: str) -> str:
    """根据扩展名返回 MIME 类型。ext 不含点，如 'jpg'。"""
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "heic": "image/heic",  # 实际发送的是转换后的 JPEG，但保留原扩展名语义
    }.get(ext, "image/jpeg")


# ---------------------------------------------------------------------------
# 引擎实现
# ---------------------------------------------------------------------------


def _recognize_paddleocr(image_path: str) -> dict:
    """使用 PaddleOCR 识别图片。"""
    ocr = _get_ocr()
    result = ocr.ocr(image_path, cls=True)

    blocks: list[dict] = []
    text_parts: list[str] = []

    # PaddleOCR 返回格式: [[ [box, (text, conf)], ... ]]
    if result and result[0]:
        for line in result[0]:
            box_coords = line[0]  # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
            text = line[1][0]
            confidence = float(line[1][1])

            blocks.append(
                {
                    "text": text,
                    "confidence": round(confidence, 4),
                    "box": box_coords,
                }
            )
            text_parts.append(text)

    raw_text = "\n".join(text_parts)
    logger.info(
        "PaddleOCR 识别完成: %s, 共 %d 个文本块", image_path, len(blocks)
    )

    return {
        "success": True,
        "raw_text": raw_text,
        "blocks": blocks,
        "engine": "paddleocr",
        "error": None,
    }


def _load_api_key() -> str:
    """
    读取 ANTHROPIC_API_KEY。
    优先级：环境变量 > .env 文件。
    自动加载 .env（如果 python-dotenv 可用）。
    """
    # 尝试自动加载 .env
    if HAS_DOTENV:
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            from dotenv import load_dotenv
            load_dotenv(env_path, override=False)

    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key

    raise RuntimeError(
        "未找到 ANTHROPIC_API_KEY。请通过以下方式之一配置:\n"
        "  1. 设置环境变量: export ANTHROPIC_API_KEY=sk-ant-...\n"
        "  2. 在项目根目录 .env 文件中添加: ANTHROPIC_API_KEY=sk-ant-...\n"
        "  3. 安装 python-dotenv 以支持自动加载 .env: pip install python-dotenv"
    )


def _recognize_claude_vision(image_path: str, orig_ext: str) -> dict:
    """
    使用 Claude Vision API 识别图片。

    Args:
        image_path: 已预处理的图片路径（HEIC 已转换）
        orig_ext: 原始文件扩展名（用于 media_type）
    """
    if not HAS_ANTHROPIC:
        raise RuntimeError(
            "缺少 anthropic 库。请安装:\n"
            "  pip install anthropic"
        )

    api_key = _load_api_key()

    # 读取图片并转 base64
    img_bytes = Path(image_path).read_bytes()
    img_b64 = base64.standard_b64encode(img_bytes).decode("utf-8")

    # HEIC 转换后实际是 JPEG
    if orig_ext == "heic":
        media_type = "image/jpeg"
    else:
        media_type = _guess_media_type(orig_ext)

    prompt_text = (
        "请仔细识别这张图片中的所有文字内容。特别注意：\n"
        "1. 如果包含手写文字，请尽量准确识别\n"
        "2. 将内容整理为结构化的纯文本输出\n"
        "3. 保持原始段落和换行格式\n"
        "4. 对于表格内容，请使用清晰的文本格式表示\n"
        "5. 只输出识别到的文字内容，不要添加额外说明"
    )

    # 模型名可通过环境变量覆盖
    model = os.environ.get("CLAUDE_VISION_MODEL", _DEFAULT_CLAUDE_MODEL)

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt_text,
                    },
                ],
            }
        ],
    )

    raw_text = message.content[0].text if message.content else ""
    logger.info("Claude Vision 识别完成: %s, 文本长度 %d", image_path, len(raw_text))

    # Claude Vision 不返回逐块坐标，整体作为一个 block
    blocks: list[dict] = []
    if raw_text.strip():
        blocks.append(
            {
                "text": raw_text.strip(),
                "confidence": 0.0,  # Claude 不返回置信度
                "box": [],
            }
        )

    return {
        "success": True,
        "raw_text": raw_text,
        "blocks": blocks,
        "engine": "claude_vision",
        "error": None,
    }


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------


def recognize(image_path: str, engine: str = "paddleocr") -> dict:
    """
    照片识别接口

    Args:
        image_path: 图片文件路径（.jpg/.png/.heic）
        engine: 识别引擎 ("paddleocr"/"claude_vision")

    Returns:
        {
            "success": bool,
            "raw_text": str,
            "blocks": list[{"text": str, "confidence": float, "box": list}],
            "engine": str,
            "error": str | None
        }
    """
    try:
        if engine not in ("paddleocr", "claude_vision"):
            return {
                "success": False,
                "raw_text": "",
                "blocks": [],
                "engine": engine,
                "error": f"不支持的引擎: {engine}，可选: paddleocr, claude_vision",
            }

        # 预处理（格式检查 / HEIC 转换），返回 (路径, 原始扩展名)
        img_path, orig_ext = _prepare_image(image_path)

        if engine == "paddleocr":
            return _recognize_paddleocr(img_path)
        else:
            return _recognize_claude_vision(img_path, orig_ext)

    except FileNotFoundError as exc:
        logger.error("文件不存在: %s", exc)
        return {
            "success": False,
            "raw_text": "",
            "blocks": [],
            "engine": engine,
            "error": str(exc),
        }
    except ValueError as exc:
        logger.error("参数错误: %s", exc)
        return {
            "success": False,
            "raw_text": "",
            "blocks": [],
            "engine": engine,
            "error": str(exc),
        }
    except RuntimeError as exc:
        logger.error("运行时错误: %s", exc)
        return {
            "success": False,
            "raw_text": "",
            "blocks": [],
            "engine": engine,
            "error": str(exc),
        }
    except Exception as exc:
        logger.exception("识别过程发生未知错误")
        return {
            "success": False,
            "raw_text": "",
            "blocks": [],
            "engine": engine,
            "error": f"未知错误: {type(exc).__name__}: {exc}",
        }


def batch_recognize(image_paths: list[str], engine: str = "paddleocr") -> list[dict]:
    """
    批量照片识别

    Args:
        image_paths: 图片路径列表
        engine: 识别引擎

    Returns:
        与输入顺序对应的识别结果列表，每个元素结构同 recognize()
    """
    if not image_paths:
        logger.warning("batch_recognize 收到空列表")
        return []

    logger.info("批量识别开始: %d 张图片, 引擎=%s", len(image_paths), engine)
    results: list[dict] = []
    for idx, path in enumerate(image_paths):
        logger.debug("处理第 %d/%d 张: %s", idx + 1, len(image_paths), path)
        result = recognize(path, engine=engine)
        results.append(result)

    succeeded = sum(1 for r in results if r["success"])
    logger.info("批量识别完成: %d/%d 成功", succeeded, len(results))
    return results


def check_dependencies() -> dict:
    """
    检查所有依赖的安装状态，返回诊断信息。
    可在启动时调用，确认功能可用性。

    Returns:
        {
            "paddleocr": {"available": bool, "install_cmd": str},
            "claude_vision": {"available": bool, "install_cmd": str},
            "heic_support": {"available": bool, "install_cmd": str},
            "dotenv": {"available": bool, "install_cmd": str},
        }
    """
    return {
        "paddleocr": {
            "available": HAS_PADDLEOCR,
            "install_cmd": "pip install paddlepaddle paddleocr",
        },
        "claude_vision": {
            "available": HAS_ANTHROPIC,
            "install_cmd": "pip install anthropic",
        },
        "heic_support": {
            "available": HAS_HEIC_SUPPORT,
            "install_cmd": "pip install pillow-heif",
        },
        "dotenv": {
            "available": HAS_DOTENV,
            "install_cmd": "pip install python-dotenv",
        },
    }


# ---------------------------------------------------------------------------
# 测试入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # --- 依赖检查 ---
    print("=== 依赖检查 ===")
    deps = check_dependencies()
    for name, info in deps.items():
        status = "✓ 已安装" if info["available"] else f"✗ 缺失 → {info['install_cmd']}"
        print(f"  {name}: {status}")
    print()

    # --- 单图测试 ---
    if len(sys.argv) >= 2:
        test_path = sys.argv[1]
        test_engine = sys.argv[2] if len(sys.argv) >= 3 else "paddleocr"
        print(f"=== 单图识别: {test_path} (引擎: {test_engine}) ===")
        res = recognize(test_path, engine=test_engine)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        sys.exit(0 if res["success"] else 1)

    # --- 无参数：运行基本功能校验 ---
    print("=== 基本功能校验 ===")

    # 1) 不存在的文件
    r1 = recognize("/nonexistent.jpg")
    assert r1["success"] is False and r1["error"] is not None
    print("✓ 不存在的文件 → 正确返回错误")

    # 2) 不支持的格式
    tmp_xyz = Path(tempfile.mktemp(suffix=".xyz"))
    tmp_xyz.touch()
    r2 = recognize(str(tmp_xyz))
    assert r2["success"] is False and "不支持" in r2["error"]
    tmp_xyz.unlink()
    print("✓ 不支持的格式 → 正确返回错误")

    # 3) 不支持的引擎
    r3 = recognize(str(tmp_xyz), engine="unknown")
    assert r3["success"] is False and "不支持的引擎" in r3["error"]
    print("✓ 不支持的引擎 → 正确返回错误")

    # 4) 空批量
    r4 = batch_recognize([])
    assert r4 == []
    print("✓ 空批量 → 正确返回空列表")

    # 5) 返回值结构校验
    assert set(r1.keys()) == {"success", "raw_text", "blocks", "engine", "error"}
    print("✓ 返回值结构符合接口规范")

    print("\n所有基本校验通过！")
    print("如需端到端测试，请传入实际图片路径: python photo_reader.py <图片路径> [引擎]")

"""
transcriber.py — 模块 A：Whisper 语音转录模块

使用 openai-whisper 库将音频文件转录为文本，支持中英文及粤语。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 模型缓存 ──────────────────────────────────────────────────────────
# key: model_size, value: loaded whisper model object
_model_cache: dict[str, object] = {}

SUPPORTED_EXTENSIONS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg"}
SUPPORTED_MODEL_SIZES = {"small", "medium", "large-v3"}


def _get_model(model_size: str):
    """获取或加载 Whisper 模型（带缓存）"""
    if model_size in _model_cache:
        logger.debug("使用已缓存的 Whisper 模型: %s", model_size)
        return _model_cache[model_size]

    import whisper  # 延迟导入，避免模块级依赖失败

    logger.info("加载 Whisper 模型: %s ...", model_size)
    model = whisper.load_model(model_size)
    _model_cache[model_size] = model
    logger.info("Whisper 模型 %s 加载完成", model_size)
    return model


def transcribe(
    audio_path: str,
    model_size: str = "medium",
    language: str | None = None,
) -> dict:
    """
    音频转录接口

    Args:
        audio_path: 音频文件路径（.m4a/.mp3/.wav/.aac/.flac/.ogg）
        model_size: Whisper 模型大小 ("small"/"medium"/"large-v3")
        language: 语言代码 ("zh"/"yue"/None=自动检测)

    Returns:
        {
            "success": bool,
            "text": str,           # 完整转录文本
            "segments": list[      # 分段时间戳
                {"start": float, "end": float, "text": str}
            ],
            "language": str,       # 检测到的语言
            "duration": float,     # 音频总时长(秒)
            "error": str | None    # 错误信息
        }
    """
    # ── 参数校验 ────────────────────────────────────────────────────
    if model_size not in SUPPORTED_MODEL_SIZES:
        return {
            "success": False,
            "text": "",
            "segments": [],
            "language": "",
            "duration": 0.0,
            "error": f"不支持的模型大小: '{model_size}'，可选: {sorted(SUPPORTED_MODEL_SIZES)}",
        }

    if language is not None and language not in {"zh", "yue"}:
        return {
            "success": False,
            "text": "",
            "segments": [],
            "language": "",
            "duration": 0.0,
            "error": f"不支持的语言: '{language}'，可选: 'zh', 'yue', None(自动检测)",
        }

    audio_path = os.fspath(audio_path)
    ext = Path(audio_path).suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        return {
            "success": False,
            "text": "",
            "segments": [],
            "language": "",
            "duration": 0.0,
            "error": f"不支持的音频格式: '{ext}'，可选: {sorted(SUPPORTED_EXTENSIONS)}",
        }

    if not os.path.isfile(audio_path):
        return {
            "success": False,
            "text": "",
            "segments": [],
            "language": "",
            "duration": 0.0,
            "error": f"音频文件不存在: {audio_path}",
        }

    # ── 转录 ───────────────────────────────────────────────────────
    try:
        model = _get_model(model_size)

        logger.info("开始转录: %s (语言=%s)", audio_path, language or "自动检测")
        # whisper 的 language 参数: None = 自动检测
        result = model.transcribe(audio_path, language=language, verbose=False)

        # 构建 segments
        segments = []
        for seg in result.get("segments", []):
            segments.append({
                "start": round(seg["start"], 3),
                "end": round(seg["end"], 3),
                "text": seg["text"].strip(),
            })

        detected_language = result.get("language", "")

        # 计算音频时长：取最后一个 segment 的 end，或从 segments 推算
        duration = 0.0
        if segments:
            duration = round(segments[-1]["end"], 3)

        full_text = result.get("text", "").strip()

        logger.info(
            "转录完成: 语言=%s, 时长=%.1fs, 文本长度=%d",
            detected_language, duration, len(full_text),
        )

        return {
            "success": True,
            "text": full_text,
            "segments": segments,
            "language": detected_language,
            "duration": duration,
            "error": None,
        }

    except Exception as exc:
        logger.exception("转录失败: %s", audio_path)
        return {
            "success": False,
            "text": "",
            "segments": [],
            "language": "",
            "duration": 0.0,
            "error": f"转录异常: {type(exc).__name__}: {exc}",
        }


# ── 测试 ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    import unittest
    from unittest.mock import MagicMock, patch

    logging.basicConfig(level=logging.DEBUG, format="%(name)s | %(levelname)s | %(message)s")

    # whisper 未安装时，注入一个 mock 模块到 sys.modules 以便 @patch 生效
    if "whisper" not in sys.modules:
        _fake_whisper = MagicMock()
        sys.modules["whisper"] = _fake_whisper

    class TestTranscribe(unittest.TestCase):
        """transcribe() 单元测试（mock）"""

        def setUp(self):
            """每个测试前清空模型缓存，确保测试隔离"""
            _model_cache.clear()

        # ── 参数校验 ────────────────────────────────────────────────
        def test_invalid_model_size(self):
            result = transcribe("test.wav", model_size="tiny")
            self.assertFalse(result["success"])
            self.assertIn("不支持的模型大小", result["error"])

        def test_invalid_language(self):
            result = transcribe("test.wav", language="en")
            self.assertFalse(result["success"])
            self.assertIn("不支持的语言", result["error"])

        def test_unsupported_format(self):
            result = transcribe("test.txt")
            self.assertFalse(result["success"])
            self.assertIn("不支持的音频格式", result["error"])

        def test_file_not_found(self):
            result = transcribe("/nonexistent/audio.wav")
            self.assertFalse(result["success"])
            self.assertIn("文件不存在", result["error"])

        # ── 正常转录流程 ────────────────────────────────────────────
        @patch("os.path.isfile", return_value=True)
        @patch("whisper.load_model")
        def test_successful_transcribe(self, mock_load, mock_isfile):
            mock_model = MagicMock()
            mock_model.transcribe.return_value = {
                "text": "你好世界",
                "language": "zh",
                "segments": [
                    {"start": 0.0, "end": 1.5, "text": " 你好"},
                    {"start": 1.5, "end": 3.2, "text": " 世界"},
                ],
            }
            mock_load.return_value = mock_model

            result = transcribe("/tmp/test.wav", model_size="small", language="zh")

            self.assertTrue(result["success"])
            self.assertEqual(result["text"], "你好世界")
            self.assertEqual(result["language"], "zh")
            self.assertAlmostEqual(result["duration"], 3.2)
            self.assertEqual(len(result["segments"]), 2)
            self.assertAlmostEqual(result["segments"][0]["start"], 0.0)
            self.assertAlmostEqual(result["segments"][1]["end"], 3.2)
            self.assertIsNone(result["error"])

        # ── 模型缓存 ────────────────────────────────────────────────
        @patch("os.path.isfile", return_value=True)
        @patch("whisper.load_model")
        def test_model_caching(self, mock_load, mock_isfile):
            mock_model = MagicMock()
            mock_model.transcribe.return_value = {
                "text": "测试缓存",
                "language": "zh",
                "segments": [{"start": 0.0, "end": 1.0, "text": "测试缓存"}],
            }
            mock_load.return_value = mock_model

            transcribe("a.wav", model_size="medium")
            transcribe("b.wav", model_size="medium")

            # medium 模型只应加载一次
            self.assertEqual(mock_load.call_count, 1)
            self.assertIn("medium", _model_cache)

        # ── 转录异常处理 ────────────────────────────────────────────
        @patch("os.path.isfile", return_value=True)
        @patch("whisper.load_model")
        def test_transcribe_exception(self, mock_load, mock_isfile):
            mock_model = MagicMock()
            mock_model.transcribe.side_effect = RuntimeError("GPU OOM")
            mock_load.return_value = mock_model

            result = transcribe("/tmp/crash.wav")
            self.assertFalse(result["success"])
            self.assertIn("RuntimeError", result["error"])
            self.assertIn("GPU OOM", result["error"])

        # ── 自动检测语言 ────────────────────────────────────────────
        @patch("os.path.isfile", return_value=True)
        @patch("whisper.load_model")
        def test_auto_detect_language(self, mock_load, mock_isfile):
            mock_model = MagicMock()
            mock_model.transcribe.return_value = {
                "text": "hello world",
                "language": "en",
                "segments": [{"start": 0.0, "end": 1.0, "text": "hello world"}],
            }
            mock_load.return_value = mock_model

            result = transcribe("/tmp/en.wav", language=None)
            self.assertTrue(result["success"])
            mock_model.transcribe.assert_called_once_with(
                "/tmp/en.wav", language=None, verbose=False,
            )

        # ── 粤语支持 ───────────────────────────────────────────────
        @patch("os.path.isfile", return_value=True)
        @patch("whisper.load_model")
        def test_cantonese_language(self, mock_load, mock_isfile):
            mock_model = MagicMock()
            mock_model.transcribe.return_value = {
                "text": "你好世界",
                "language": "yue",
                "segments": [{"start": 0.0, "end": 2.0, "text": "你好世界"}],
            }
            mock_load.return_value = mock_model

            result = transcribe("/tmp/yue.wav", model_size="large-v3", language="yue")
            self.assertTrue(result["success"])
            self.assertEqual(result["language"], "yue")
            mock_model.transcribe.assert_called_once_with(
                "/tmp/yue.wav", language="yue", verbose=False,
            )

    unittest.main()

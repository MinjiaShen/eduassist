# EduAssist 共享接口规范 v1.0
# 所有模块必须严格遵守此规范，确保模块间一致性

## 1. 项目根目录
```
/root/.openclaw/workspace/eduassist/
```

## 2. 模块接口定义

### 2.1 transcriber.py — 模块 A

```python
def transcribe(audio_path: str, model_size: str = "medium", language: str | None = None) -> dict:
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
```

### 2.2 photo_reader.py — 模块 B

```python
def recognize(image_path: str, engine: str = "paddleocr") -> dict:
    """
    照片识别接口
    Args:
        image_path: 图片文件路径（.jpg/.png/.heic）
        engine: 识别引擎 ("paddleocr"/"claude_vision")
    Returns:
        {
            "success": bool,
            "raw_text": str,       # 原始识别文本
            "blocks": list[        # 文本块（含位置信息）
                {"text": str, "confidence": float, "box": list}
            ],
            "engine": str,         # 使用的引擎名
            "error": str | None
        }
    """

def batch_recognize(image_paths: list[str], engine: str = "paddleocr") -> list[dict]:
    """
    批量照片识别
    Args:
        image_paths: 图片路径列表
        engine: 识别引擎
    Returns:
        与输入顺序对应的识别结果列表，每个元素结构同 recognize()
    """
```

### 2.3 marker_parser.py — 模块 C

```python
def load_markers(config_path: str = "config/markers.yaml") -> dict:
    """
    加载标记配置
    Returns:
        {
            "markers": [
                {"symbol": str, "field": str, "type": str, "description": str}
            ],
            "output_template": str
        }
    """

def parse_text(text: str, markers_config: dict | None = None) -> dict:
    """
    解析文本中的自定义标记
    Args:
        text: 待解析文本（来自转录或 OCR）
        markers_config: 标记配置（None 则自动加载）
    Returns:
        {
            "fields": {
                "<field_name>": str,   # 每个字段提取的内容
                ...
            },
            "raw_text": str,           # 原始文本
            "matched_markers": list,   # 匹配到的标记列表
            "unmatched_sections": list # 未匹配的段落
        }
    """

def reload_config():
    """重载标记配置（热更新）"""
```

### 2.4 post_processor.py — 整合引擎

```python
def generate_case(fields: dict, template_path: str = "templates/case_template.md") -> str:
    """
    用模板渲染结构化医案
    Args:
        fields: 从 marker_parser.parse_text() 得到的 fields
        template_path: Jinja2 模板路径
    Returns:
        渲染后的 Markdown 文本
    """

def export_docx(markdown_text: str, output_path: str) -> str:
    """
    将 Markdown 转为 DOCX
    Args:
        markdown_text: Markdown 文本
        output_path: 输出文件路径
    Returns:
        输出文件的完整路径
    """

def save_output(content: str, filename: str, fmt: str = "md") -> str:
    """
    保存输出文件到 output/ 目录
    Args:
        content: 文件内容
        filename: 文件名（不含扩展名）
        fmt: 格式 ("md"/"docx"/"txt")
    Returns:
        输出文件的完整路径
    """
```

## 3. 共享约定

### 3.1 错误处理
- 所有函数不抛异常，通过返回值中的 `success` 和 `error` 字段报告错误
- 内部异常必须 try/except 捕获，转换为 error 字符串

### 3.2 路径约定
- 上传文件临时目录: `uploads/`
- 输出目录: `output/`
- 配置目录: `config/`
- 模板目录: `templates/`
- 所有路径基于项目根目录 `/root/.openclaw/workspace/eduassist/`

### 3.3 日志
- 使用 Python 标准 `logging` 模块
- 模块级 logger: `logger = logging.getLogger(__name__)`

### 3.4 编码
- 所有文件 UTF-8
- 字符串处理统一使用 Python 3.10+ 类型注解

### 3.5 依赖
- 仅使用 requirements.txt 中声明的依赖
- 如需新增依赖，先更新 requirements.txt

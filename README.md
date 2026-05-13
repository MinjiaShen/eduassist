# 🩺 EduAssist — 中医医案辅助记录工具

自动化笔记处理流水线：音频转录、照片识别、标记解析、结构化医案生成。

## 功能

| 模块 | 功能 | 技术 |
|------|------|------|
| 🎙️ 音频转录 | 本地音频 → 中文逐字稿 | OpenAI Whisper |
| 📷 照片识别 | 处方/病历照片 → 文字 | PaddleOCR / Claude Vision |
| ⚙️ 标记解析 | 自定义标记 → 结构化字段 | YAML 配置 + 正则解析 |
| 📝 医案生成 | 字段 → 标准五部分医案 | Jinja2 模板渲染 |

## 医案格式

依据「优秀中医临床人才研修项目医案撰写要求」，输出标准五部分结构：

1. **病人一般情况和诊疗过程** — 患者信息、主诉、四诊、诊断
2. **辨证分析与立法** — 辨证思路、治则、方剂
3. **处方** — 内服方、外用药、取穴
4. **医嘱** — 饮食宜忌、起居调摄
5. **体会** — 临证心得

支持复诊记录（二诊、三诊…）自动识别。

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务
python app.py

# 浏览器访问
http://localhost:5000
```

## 目录结构

```
eduassist/
├── app.py                    # Flask 主入口
├── requirements.txt          # Python 依赖
├── config/
│   └── markers.yaml          # 自定义标记配置（23 个标记）
├── templates/
│   ├── index.html            # Web UI（三标签页）
│   └── case_template.md      # 医案 Jinja2 模板
├── modules/
│   ├── transcriber.py        # 模块 A：Whisper 语音转录
│   ├── photo_reader.py       # 模块 B：OCR / Vision 照片识别
│   ├── marker_parser.py      # 模块 C：标记解析器
│   └── post_processor.py     # 模块 D：整合引擎
└── static/
    └── style.css             # UI 样式
```

## API 接口

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/transcribe` | 音频转录 |
| POST | `/api/recognize` | 单图识别 |
| POST | `/api/batch_recognize` | 批量识别 |
| POST | `/api/parse` | 标记解析 |
| POST | `/api/generate` | 医案生成 |
| POST | `/api/export` | 导出文件 |
| GET | `/api/markers` | 获取标记配置 |
| POST | `/api/markers/save` | 保存配置 |
| POST | `/api/markers/reload` | 重载配置 |
| GET | `/api/download/<filename>` | 下载文件 |

## 自定义标记

编辑 `config/markers.yaml`，支持三种标记类型：

- **section_header** — 段落标记（如 `【主诉】`）
- **inline_tag** — 行内标记（如 `△足三里`）
- **highlight** — 高亮标记（如 `★重点症状`）

Web 界面中点击「重载配置」即可生效，无需重启服务。

## 隐私说明

- 音频转录完全本地运行，数据不离本机
- PaddleOCR 识别完全本地运行
- Claude Vision 需要调用 Anthropic API（请评估隐私风险）
- 上传文件临时存放于 `uploads/`，处理后建议清理

## License

MIT

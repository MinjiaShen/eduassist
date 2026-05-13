# EduAssist 本地运行

## 启动

```bash
git clone https://github.com/MinjiaShen/eduassist.git
cd eduassist
pip install -r requirements.txt
python app.py
```

浏览器打开 **http://127.0.0.1:5000**

## 可选功能

```bash
pip install openai-whisper          # 语音转录
pip install paddlepaddle paddleocr  # 本地 OCR
pip install anthropic               # Claude Vision 高精度识别
pip install pillow-heif             # iPhone HEIC 图片
```

不安装不影响页面使用，对应功能会提示缺少依赖。

## 配置（.env，可选）

```bash
ANTHROPIC_API_KEY=sk-ant-xxx  # Claude Vision 需要
FLASK_HOST=127.0.0.1          # 改 0.0.0.0 可局域网访问
FLASK_PORT=5000
FLASK_DEBUG=false
```

## 常见问题

| 问题 | 解决 |
|------|------|
| pip 权限错误 | `python3 -m venv venv && source venv/bin/activate` |
| venv 创建失败 (Ubuntu) | `sudo apt install python3.12-venv` |
| 端口被占用 | `FLASK_PORT=5001 python app.py` |
| 转录慢 | 模型选 `small` 测试，正式用 `medium` |

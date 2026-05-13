# 更新日志

## [1.1.0] — 2026-05-13

### 安全修复（Critical）
- **B-01** 移除硬编码 `debug=True` 和 `host="0.0.0.0"`，改为环境变量 `FLASK_DEBUG`/`FLASK_HOST`/`FLASK_PORT` 控制，默认仅本机访问
- **B-02** 所有 API 路由在 `finally` 块中自动清理 `uploads/` 临时文件，防止存储泄漏和隐私风险
- **B-03** 移除 `INTERFACES.md` 中硬编码的绝对路径，改为 `Path(__file__).resolve().parent` 动态解析

### 功能缺陷修复（High）
- **B-04** CORS 从全域开放限制为 `localhost:5000` + `127.0.0.1:5000`
- **B-05** `/api/markers/save` 写入前增加 YAML 语法校验，失败返回错误；写入前自动备份，失败时回滚
- **B-06** `/api/download/<filename>` 增加 `resolve()` + 路径前缀检查，防止路径穿越攻击
- **B-07** 音频转录改为后台线程异步执行，新增 `/api/task/<task_id>` 轮询接口，解决长音频请求超时问题

### 逻辑修复（Medium）
- **B-08** `requirements.txt` 核心/可选依赖分离，移除强制安装的 ML 大包
- **B-09** `app.py` 调用模块后显式检查返回值 `success` 字段，与 `INTERFACES.md` 规范一致
- **B-10** 批量识别中单文件失败不再中止整批，改为记录到 `skipped` 列表继续处理

### 优化（Low）
- **B-11** 引入 `MAX_TASK_AGE` 机制，自动清理超过 1 小时的已完成任务，防止内存泄漏
- **O-03** 新增 `modules/__init__.py`，确保包导入在所有 Python 版本下正常工作
- **O-05** 新增 `/health` 健康检查端点，返回各模块加载状态
- **O-06** `photo_reader.py` 自动注册 HEIC 格式支持（需安装 `pillow-heif`）

### 文档
- 新增 `QUICKSTART.md` 本地运行指南
- 更新 `INTERFACES.md` 接口规范，明确错误处理约定
- 更新 `.env.example` 补充 Flask 服务器配置项

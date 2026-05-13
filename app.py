"""
EduAssist — Flask 主入口
医疗辅助 Web 应用：音频转录、照片识别、标记解析、医案生成
"""

import os
import sys
import uuid
import time
import logging
import threading
from pathlib import Path

import yaml
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

# ── 基础设置 ──────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"
CONFIG_DIR = BASE_DIR / "config"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("eduassist")

# ── 确保项目根在 sys.path，方便 modules 导入 ─────────────
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ── 安全导入各模块（允许部分模块尚未就绪）─────────────────
transcriber = None
photo_reader = None
marker_parser = None
post_processor = None

try:
    from modules import transcriber
except ImportError as e:
    logger.warning("transcriber 模块未就绪: %s", e)

try:
    from modules import photo_reader
except ImportError as e:
    logger.warning("photo_reader 模块未就绪: %s", e)

try:
    from modules import marker_parser
except ImportError as e:
    logger.warning("marker_parser 模块未就绪: %s", e)

try:
    from modules import post_processor
except ImportError as e:
    logger.warning("post_processor 模块未就绪: %s", e)

# ── Flask 应用 ────────────────────────────────────────────
app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB
CORS(app, origins=["http://localhost:5000", "http://127.0.0.1:5000"])

ALLOWED_AUDIO = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg"}
ALLOWED_IMAGE = {".jpg", ".jpeg", ".png", ".heic"}

# ── 任务队列（B-07 + B-11）───────────────────────────────
tasks = {}  # task_id -> {"status", "result", "created_at", ...}
tasks_lock = threading.Lock()
MAX_TASK_AGE = 3600  # 秒


def _cleanup_old_tasks():
    """清理超过 MAX_TASK_AGE 的已完成任务，防止内存泄漏"""
    now = time.time()
    with tasks_lock:
        expired = [
            tid for tid, t in tasks.items()
            if t.get("status") in ("done", "error") and now - t["created_at"] > MAX_TASK_AGE
        ]
        for tid in expired:
            del tasks[tid]
    if expired:
        logger.info("已清理 %d 个过期任务", len(expired))


def _remove_file(path: str | None):
    """安全删除临时文件"""
    if path:
        try:
            p = Path(path)
            if p.exists():
                p.unlink()
                logger.debug("已清理临时文件: %s", path)
        except OSError as e:
            logger.warning("清理临时文件失败 %s: %s", path, e)


def _run_transcribe_task(task_id: str, audio_path: str, model_size: str, language: str | None):
    """后台线程：执行转录任务"""
    try:
        result = transcriber.transcribe(
            audio_path, model_size=model_size, language=language
        )
        with tasks_lock:
            if task_id in tasks:
                if result.get("success"):
                    tasks[task_id]["status"] = "done"
                    tasks[task_id]["result"] = result
                else:
                    tasks[task_id]["status"] = "error"
                    tasks[task_id]["error"] = result.get("error", "转录失败")
    except Exception as e:
        logger.exception("后台转录失败 [%s]", task_id)
        with tasks_lock:
            if task_id in tasks:
                tasks[task_id]["status"] = "error"
                tasks[task_id]["error"] = str(e)
    finally:
        _remove_file(audio_path)
        _cleanup_old_tasks()


# ── 工具函数 ──────────────────────────────────────────────
def ok(data=None):
    """成功响应"""
    payload = {"success": True}
    if data is not None:
        payload.update(data)
    return jsonify(payload)


def fail(error: str, status: int = 400):
    """失败响应"""
    return jsonify({"success": False, "error": error}), status


def save_upload(file_storage, allowed_exts: set[str]) -> Path:
    """保存上传文件，返回保存路径；格式不对则抛 ValueError"""
    filename = file_storage.filename
    ext = Path(filename).suffix.lower()
    if ext not in allowed_exts:
        raise ValueError(f"不支持的文件格式: {ext}")
    unique_name = f"{uuid.uuid4().hex}{ext}"
    save_path = UPLOAD_DIR / unique_name
    file_storage.save(str(save_path))
    logger.info("文件已保存: %s → %s", filename, save_path)
    return save_path


# ── 页面路由 ──────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


# ── 健康检查（O-05）──────────────────────────────────────
@app.route("/health")
def health():
    modules_status = {
        "transcriber": transcriber is not None,
        "photo_reader": photo_reader is not None,
        "marker_parser": marker_parser is not None,
        "post_processor": post_processor is not None,
    }
    all_ok = all(modules_status.values())
    return jsonify({
        "success": True,
        "status": "healthy" if all_ok else "degraded",
        "modules": modules_status,
    }), 200 if all_ok else 200


# ── 音频转录（B-07: 异步 + B-02: 临时文件清理 + B-09: success 检查）──
@app.route("/api/transcribe", methods=["POST"])
def api_transcribe():
    if transcriber is None:
        return fail("transcriber 模块未加载，无法使用转录功能", 503)

    audio = request.files.get("audio")
    if not audio:
        return fail("请上传音频文件")

    model_size = request.form.get("model_size", "medium")
    language = request.form.get("language") or None  # 空字符串→None

    audio_path = None
    try:
        audio_path = save_upload(audio, ALLOWED_AUDIO)
    except ValueError as e:
        return fail(str(e))

    # 生成 task_id，启动后台线程
    task_id = uuid.uuid4().hex
    with tasks_lock:
        tasks[task_id] = {
            "status": "pending",
            "created_at": time.time(),
            "result": None,
            "error": None,
        }

    thread = threading.Thread(
        target=_run_transcribe_task,
        args=(task_id, str(audio_path), model_size, language),
        daemon=True,
    )
    thread.start()

    logger.info("转录任务已提交: %s", task_id)
    return jsonify({"success": True, "task_id": task_id}), 202


# ── 任务状态查询（B-07）──────────────────────────────────
@app.route("/api/task/<task_id>")
def api_task_status(task_id):
    with tasks_lock:
        task = tasks.get(task_id)
    if not task:
        return fail("任务不存在", 404)
    resp = {"task_id": task_id, "status": task["status"]}
    if task["status"] == "done":
        resp["result"] = task["result"]
    elif task["status"] == "error":
        resp["error"] = task["error"]
    return jsonify({"success": True, **resp})


# ── 照片识别（B-02: 临时文件清理 + B-09: success 检查）──
@app.route("/api/recognize", methods=["POST"])
def api_recognize():
    if photo_reader is None:
        return fail("photo_reader 模块未加载，无法使用识别功能", 503)

    image = request.files.get("image")
    if not image:
        return fail("请上传图片文件")

    engine = request.form.get("engine", "paddleocr")

    image_path = None
    try:
        image_path = save_upload(image, ALLOWED_IMAGE)
        result = photo_reader.recognize(str(image_path), engine=engine)
        if not result.get("success"):
            return fail(result.get("error", "识别失败"), 500)
        return ok(result)
    except ValueError as e:
        return fail(str(e))
    except Exception as e:
        logger.exception("识别失败")
        return fail(f"识别失败: {e}", 500)
    finally:
        _remove_file(str(image_path) if image_path else None)


# ── 批量识别（B-10: 单文件跳过 + B-02: 临时文件清理）───
@app.route("/api/batch_recognize", methods=["POST"])
def api_batch_recognize():
    if photo_reader is None:
        return fail("photo_reader 模块未加载，无法使用识别功能", 503)

    images = request.files.getlist("images")
    if not images:
        return fail("请上传至少一张图片")

    engine = request.form.get("engine", "paddleocr")

    paths = []
    skipped = []
    for img in images:
        try:
            p = save_upload(img, ALLOWED_IMAGE)
            paths.append(str(p))
        except ValueError as e:
            skipped.append({"filename": img.filename, "reason": str(e)})

    if not paths:
        return fail("没有可处理的有效图片")

    try:
        results = photo_reader.batch_recognize(paths, engine=engine)
        return ok({"results": results, "skipped": skipped})
    except Exception as e:
        logger.exception("批量识别失败")
        return fail(f"批量识别失败: {e}", 500)
    finally:
        for p in paths:
            _remove_file(p)


# ── 标记解析 ──────────────────────────────────────────────
@app.route("/api/parse", methods=["POST"])
def api_parse():
    if marker_parser is None:
        return fail("marker_parser 模块未加载", 503)

    data = request.get_json(silent=True) or {}
    text = data.get("text", "").strip()
    if not text:
        return fail("请提供待解析文本")

    try:
        result = marker_parser.parse_text(text)
        return ok(result)
    except Exception as e:
        logger.exception("解析失败")
        return fail(f"解析失败: {e}", 500)


# ── 医案生成 ──────────────────────────────────────────────
@app.route("/api/generate", methods=["POST"])
def api_generate():
    if post_processor is None:
        return fail("post_processor 模块未加载", 503)

    data = request.get_json(silent=True) or {}
    fields = data.get("fields", {})
    if not fields:
        return fail("请提供字段数据")

    try:
        md = post_processor.generate_case(fields)
        return ok({"markdown": md})
    except Exception as e:
        logger.exception("生成医案失败")
        return fail(f"生成失败: {e}", 500)


# ── 导出文件 ──────────────────────────────────────────────
@app.route("/api/export", methods=["POST"])
def api_export():
    if post_processor is None:
        return fail("post_processor 模块未加载", 503)

    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    filename = data.get("filename", "case_output")
    fmt = data.get("format", "md")

    if not content:
        return fail("请提供导出内容")

    try:
        output_path = post_processor.save_output(content, filename, fmt)
        out_name = Path(output_path).name
        return ok({"filename": out_name, "path": output_path})
    except Exception as e:
        logger.exception("导出失败")
        return fail(f"导出失败: {e}", 500)


# ── 标记配置管理 ──────────────────────────────────────────
@app.route("/api/markers", methods=["GET"])
def api_markers_get():
    config_path = CONFIG_DIR / "markers.yaml"
    if not config_path.exists():
        return fail("标记配置文件不存在", 404)
    return ok({"content": config_path.read_text(encoding="utf-8")})


@app.route("/api/markers/save", methods=["POST"])
def api_markers_save():
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    if not content:
        return fail("请提供配置内容")

    # B-05: YAML 语法校验
    try:
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        return fail(f"YAML 语法错误: {e}")

    config_path = CONFIG_DIR / "markers.yaml"
    backup_path = CONFIG_DIR / "markers.yaml.bak"

    # B-05: 写入前备份
    try:
        if config_path.exists():
            backup_path.write_bytes(config_path.read_bytes())
            logger.info("已备份标记配置到 %s", backup_path)
    except Exception as e:
        logger.warning("备份标记配置失败: %s", e)

    # 写入新配置，失败则回滚
    try:
        config_path.write_text(content, encoding="utf-8")
        logger.info("标记配置已保存")
        return ok({"message": "配置已保存"})
    except Exception as e:
        logger.exception("保存配置失败")
        # 回滚：从备份恢复
        if backup_path.exists():
            try:
                config_path.write_bytes(backup_path.read_bytes())
                logger.info("已从备份回滚标记配置")
            except Exception as rb_err:
                logger.error("回滚失败: %s", rb_err)
        return fail(f"保存失败: {e}", 500)


@app.route("/api/markers/reload", methods=["POST"])
def api_markers_reload():
    if marker_parser is None:
        return fail("marker_parser 模块未加载", 503)

    try:
        marker_parser.reload_config()
        logger.info("标记配置已重载")
        return ok({"message": "配置已重载"})
    except Exception as e:
        logger.exception("重载配置失败")
        return fail(f"重载失败: {e}", 500)


# ── 文件下载（B-06: 路径穿越防护）────────────────────────
@app.route("/api/download/<filename>")
def api_download(filename):
    safe_name = Path(filename).name
    file_path = (OUTPUT_DIR / safe_name).resolve()
    if not str(file_path).startswith(str(OUTPUT_DIR.resolve())):
        return fail("非法路径", 403)
    if not file_path.exists():
        return fail("文件不存在", 404)
    return send_from_directory(str(OUTPUT_DIR), safe_name, as_attachment=True)


# ── 错误处理 ──────────────────────────────────────────────
@app.errorhandler(413)
def too_large(e):
    return fail("上传文件过大，最大 200MB", 413)


@app.errorhandler(404)
def not_found(e):
    return fail("资源不存在", 404)


@app.errorhandler(500)
def server_error(e):
    return fail("服务器内部错误", 500)


# ── 启动（B-01: 环境变量控制 debug）──────────────────────
if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_PORT", "5000"))
    app.run(host=host, port=port, debug=debug_mode)

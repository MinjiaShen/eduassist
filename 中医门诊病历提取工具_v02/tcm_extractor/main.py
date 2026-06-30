#!/usr/bin/env python3
# =============================================================================
#  main.py — 入口脚本
#
#  用法：
#    单张图片:      python main.py 1_1.jpg
#    批量目录:      python main.py ./images/
#    持续监听目录:  python main.py ./images/ --watch
#    指定输出文件:  python main.py ./images/ --output 我的记录.xlsx
# =============================================================================

import sys
import os
import time
import logging
import argparse
from pathlib import Path

# ── 日志配置（在 import config 之前设置）────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("run.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

import config
import extractor
import excel_writer

# ── 支持的图片扩展名 ──────────────────────────────────────────────────────────
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def process_single(image_path: str) -> bool:
    """处理单张图片，返回是否成功写入。"""
    path = Path(image_path)
    if not path.exists():
        logger.error(f"文件不存在: {image_path}")
        return False
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        logger.warning(f"不支持的文件类型: {image_path}")
        return False

    record = extractor.extract_from_image(str(path))
    if record is None:
        return False

    written = excel_writer.append_record(record)
    return written


def process_directory(dir_path: str) -> dict:
    """
    处理目录下所有图片，按文件名前缀自动分组（同一患者的多张截图合并）。
    返回统计 {"total": N, "written": N, "skipped": N, "failed": N}
    """
    records = extractor.extract_from_directory(dir_path)

    if not records:
        return {"total": 0, "written": 0, "skipped": 0, "failed": 0}

    stats = {"total": len(records), "written": 0, "skipped": 0, "failed": 0}

    for record in records:
        if record is None:
            stats["failed"] += 1
            continue
        try:
            written = excel_writer.append_record(record)
            if written:
                stats["written"] += 1
            else:
                stats["skipped"] += 1
        except Exception as e:
            logger.error(f"写入异常: {e}")
            stats["failed"] += 1

    return stats


def watch_directory(dir_path: str, interval: int = 5):
    """
    持续监听目录，发现新图片自动处理。
    Ctrl+C 退出。
    （轻量实现，不依赖 watchdog 库）
    """
    d = Path(dir_path)
    if not d.is_dir():
        logger.error(f"不是有效目录: {dir_path}")
        return

    logger.info(f"开始监听目录: {d.resolve()}  (每 {interval}s 扫描一次，Ctrl+C 退出)")
    processed = set()

    # 把已有文件标记为已处理（不重复处理历史图片）
    for p in d.iterdir():
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
            processed.add(p.name)
    logger.info(f"已忽略现有 {len(processed)} 张图片，等待新文件...")

    try:
        while True:
            time.sleep(interval)
            current = {
                p.name: p
                for p in d.iterdir()
                if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
            }
            new_files = [p for name, p in current.items() if name not in processed]

            for img_path in sorted(new_files):
                logger.info(f"发现新文件: {img_path.name}")
                # 等待文件写入完成（避免处理到一半的截图）
                time.sleep(1)
                record = extractor.extract_from_image(str(img_path))
                if record is not None:
                    try:
                        excel_writer.append_record(record)
                    except Exception as e:
                        logger.error(f"写入异常: {e}")
                processed.add(img_path.name)

    except KeyboardInterrupt:
        logger.info("监听已停止。")


def print_banner():
    print("=" * 60)
    print("  中医门诊病历图像提取工具（RapidOCR 本地版）")
    print(f"  输出文件: {config.OUTPUT_EXCEL}")
    print(f"  OCR引擎: RapidOCR (ONNX Runtime)")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="从门诊系统截图中提取病历信息并写入 Excel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py photo.jpg                    # 单张图片
  python main.py ./截图/                      # 整个目录
  python main.py ./截图/ --watch              # 持续监听
  python main.py ./截图/ --output 6月记录.xlsx
        """,
    )
    parser.add_argument(
        "input",
        help="图片文件路径 或 图片所在目录路径",
    )
    parser.add_argument(
        "--watch", "-w",
        action="store_true",
        help="持续监听目录，发现新图片自动处理（仅目录模式有效）",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help=f"输出 Excel 文件路径（默认: {config.OUTPUT_EXCEL}）",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=5,
        help="监听模式下的扫描间隔秒数（默认: 5）",
    )

    args = parser.parse_args()

    # 覆盖配置
    if args.output:
        config.OUTPUT_EXCEL = args.output

    print_banner()
    input_path = Path(args.input)

    # ── 单张图片 ──────────────────────────────────────────────────────────────
    if input_path.is_file():
        logger.info(f"单张模式: {input_path.name}")
        success = process_single(str(input_path))
        if success:
            print(f"\n[OK] 处理完成，已写入 {config.OUTPUT_EXCEL}")
        else:
            print(f"\n[WARN] 处理失败，请查看 {config.ERROR_LOG}")

    # ── 目录模式 ──────────────────────────────────────────────────────────────
    elif input_path.is_dir():
        if args.watch:
            watch_directory(str(input_path), interval=args.interval)
        else:
            stats = process_directory(str(input_path))
            print(f"""
{'=' * 50}
  [OK] 批量处理完成
  总计: {stats.get('total', 0)} 张
  写入: {stats.get('written', 0)} 条
  跳过(重复): {stats.get('skipped', 0)} 条
  失败: {stats.get('failed', 0)} 条
  输出文件: {config.OUTPUT_EXCEL}
{'=' * 50}
""")

    else:
        print(f"[ERROR] 路径不存在或无法识别: {args.input}")
        sys.exit(1)


if __name__ == "__main__":
    main()

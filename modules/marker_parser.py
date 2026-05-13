"""
marker_parser.py — 模块 C：自定义标记解析器
从文本中提取结构化字段，支持 section_header / inline_tag / highlight 三种标记类型。
"""

import logging
import os
import re
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 模块级缓存配置
_cached_config: dict | None = None


def _resolve_config_path(config_path: str) -> Path:
    """将相对路径解析为基于项目根目录的绝对路径。"""
    p = Path(config_path)
    if p.is_absolute():
        return p
    return _PROJECT_ROOT / p


def load_markers(config_path: str = "config/markers.yaml") -> dict:
    """
    加载标记配置。

    Args:
        config_path: 配置文件路径（相对于项目根目录或绝对路径）

    Returns:
        {
            "markers": [
                {"symbol": str, "field": str, "type": str, "description": str}
            ],
            "output_template": str
        }
    """
    global _cached_config
    resolved = _resolve_config_path(config_path)
    try:
        with open(resolved, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        if not isinstance(config, dict):
            logger.warning("配置文件内容格式异常，返回空配置: %s", resolved)
            config = {"markers": [], "output_template": ""}
        # 确保必需字段存在
        config.setdefault("markers", [])
        config.setdefault("output_template", "")
        _cached_config = config
        logger.info("标记配置已加载: %s (%d 条标记)", resolved, len(config.get("markers", [])))
        return config
    except FileNotFoundError:
        logger.error("配置文件不存在: %s", resolved)
        empty = {"markers": [], "output_template": ""}
        _cached_config = empty
        return empty
    except Exception as e:
        logger.error("加载配置文件失败: %s — %s", resolved, e)
        empty = {"markers": [], "output_template": ""}
        _cached_config = empty
        return empty


def _get_config(markers_config: dict | None) -> dict:
    """获取配置：优先使用传入值，其次使用缓存，最后重新加载。"""
    if markers_config is not None:
        return markers_config
    if _cached_config is not None:
        return _cached_config
    return load_markers()


def _escape_symbol(symbol: str) -> str:
    """对正则特殊字符进行转义。"""
    return re.escape(symbol)


def parse_text(text: str, markers_config: dict | None = None) -> dict:
    """
    解析文本中的自定义标记。

    Args:
        text: 待解析文本（来自转录或 OCR）
        markers_config: 标记配置（None 则自动加载）

    Returns:
        {
            "fields": { "<field_name>": str, ... },
            "raw_text": str,
            "matched_markers": list,
            "unmatched_sections": list
        }
    """
    try:
        config = _get_config(markers_config)
        markers = config.get("markers", [])

        fields: dict[str, str] = {}
        matched_markers: list[dict] = []
        unmatched_sections: list[str] = []

        # 按类型分组
        section_markers = [m for m in markers if m.get("type") == "section_header"]
        inline_markers = [m for m in markers if m.get("type") == "inline_tag"]
        highlight_markers = [m for m in markers if m.get("type") == "highlight"]

        # 收集 inline/highlight 的 symbols（用于末尾 section 截断判断）
        tail_symbols = [m["symbol"] for m in inline_markers + highlight_markers]

        # ── section_header 处理 ──
        # section 内容在下一个 section_header 处截断。
        # 对于最后一个 section_header（无后续 section），如果其末尾有
        # inline/highlight 标记，则截断到第一个此类标记处，避免把
        # 独立的标记内容错误归入最后一个 section。
        if section_markers:
            # 收集所有 section_header symbol 在文本中的位置
            positions: list[tuple[int, dict]] = []
            for m in section_markers:
                sym = m["symbol"]
                idx = text.find(sym)
                while idx != -1:
                    positions.append((idx, m))
                    idx = text.find(sym, idx + len(sym))

            # 按位置排序
            positions.sort(key=lambda x: x[0])

            for i, (start, marker) in enumerate(positions):
                sym = marker["symbol"]
                field_name = marker["field"]
                content_start = start + len(sym)
                # 结束位置：下一个 section_header 的起始，或文本末尾
                if i + 1 < len(positions):
                    content_end = positions[i + 1][0]
                else:
                    content_end = len(text)
                    # 最后一个 section：如果末尾有 inline/highlight 标记，
                    # 截断到第一个此类标记处，避免把标记及其前缀文本
                    # （如"取穴："）错误归入 section 内容
                    if tail_symbols:
                        for tsym in tail_symbols:
                            idx = text.find(tsym, content_start)
                            if idx != -1 and idx < content_end:
                                content_end = idx
                segment = text[content_start:content_end].strip()

                # 末尾清理：如果最后一个 section 的末尾是连接性文本
                # （如"取穴："、"取穴为："、"穴位："），且紧随其后的是
                # inline markers（已独立解析），则移除这些连接文本
                if i == len(positions) - 1 and tail_symbols:
                    for tsym in tail_symbols:
                        marker_idx = text.find(tsym, content_start)
                        if marker_idx != -1 and marker_idx >= content_start:
                            # 取 marker 之前的文本，查找连接词
                            before = text[content_start:marker_idx].rstrip()
                            # 如果末尾有类似"取穴""穴位"等连接词，截掉
                            conn_match = re.search(
                                r'(?:，|,|\n)\s*(?:取穴|穴位|取穴为|穴位为|配合|配穴)[：:]?\s*$',
                                before,
                            )
                            if conn_match:
                                segment = before[:conn_match.start()].strip()
                            break

                fields[field_name] = segment
                matched_markers.append({
                    "symbol": sym,
                    "field": field_name,
                    "type": "section_header",
                    "content": segment,
                    "position": start,
                })

        # ── inline_tag 处理 ──
        for m in inline_markers:
            sym = m["symbol"]
            field_name = m["field"]
            escaped = _escape_symbol(sym)
            # 匹配 symbol 后（允许可选空白）的词/短语（中文字符、字母、数字、下划线）
            pattern = escaped + r"\s*([\u4e00-\u9fff\w]+)"
            matches = re.findall(pattern, text)
            if matches:
                # 以列表形式存储，多个用逗号分隔
                fields[field_name] = ", ".join(matches)
                matched_markers.append({
                    "symbol": sym,
                    "field": field_name,
                    "type": "inline_tag",
                    "content": matches,
                    "count": len(matches),
                })

        # ── highlight 处理 ──
        for m in highlight_markers:
            sym = m["symbol"]
            field_name = m["field"]
            escaped = _escape_symbol(sym)
            pattern = escaped + r"\s*([\u4e00-\u9fff\w]+)"
            matches = re.findall(pattern, text)
            if matches:
                fields[field_name] = ", ".join(matches)
                matched_markers.append({
                    "symbol": sym,
                    "field": field_name,
                    "type": "highlight",
                    "content": matches,
                    "count": len(matches),
                })

        # ── 未匹配段落 ──
        # 将文本按行拆分，标记每一行是否被任何 section_header 覆盖
        # 简单策略：找出所有未被 section_header 覆盖且不包含 inline/highlight 标记的行
        all_symbols = {m["symbol"] for m in markers}
        lines = text.split("\n")
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            # 检查是否是某个 section_header 的起始行（跳过）
            is_section_start = any(stripped.startswith(m["symbol"]) for m in section_markers)
            if is_section_start:
                continue
            # 检查是否包含任何 marker symbol
            has_marker = any(sym in stripped for sym in all_symbols)
            if not has_marker and stripped:
                unmatched_sections.append(stripped)

        logger.info(
            "文本解析完成: %d 个字段, %d 个标记匹配, %d 个未匹配段落",
            len(fields), len(matched_markers), len(unmatched_sections),
        )

        return {
            "fields": fields,
            "raw_text": text,
            "matched_markers": matched_markers,
            "unmatched_sections": unmatched_sections,
        }

    except Exception as e:
        logger.error("文本解析失败: %s", e)
        return {
            "fields": {},
            "raw_text": text,
            "matched_markers": [],
            "unmatched_sections": [],
            "error": str(e),
        }


def reload_config():
    """重载标记配置（热更新）。"""
    global _cached_config
    _cached_config = None
    load_markers()
    logger.info("标记配置已重载")


# ── 直接运行时的测试 ──
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    # 加载配置
    cfg = load_markers()
    print("=== 加载的配置 ===")
    print(f"标记数量: {len(cfg.get('markers', []))}")
    print(f"模板路径: {cfg.get('output_template', '')}")
    print()

    # 示例文本
    sample_text = """\
【主诉】反复胃脘疼痛3年，加重1周。
【现病史】患者3年前开始出现胃脘部隐痛，进食后加重，伴有反酸、嗳气。
近1周因饮食不节，症状加重，疼痛呈持续性，★胃脘灼热感明显。
曾服用奥美拉唑等药物，效果不佳。
【既往史】既往有慢性胃炎病史。
【舌象】舌红，苔黄腻。
【脉象】脉滑数。
【诊断】胃脘痛（湿热中阻证）
【治则】清热化湿，理气和胃。
【处方】黄连6g 黄芩10g 半夏10g 陈皮10g 茯苓15g 甘草6g
【医嘱】忌辛辣油腻，规律饮食。
取穴：△足三里 △中脘 △内关，阳虚 → 温阳补肾。
"""

    print("=== 解析结果 ===")
    result = parse_text(sample_text)
    print(f"字段数: {len(result['fields'])}")
    for k, v in result["fields"].items():
        preview = v if len(v) <= 60 else v[:60] + "..."
        print(f"  {k}: {preview}")
    print(f"\n匹配标记: {len(result['matched_markers'])}")
    for mm in result["matched_markers"]:
        print(f"  [{mm['type']}] {mm['symbol']} → {mm.get('field', '')}: {mm.get('content', '')}")
    print(f"\n未匹配段落: {len(result['unmatched_sections'])}")
    for us in result["unmatched_sections"]:
        print(f"  - {us}")

    # 热更新测试
    print("\n=== 热更新测试 ===")
    reload_config()
    cfg2 = load_markers()
    print(f"重载后标记数: {len(cfg2.get('markers', []))}")

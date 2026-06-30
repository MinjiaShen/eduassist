# =============================================================================
#  extractor.py — RapidOCR 本地识别 + 结构化字段提取
#  替代原 Anthropic Claude Vision API 方案，无需 API Key
#
#  v2.0 主要改进:
#    - 侧边栏过滤（排除左侧患者列表/导航干扰）
#    - 姓名 OCR 伪影裁剪（如 "孙政圖" → "孙政"）
#    - 初诊/复诊自动判断（历史(N)按钮、独立标签、右侧面板）
#    - 确定性药材提取（空间排序，消除随机性）
#    - 去重键碰撞修复（全 None 时不再误合并）
# =============================================================================

import os
import re
import time
import uuid
import logging
from pathlib import Path
from collections import OrderedDict

from PIL import Image

import config

logger = logging.getLogger(__name__)

# ── 延迟加载 OCR 引擎（首次调用时初始化，后续复用）───────────────────────────
_ocr_engine = None


def _get_ocr():
    """获取或创建 RapidOCR 引擎实例（单例）。"""
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
        logger.info("RapidOCR 引擎初始化完成")
    return _ocr_engine


# ── 侧边栏过滤阈值 ────────────────────────────────────────────────────────────
# 门诊系统截图左侧约 200px 为患者列表/导航栏，其文本不应参与病历字段提取
SIDEBAR_X_THRESHOLD = 200

# 右侧面板（历史就诊记录）起始 x 坐标阈值
# 1707px 宽的截图中，主内容区 x < 1200，右侧面板 x ≈ 1200+
RIGHT_PANEL_X_THRESHOLD = 1200


def _filter_sidebar(blocks: list) -> list:
    """过滤掉左侧侧边栏区域的文本块。"""
    main = [b for b in blocks if b[4] >= SIDEBAR_X_THRESHOLD]
    skipped = len(blocks) - len(main)
    if skipped > 0:
        logger.debug(f"过滤侧边栏文本块 {skipped} 个")
    return main


# ── 已知的中药名列表（用于辅助识别和分词）────────────────────────────────
KNOWN_HERBS = {
    "银柴胡", "黄芩片", "金银花", "连翘", "山楂", "冬瓜皮", "茵陈", "黄柏",
    "天冬", "麦冬", "川牛膝", "葛根", "黄连片", "陈皮", "甘草片", "太子参",
    "百合", "盐知母", "浮小麦", "刺五加", "五味子", "龙眼肉", "牛膝",
    "薏根", "当归", "黄芪", "白术", "茯苓", "白芍", "川芎", "熟地黄",
    "生地黄", "枸杞子", "山药", "山茱萸", "泽泻", "牡丹皮", "桂枝",
    "附子", "干姜", "细辛", "麻黄", "杏仁", "桔梗", "枳壳", "厚朴",
    "苍术", "砂仁", "木香", "香附", "柴胡", "升麻", "防风",
    "羌活", "独活", "威灵仙", "秦艽", "防己", "桑枝", "稀莶草",
    "木瓜", "杜仲", "续断", "骨碎补", "淫羊藿", "巴戟天", "肉苁蓉",
    "锁阳", "菟丝子", "沙苑子", "益智仁", "酸枣仁", "远志", "合欢皮",
    "夜交藤", "珍珠母", "龙骨", "牡蛎", "石决明", "代赭石", "天麻",
    "钩藤", "全蝎", "蜈蚣", "地龙", "僵蚕", "丹参", "红花", "桃仁",
    "延胡索", "郁金", "姜黄", "乳香", "没药", "三棱", "莪术",
    "水蛭", "虻虫", "三七", "蒲黄", "五灵脂", "小蓟", "大蓟", "地榆",
    "槐花", "白茅根", "侧柏叶", "艾叶", "炮姜", "灶心土", "白及",
    "仙鹤草", "棕榈炭", "血余炭", "藕节", "紫珠草",
}


# ── 常见 OCR 医学用字纠错表（错字 → 正字）──────────────────────────────────
_MEDICAL_CHAR_FIX = {
    "心季": "心悸",
    "胸闵": "胸闷",
    "纳呆": "纳差",
    "神疲之力": "神疲乏力",
}


def _fix_medical_text(text: str) -> str:
    """
    对病历文本做常见 OCR 纠错。
    使用词级替换（非单字），避免过度纠正。
    """
    if not text:
        return text
    for wrong, correct in _MEDICAL_CHAR_FIX.items():
        if wrong != correct and wrong in text:
            text = text.replace(wrong, correct)
    return text


# ═══════════════════════════════════════════════════════════════════════════════
#  OCR 识别
# ═══════════════════════════════════════════════════════════════════════════════

def _ocr_image(image_path: str) -> list:
    """
    对图片执行 OCR，返回文本块列表。
    每个元素: (box, text, confidence, y_center, x_center)
    """
    ocr = _get_ocr()

    with Image.open(image_path) as img:
        # 转换 RGBA / P / RGBA 等非 RGB 模式
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        max_side = config.IMAGE_MAX_SIDE
        if max_side:
            w, h = img.size
            longest = max(w, h)
            if longest > max_side:
                ratio = max_side / longest
                img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

        # 保存为临时 JPEG（避免路径中文编码问题），使用 uuid 防并发冲突
        tmp_path = os.path.join(
            os.path.dirname(image_path), f"_tmp_ocr_{uuid.uuid4().hex[:8]}.jpg"
        )
        img.save(tmp_path, "JPEG", quality=92)

    try:
        result, _ = ocr(tmp_path)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    if not result:
        return []

    blocks = []
    for box, text, conf in result:
        y_c = sum(p[1] for p in box) / 4
        x_c = sum(p[0] for p in box) / 4
        blocks.append((box, text.strip(), conf, y_c, x_c))

    # 按 y 坐标排序（从上到下），同 y 再按 x（从左到右）
    blocks.sort(key=lambda b: (round(b[3] / 15) * 15, b[4]))
    return blocks


# ═══════════════════════════════════════════════════════════════════════════════
#  字段提取
# ═══════════════════════════════════════════════════════════════════════════════

# 字段标签（用于前缀匹配停止，覆盖各种变体）
_STOP_KEYWORDS = {
    "主诉", "现病史", "现病", "既往史", "既往", "望闻切诊",
    "诊断", "辨证", "诊疗项目", "处方医嘱", "医嘱事项",
    "处方", "加工", "备注", "剂型", "药房",
}

# 短标签名（用于在左侧区域识别无冒号的字段标签）
_LABEL_NAMES = {
    "主诉", "现病史", "现病", "既往史", "既往",
    "望闻切诊", "诊断", "辨证",
}

# 多行收集专用停止词（防止从主诉越界到现病史等）
_MULTI_STOP_WORDS = {"确诊", "否认", "患者"}


def _extract_field_value(blocks, keyword: str) -> str | None:
    """
    提取 "关键词：值" 或 "关键词 值" 模式的字段。
    支持值跨多行（直到遇到下一个已知字段标签或非医疗内容）。

    改进 v4:
      - 策略 A: 同线值充足 (≥10字符) → 直接返回，不进入多行收集
      - 策略 B: 同线值为空或偏短 → 进入多行收集（含增强停止条件）
      - 右侧面板 x > 1200 一律排除
      - 同线容差 ±12，多行 y 间距保护
    """
    # ── Step 1: 定位关键词标签 ──
    found_idx = None
    keyword_y = None
    keyword_x = None

    for i, (_, text, conf, y_c, x_c) in enumerate(blocks):
        clean = text.strip().rstrip("：:").strip()
        if clean == keyword or text.strip() == keyword:
            found_idx = i
            keyword_y = y_c
            keyword_x = x_c
            break

    if found_idx is None:
        return None

    # ── Step 2: 同线检查（±12 容差 + 排除右侧面板）──
    same_line_parts = []
    same_line_texts = set()  # 用于去重：防止多行收集重复收集同线值
    for _, t2, _, y2, x2 in blocks:
        if abs(y2 - keyword_y) < 12 and x2 > keyword_x + 30:
            if x2 > RIGHT_PANEL_X_THRESHOLD:
                continue
            v = t2.strip()
            if not v:
                continue
            is_stop = any(v.startswith(sk) for sk in _STOP_KEYWORDS)
            if is_stop:
                continue
            clean_v = v.rstrip("：:").strip()
            if clean_v in _LABEL_NAMES and len(v) < 6:
                continue
            same_line_parts.append(v)
            same_line_texts.add(v)

    same_line_result = "".join(same_line_parts)

    # 策略 A: 同线值充足 → 直接返回（避免多行收集引入污染）
    # 阈值 10 字符足以覆盖大多数单行字段值
    if len(same_line_result) >= 10:
        return same_line_result

    # ── Step 3: 多行收集（仅当同线值不足时）──
    multi_parts = []
    last_y = keyword_y
    for j in range(found_idx + 1, len(blocks)):
        _, text_j, conf_j, y_j, x_j = blocks[j]

        # 排除侧边栏和右侧面板
        if x_j < SIDEBAR_X_THRESHOLD:
            continue
        if x_j > RIGHT_PANEL_X_THRESHOLD:
            continue

        clean_j = text_j.strip().rstrip("：:").strip()

        # 停止条件 1: y 方向超出范围
        if y_j > keyword_y + 250:
            break

        # 停止条件 2: 遇到下一个字段标签（前缀匹配）
        if any(clean_j.startswith(sk) for sk in _STOP_KEYWORDS):
            break
        # 停止条件 2b: 多行专用停止词（防止从主诉越界到现病史等）
        if any(clean_j.startswith(ms) for ms in _MULTI_STOP_WORDS):
            break
        # 停止条件 2c: 左侧区域的短文本匹配字段标签名（即使没有冒号）
        if clean_j in _LABEL_NAMES and x_j < 500 and len(text_j.strip()) < 8:
            break

        # 停止条件 3: 文本块包含字段标签模式
        has_label = False
        for sk in _STOP_KEYWORDS:
            if sk + "：" in text_j or sk + ":" in text_j:
                has_label = True
                break
        if has_label:
            break

        # 停止条件 4: 非病历内容
        if re.match(r'^\d{4}[-/.]', text_j.strip()):
            break
        if re.match(r'^\d{11}$', text_j.strip()):
            continue
        if re.match(r'^[\u4e00-\u9fff]{2,4}[-·\s][\u4e00-\u9fff]+科', text_j.strip()):
            break
        if text_j.strip().startswith("已收") or text_j.strip() == "预":
            continue
        if re.match(r'^[\u4e00-\u9fff]{2,3}预$', text_j.strip()):
            continue
        if re.match(r'^(下午|上午)\d+', text_j.strip()):
            continue
        if text_j.strip().endswith("已收"):
            continue

        # 停止条件 5: y 间距过大（进入新区域，典型字段间距 30-45px）
        if multi_parts and y_j > last_y + 40:
            break

        val = text_j.strip()
        if val:
            # 去重：跳过已在同线收集中出现的相同文本
            if val in same_line_texts:
                continue
            multi_parts.append(val)
            last_y = y_j

    # ── Step 4: 组合结果 ──
    # 多行收集可能越界包含其他字段的内容，做尾部清理
    if multi_parts:
        for sk in _LABEL_NAMES:
            for k in range(len(multi_parts) - 1, -1, -1):
                if multi_parts[k].startswith(sk):
                    multi_parts = multi_parts[:k]
                    break  # 截断后停止当前 label 的搜索

    all_parts = same_line_parts + multi_parts
    result = "".join(all_parts)
    return result if result else None


# ═══════════════════════════════════════════════════════════════════════════════
#  处方药材提取
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_inline_herbs(text: str) -> list:
    """
    从合并文本中提取药材和剂量，如 "甘草片20g太子参40g百合50g"
    """
    inline_skip = {
        "单剂", "总计", "用法", "每日", "备注", "已收费", "收费",
        "加工", "处方", "中药", "饮片", "药房", "剂型",
    }

    herbs = []
    remaining = text
    while remaining:
        # 跳过非药材文本
        skipped = False
        for skip_word in inline_skip:
            if remaining.startswith(skip_word):
                remaining = remaining[len(skip_word):]
                m = re.match(r'\s*[\d.]+\s*g?\s*', remaining)
                if m:
                    remaining = remaining[m.end():]
                skipped = True
                break
        if skipped:
            continue

        found = False
        # 按长度从长到短匹配药名（确定性排序：同长度按字母序）
        for herb_name in sorted(KNOWN_HERBS, key=lambda h: (-len(h), h)):
            if remaining.startswith(herb_name):
                remaining = remaining[len(herb_name):]
                m = re.match(r'\s*(\d+(?:\.\d+)?)\s*g?', remaining)
                if m:
                    dose = float(m.group(1))
                    if dose == int(dose):
                        dose = int(dose)
                    if 0 < dose <= 200:
                        herbs.append({"name": herb_name, "dose_g": dose})
                    remaining = remaining[m.end():]
                else:
                    herbs.append({"name": herb_name, "dose_g": None})
                found = True
                break
        if not found:
            # 通用模式：2-4 个汉字 + 数字+g
            m = re.match(r'([\u4e00-\u9fff]{2,4})\s*(\d+(?:\.\d+)?)\s*g', remaining)
            if m:
                name = m.group(1)
                dose = float(m.group(2))
                if dose == int(dose):
                    dose = int(dose)
                if name not in inline_skip and 0 < dose <= 200:
                    herbs.append({"name": name, "dose_g": dose})
                remaining = remaining[m.end():]
            else:
                remaining = remaining[1:]

    return herbs


def _extract_herbs(blocks) -> list:
    """
    从 OCR 结果中提取处方药材。
    支持: 1) 药名和剂量分开的文本块（grid 布局）
          2) 合并的内联文本
    侧边栏文本块被过滤，药材按空间位置排序以确保确定性。
    """
    herb_pattern = re.compile(r'^[\u4e00-\u9fff]{2,4}$')
    dose_pattern = re.compile(r'^(\d+(?:\.\d+)?)\s*g?$')

    # 不应被当作药名的 UI 文本
    skip_words = {
        "饮片", "加工", "药房", "处方", "医嘱", "门诊", "病历", "诊疗",
        "项目", "看板", "预约", "挂号", "工具", "用药", "安全", "合理",
        "助手", "汇总", "情况", "报告", "更多", "修改", "打印", "医保",
        "接诊", "搜索", "工作", "医生", "已收", "下午", "上午", "今天",
        "本次", "辅助", "备注", "复制", "主诉", "现病", "既往", "诊断",
        "辨证", "剂型", "中药", "方剂", "配方", "已收费", "收费",
        "小工具", "门诊看板", "预约看板", "专属客服", "门诊网诊",
        "网诊", "叫号", "单剂", "总计", "费用预览", "历史",
        "附件", "普通", "首页", "商城", "下一位", "味", "用法",
        "每日", "包", "团", "剂",
    }

    non_herb_patterns = [
        r'^\d+', r'^[a-zA-Z]', r'^[\d.]+g?$', r'^[¥￥]',
    ]

    # 过滤侧边栏
    main_blocks = _filter_sidebar(blocks)

    # 空间排序确保确定性提取顺序
    sorted_items = sorted(
        [(i, text, b[3], b[4]) for i, b in enumerate(main_blocks) for text in [b[1]]],
        key=lambda t: (round(t[2] / 15) * 15, t[3]),
    )

    herbs = []
    seen_positions = set()

    # ── 方法 1: grid 布局（药名 + 剂量分开的文本块）──────────────────
    for i, text, y_c, x_c in sorted_items:
        if not herb_pattern.match(text):
            continue
        if text in skip_words:
            continue
        if any(re.match(p, text) for p in non_herb_patterns):
            continue

        # 在同一行（y ± 25px）右侧找剂量
        dose_val = None
        dose_idx = None
        for j, t2, y2, x2 in sorted_items:
            if j == i:
                continue
            if abs(y2 - y_c) < 25 and x2 > x_c:
                m = dose_pattern.match(t2)
                if m:
                    dose_val = float(m.group(1))
                    if dose_val == int(dose_val):
                        dose_val = int(dose_val)
                    dose_idx = j
                    break

        if dose_val is not None and 0 < dose_val <= 200:
            seen_positions.add(i)
            seen_positions.add(dose_idx)
            herbs.append({"name": text, "dose_g": dose_val})

    # ── 方法 2: 内联文本（药名+剂量合并在一起）──────────────────────
    inline_pattern = re.compile(r'[\u4e00-\u9fff]{2,4}\d+(?:\.\d+)?g')
    for i, text, y_c, x_c in sorted_items:
        if i in seen_positions:
            continue
        if x_c < SIDEBAR_X_THRESHOLD:
            continue
        if inline_pattern.search(text):
            for h in _extract_inline_herbs(text):
                herbs.append(h)
            seen_positions.add(i)

    # ── 去重（保持顺序）+ 模糊去重（OCR 近似药名如 黄苓片/黄芩片）─────
    seen = OrderedDict()
    for h in herbs:
        merged = False
        for existing_name in list(seen.keys()):
            if _herbs_similar(h["name"], existing_name):
                existing = seen[existing_name]
                # 如果新名字在已知药名列表中，替换 key
                if h["name"] in KNOWN_HERBS and existing_name not in KNOWN_HERBS:
                    del seen[existing_name]
                    seen[h["name"]] = h
                elif h["dose_g"] and not existing.get("dose_g"):
                    seen[existing_name] = h
                merged = True
                break
        if not merged:
            seen[h["name"]] = h

    return list(seen.values())


def _herbs_similar(name1: str, name2: str) -> bool:
    """
    判断两个药名是否是 OCR 近似变体。
    例如: 黄苓片 vs 黄芩片, 黄答片 vs 黄芩片
    """
    if name1 == name2:
        return True
    if len(name1) != len(name2):
        return False
    # OCR 常见混淆字符对
    _confusable = {
        ('苓', '芩'), ('芩', '苓'),
        ('答', '芩'), ('芩', '答'),
        ('答', '苓'), ('苓', '答'),
        ('己', '已'), ('已', '己'),
        ('术', '朮'), ('朮', '术'),
    }
    diff_positions = [i for i, (a, b) in enumerate(zip(name1, name2)) if a != b]
    if len(diff_positions) != 1:
        return False
    # 单字符差异：一个是已知药名，或属于混淆字符对
    i = diff_positions[0]
    c1, c2 = name1[i], name2[i]
    if name1 in KNOWN_HERBS or name2 in KNOWN_HERBS:
        return True
    if (c1, c2) in _confusable:
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
#  患者基本信息 + 就诊类型
# ═══════════════════════════════════════════════════════════════════════════════

# 不应被当作患者姓名的 UI 标签
_NAME_SKIP = {
    "门诊", "网诊", "病历", "报告", "修改", "打印", "医保",
    "搜索", "接诊", "更多", "初诊", "复诊", "普通", "科室",
    "主诉", "诊断", "辨证", "附件", "处方", "叫号", "首页",
    "商城", "诊疗", "看板", "预约", "工具", "助手", "汇总",
}


def _is_name_candidate(text: str) -> bool:
    """判断文本是否可能是患者姓名。"""
    if not re.match(r'^[\u4e00-\u9fff]{2,3}$', text):
        return False
    if text in _NAME_SKIP:
        return False
    return True


def _trim_ocr_artifact(text: str, conf: float) -> str:
    """
    裁剪 OCR 尾部伪影。
    对于置信度偏低的 3 字中文名，尝试截去末字（如 "孙政圖" → "孙政"）。
    对于 4+ 字名，逐步裁剪到 2-3 字。
    """
    if conf >= 0.85:
        return text
    # 3 字名低置信度 → 可能是 2 字名 + OCR 伪影
    if len(text) == 3:
        trimmed = text[:2]
        if re.match(r'^[\u4e00-\u9fff]{2}$', trimmed):
            logger.debug(f"裁剪 OCR 伪影: '{text}' → '{trimmed}' (conf={conf:.2f})")
            return trimmed
        return text
    # 4+ 字名 → 尝试裁剪到 3 字或 2 字
    if len(text) > 3:
        trimmed3 = text[:3]
        if _is_name_candidate(trimmed3):
            return trimmed3
        trimmed2 = text[:2]
        if re.match(r'^[\u4e00-\u9fff]{2}$', trimmed2):
            return trimmed2
    return text


def _extract_patient_info(blocks) -> dict:
    """从 OCR 结果中提取患者基本信息（含侧边栏过滤和姓名伪影裁剪）。"""
    info = {}

    # 过滤侧边栏后的文本块
    main_blocks = _filter_sidebar(blocks)

    # ── 性别 + 年龄 ──
    gender_y, gender_x = None, None
    for _, text, conf, y_c, x_c in main_blocks:
        if conf > 0.75:
            m = re.match(r'([男女])(\d+岁(?:\d+月)?)', text)
            if m:
                info["性别"] = m.group(1)
                info["年龄"] = m.group(2)
                gender_y, gender_x = y_c, x_c
                break

    # ── 姓名 ──
    # 策略 1: 在性别/年龄块左侧附近查找
    if gender_y is not None:
        best_name = None
        best_dist = 999
        for _, text, conf, y_c, x_c in main_blocks:
            if conf < 0.65:
                continue
            raw_name = text.strip()
            # 尝试裁剪 OCR 伪影
            trimmed = _trim_ocr_artifact(raw_name, conf)
            if not _is_name_candidate(trimmed):
                continue
            if abs(y_c - gender_y) < 40 and x_c < gender_x:
                dist = gender_x - x_c
                if dist < best_dist:
                    best_dist = dist
                    best_name = trimmed
        if best_name:
            info["姓名"] = best_name

    # 策略 2: 回退到头部区域
    if "姓名" not in info:
        for _, text, conf, y_c, x_c in main_blocks:
            if conf < 0.75:
                continue
            raw_name = text.strip()
            trimmed = _trim_ocr_artifact(raw_name, conf)
            if not _is_name_candidate(trimmed):
                continue
            # 患者头部区域 (y ~ 280-350, x 400-900)
            if 280 < y_c < 350 and 400 < x_c < 900:
                info["姓名"] = trimmed
                break

    # 策略 3: 从侧边栏患者列表中提取（当前选中项通常无"预"后缀）
    if "姓名" not in info:
        sidebar_blocks = [b for b in blocks if b[4] < SIDEBAR_X_THRESHOLD]
        # 侧边栏患者列表通常在 y=350~750 范围，上方是导航/搜索
        sidebar_names = []
        for _, text, conf, y_c, x_c in sidebar_blocks:
            if not (350 < y_c < 750):
                continue
            raw = text.strip()
            # 跳过带"预"后缀的条目（其他患者的预约）
            if raw.endswith("预"):
                continue
            # 裁剪 OCR 伪影（如 "孙政圖" → "孙政"）
            trimmed = _trim_ocr_artifact(raw, conf)
            if not _is_name_candidate(trimmed):
                continue
            # 排除已知药材名和 UI 元素
            if trimmed in KNOWN_HERBS:
                continue
            if trimmed in {"今天", "昨天", "明天", "上午", "下午", "已收"}:
                continue
            if conf >= 0.60:
                sidebar_names.append((trimmed, conf, y_c))

        if sidebar_names:
            # 选择置信度最高的候选
            sidebar_names.sort(key=lambda s: -s[1])
            best = sidebar_names[0]
            info["姓名"] = best[0]
            logger.info(f"从侧边栏提取姓名: {best[0]} (conf={best[1]:.2f})")

    # ── 电话：11 位数字 ──
    for _, text, conf, y_c, x_c in main_blocks:
        if conf > 0.75 and re.match(r'^\d{11}$', text):
            info["电话"] = text
            break

    # ── 医师 + 科室 ──
    for _, text, conf, y_c, x_c in main_blocks:
        if conf > 0.75:
            m = re.match(r'([\u4e00-\u9fff]{2,4})[-\u00b7\s]([\u4e00-\u9fff]+科)', text)
            if m:
                info["主治医师"] = m.group(1)
                info["科室"] = m.group(2)
                break

    # ── 就诊类型 + 费用（来自文本块，如 "初诊¥26"）──
    for _, text, conf, y_c, x_c in main_blocks:
        if conf > 0.75:
            m = re.match(r'(初诊|复诊)[\s¥￥]*(\d[\d.]*)', text)
            if m:
                info["就诊类型"] = m.group(1)
                info["费用"] = "\u00a5" + m.group(2)
                break

    # ── 就诊日期时间 ──
    for _, text, conf, y_c, x_c in main_blocks:
        if conf > 0.75:
            m = re.search(r'(\d{1,2}:\d{2})[~\-](\d{1,2}:\d{2})', text)
            if m:
                date_str = None
                for _, t2, _, _, _ in main_blocks:
                    dm = re.search(r'(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})', t2)
                    if dm:
                        date_str = (
                            f"{dm.group(1)}-"
                            f"{dm.group(2).zfill(2)}-"
                            f"{dm.group(3).zfill(2)}"
                        )
                        break
                if date_str:
                    info["就诊日期时间"] = f"{date_str} {m.group(1)}"
                else:
                    info["就诊日期时间"] = m.group(1)
                break

    # ── 费用（补充查找）──
    if "费用" not in info:
        for _, text, conf, y_c, x_c in main_blocks:
            m = re.match(r'[¥￥]([\d.]+)', text)
            if m and conf > 0.75:
                val = float(m.group(1))
                if val > 1:
                    info["费用"] = "\u00a5" + m.group(1)
                    break

    # ── 总量和单剂量 ──
    for _, text, conf, y_c, x_c in main_blocks:
        m = re.search(r'单剂\s*(\d+)\s*g.*总计\s*(\d+)\s*g', text)
        if m:
            info["单剂量g"] = int(m.group(1))
            info["总量g"] = int(m.group(2))
            break
    if "总量g" not in info:
        for _, text, conf, y_c, x_c in main_blocks:
            m = re.search(r'总计\s*(\d+)\s*g', text)
            if m and conf > 0.75:
                info["总量g"] = int(m.group(1))
                break

    # ── 剂型 ──
    for _, text, conf, _, _ in main_blocks:
        if "饮片" in text:
            info["剂型"] = "饮片"
            break

    # ── 药房 ──
    for _, text, conf, _, _ in main_blocks:
        if "药房" in text and len(text) < 20:
            info["药房"] = text
            break

    return info


def _detect_visit_type(blocks) -> str | None:
    """
    自动判断初诊/复诊。
    信号优先级:
      1. 右侧面板 / 正文中的历史就诊日期条目（最可靠，患者级别）
      2. 独立的 "初诊" / "复诊" 文本块
    注意: "历史(N)" 按钮是全局 UI 元素（对所有患者都显示），不作为判断依据。
    """
    # 使用全量 blocks（含侧边栏），因为历史记录可能在任意位置

    # ── 信号 1: 右侧面板中的历史日期条目（最可靠）──
    # 当前就诊的日期通常出现在头部区域；
    # 历史面板中的短日期 (MM-DD) 表示过往就诊
    #
    # 先获取当前就诊日期（从头部/底部区域查找，排除正文中的历史日期）
    current_date_mmdd = None
    for _, text, conf, y_c, x_c in blocks:
        if conf < 0.75:
            continue
        # 仅在头部 (y<380) 或底部 (y>1000) 查找当前日期
        if not (y_c < 380 or y_c > 1000):
            continue
        dm = re.search(r'(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})', text)
        if dm:
            mm = dm.group(2).zfill(2)
            dd = dm.group(3).zfill(2)
            current_date_mmdd = f"{mm}-{dd}"
            break

    right_panel_dates = []
    for _, text, conf, y_c, x_c in blocks:
        if conf < 0.70 or x_c < 300:
            continue
        clean = text.strip()
        # MM-DD 格式短日期（排除时间和当前就诊日期）
        if re.match(r'^\d{2}[-/]\d{2}$', clean) and ':' not in clean:
            if current_date_mmdd and clean == current_date_mmdd:
                continue  # 当前就诊日期的 MM-DD 显示，非历史记录
            right_panel_dates.append(clean)
    if right_panel_dates:
        logger.info(f"检测到 {len(right_panel_dates)} 个历史日期条目 -> 复诊")
        return "复诊"

    # ── 信号 2: 独立的初诊/复诊文本块 ──
    for _, text, conf, y_c, x_c in blocks:
        if conf < 0.75:
            continue
        clean = text.strip()
        if clean == "初诊":
            return "初诊"
        if clean == "复诊":
            return "复诊"

    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  单张图片处理
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_single_image(image_path: str) -> dict | None:
    """
    对单张图片进行 OCR 并提取所有字段，返回原始字典。
    """
    source_name = Path(image_path).name
    blocks = _ocr_image(image_path)

    if not blocks:
        logger.warning(f"[{source_name}] OCR 未识别到任何文本")
        return None

    logger.info(f"[{source_name}] 识别到 {len(blocks)} 个文本块")

    # ── 过滤非医疗图片 ──
    structure_keywords = {
        "主诉", "现病史", "既往史", "望闻切诊", "诊断", "辨证",
        "处方", "病历", "饮片", "中药处方", "诊疗项目", "处方医嘱",
    }
    content_indicators = {
        "预约时间", "预约科室", "费用", "已收费", "已收",
        "医师", "患者", "中药房",
    }
    all_text_set = {text.strip().rstrip("：:") for _, text, _, _, _ in blocks}
    all_text_joined = " ".join(text.strip() for _, text, _, _, _ in blocks)

    has_structure = any(kw in all_text_set for kw in structure_keywords)
    has_content = any(kw in all_text_joined for kw in content_indicators)

    if not has_structure:
        logger.info(f"[{source_name}] 未检测到医疗结构标签，跳过")
        return None
    if not has_content and len(blocks) < 50:
        logger.info(f"[{source_name}] 医疗内容不足（{len(blocks)}个文本块），跳过")
        return None

    raw = {}

    # ── 患者基本信息 ──
    info = _extract_patient_info(blocks)
    raw.update(info)

    # ── 就诊类型（初诊/复诊）自动判断 ──
    # 优先使用从文本块直接提取的结果，其次使用自动检测结果
    detected_type = _detect_visit_type(blocks)
    if detected_type:
        # 如果 info 中已有就诊类型且与检测结果一致，保持不变
        # 如果 info 中没有，或检测结果更可靠，使用检测结果
        if "就诊类型" not in raw or not raw["就诊类型"]:
            raw["就诊类型"] = detected_type
        elif raw["就诊类型"] != detected_type:
            logger.info(
                f"[{source_name}] 就诊类型冲突: "
                f"文本提取={raw['就诊类型']}, 自动检测={detected_type}，"
                f"使用自动检测结果"
            )
            raw["就诊类型"] = detected_type

    # ── 病历字段 ──
    # 过滤侧边栏后再做字段提取
    main_blocks = _filter_sidebar(blocks)

    field_mappings = {
        "主诉": "主诉",
        "现病史": "现病史",
        "现病": "现病史",
        "既往史": "既往史",
        "既往": "既往史",
        "望闻切诊": "望闻切诊",
    }
    for ocr_key, field_name in field_mappings.items():
        if field_name not in raw or not raw[field_name]:
            val = _extract_field_value(main_blocks, ocr_key)
            if val:
                raw[field_name] = val

    # ── 诊断 ──
    if not raw.get("诊断"):
        for _, text, conf, y_c, x_c in main_blocks:
            clean = text.strip().rstrip("：:").strip()
            if clean == "诊断" and conf > 0.85:
                for _, t2, _, y2, x2 in main_blocks:
                    if abs(y2 - y_c) < 12 and x2 > x_c + 20:
                        if x2 > RIGHT_PANEL_X_THRESHOLD:
                            continue  # 排除右侧面板
                        v = t2.strip()
                        if v and v not in ("辨证", "处方"):
                            raw["诊断"] = v
                            break
                break

    # ── 辨证（使用更严格的 y 容差，避免与诊断值混淆）──
    if not raw.get("辨证"):
        for _, text, conf, y_c, x_c in main_blocks:
            clean = text.strip().rstrip("：:").strip()
            if clean == "辨证" and conf > 0.85:
                for _, t2, _, y2, x2 in main_blocks:
                    if abs(y2 - y_c) < 12 and x2 > x_c + 20:
                        if x2 > RIGHT_PANEL_X_THRESHOLD:
                            continue  # 排除右侧面板
                        v = t2.strip()
                        if v and v != raw.get("诊断"):
                            raw["辨证"] = v
                            break
                break

    # ── 处方药材 ──
    herbs = _extract_herbs(blocks)
    raw["处方"] = herbs

    # ── 对病历文本字段做 OCR 纠错 ──
    for field in ("主诉", "现病史", "既往史", "望闻切诊", "诊断", "辨证"):
        if raw.get(field):
            raw[field] = _fix_medical_text(raw[field])

    # ── 二次验证：确保提取到至少 2 个有意义的病历字段 ──
    # 防止架构图、流程图等非病历图片通过初步过滤
    medical_fields = ["主诉", "现病史", "既往史", "望闻切诊", "诊断", "辨证"]
    extracted_fields = sum(1 for f in medical_fields if raw.get(f))

    # 内容质量检查：病历字段应包含中文字符（排除架构图等含代码/英文的误识别）
    # 但如果提取到了药材，说明是有效处方图片，不需要此检查
    if not herbs:
        chinese_medical_pattern = re.compile(r'[\u4e00-\u9fff]{2,}')
        has_chinese_medical = any(
            chinese_medical_pattern.search(raw.get(f, ""))
            for f in medical_fields if raw.get(f)
        )
        if not has_chinese_medical:
            logger.info(f"[{source_name}] 字段内容不含中文医疗术语且无处方药材，跳过")
            return None

    if extracted_fields < 2 and not herbs:
        logger.info(
            f"[{source_name}] 医疗字段不足"
            f"（{extracted_fields}个，需≥2）且无处方药材，跳过"
        )
        return None

    raw["_source"] = source_name
    return raw


# ═══════════════════════════════════════════════════════════════════════════════
#  分组 / 合并 / 规范化
# ═══════════════════════════════════════════════════════════════════════════════

def _group_images_by_patient(image_paths: list) -> list:
    """
    按文件名前缀分组图片。
    例如: 1.1.jpg, 1.2.jpg -> group "1"
          2.1.jpg, 2.2.jpg, 2.3.jpg -> group "2"
    """
    groups = OrderedDict()
    for path in sorted(image_paths):
        name = Path(path).stem  # e.g., "1.1", "2.3"
        prefix = name.split(".")[0] if "." in name else name
        if prefix not in groups:
            groups[prefix] = []
        groups[prefix].append(path)
    return list(groups.values())


def _merge_records(records: list) -> dict:
    """
    合并同一患者的多条 OCR 记录。
    - 基本信息字段: 取第一个非空值
    - 病历文本字段: 取最长值（信息更完整）
    - 日期字段: 取最新日期
    - 药材: 模糊去重合并
    """
    if not records:
        return {}
    if len(records) == 1:
        return records[0]

    merged = {}

    # ── 基本信息字段：取第一个非空值 ──
    basic_fields = [
        "姓名", "性别", "年龄", "电话", "主治医师", "就诊类型",
        "费用", "科室", "剂型", "药房",
    ]
    for field in basic_fields:
        for rec in records:
            val = rec.get(field)
            if val:
                merged[field] = val
                break

    # ── 病历文本字段 ──
    # 策略：首条记录（主视图）≥ 10 字符 → 优先采用（布局清晰，无污染）
    #       否则 → 取所有记录中最长的非空值（更完整的多行内容）
    text_fields = ["主诉", "现病史", "既往史", "望闻切诊", "诊断", "辨证"]
    for field in text_fields:
        first_val = records[0].get(field)
        if first_val and len(first_val) >= 10:
            merged[field] = first_val
        else:
            best_val = None
            best_len = 0
            for rec in records:
                val = rec.get(field)
                if val and len(val) > best_len:
                    best_val = val
                    best_len = len(val)
            if best_val:
                merged[field] = best_val

    # ── 日期字段：取最新日期 ──
    best_date = None
    for rec in records:
        val = rec.get("就诊日期时间")
        if val and len(val) >= 10:
            if best_date is None or val[:10] > best_date[:10]:
                best_date = val
    if best_date:
        merged["就诊日期时间"] = best_date

    # ── 数值字段：取第一个非空值 ──
    for field in ["单剂量g", "总量g"]:
        for rec in records:
            val = rec.get(field)
            if val is not None:
                merged[field] = val
                break

    # 合并药材（保持顺序 + 模糊去重，消除跨图片重复处方）
    merged_herbs = OrderedDict()
    for rec in records:
        for herb in rec.get("处方", []):
            found_similar = False
            for existing_name in list(merged_herbs.keys()):
                if _herbs_similar(herb["name"], existing_name):
                    existing = merged_herbs[existing_name]
                    # 保留已知药名（纠正 OCR 误识别）
                    if herb["name"] in KNOWN_HERBS and existing_name not in KNOWN_HERBS:
                        del merged_herbs[existing_name]
                        merged_herbs[herb["name"]] = herb
                    # 仅当已有记录无剂量时补充
                    elif herb.get("dose_g") and not existing.get("dose_g"):
                        merged_herbs[existing_name] = herb
                    found_similar = True
                    break
            if not found_similar:
                merged_herbs[herb["name"]] = herb
    merged["处方"] = list(merged_herbs.values())

    # 来源文件合并
    sources = []
    for rec in records:
        src = rec.get("_source", "")
        if src and src not in sources:
            sources.append(src)
    merged["_source"] = ", ".join(sources)

    return merged


def _normalize_record(raw: dict, source_file: str = None) -> dict:
    """将提取的原始数据规范化为主表行字典。"""
    herbs = raw.get("处方") or []

    # 处方合并文本
    if herbs:
        parts = []
        for h in herbs:
            name = h.get("name") or ""
            dose = h.get("dose_g")
            if dose is not None:
                parts.append(f"{name} {dose}g")
            elif name:
                parts.append(name)
        prescription_str = "; ".join(parts)
    else:
        prescription_str = None

    sf = source_file or raw.get("_source", "")

    return {
        "姓名":       raw.get("姓名"),
        "性别":       raw.get("性别"),
        "年龄":       raw.get("年龄"),
        "电话":       raw.get("电话"),
        "主治医师":   raw.get("主治医师"),
        "就诊类型":   raw.get("就诊类型"),
        "费用":       raw.get("费用"),
        "就诊日期时间": raw.get("就诊日期时间"),
        "科室":       raw.get("科室"),
        "主诉":       raw.get("主诉"),
        "现病史":     raw.get("现病史"),
        "既往史":     raw.get("既往史"),
        "望闻切诊":   raw.get("望闻切诊"),
        "诊断":       raw.get("诊断"),
        "辨证":       raw.get("辨证"),
        "处方（合并）": prescription_str,
        "剂型":       raw.get("剂型"),
        "药房":       raw.get("药房"),
        "单剂量g":    raw.get("单剂量g"),
        "总量g":      raw.get("总量g"),
        "来源文件":   sf,
        "_herbs":     herbs,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  公开接口
# ═══════════════════════════════════════════════════════════════════════════════

def extract_from_image(image_path: str) -> dict | None:
    """
    对单张图片使用 RapidOCR 提取信息，返回规范化记录字典。
    失败时返回 None 并记录日志。
    """
    source_name = Path(image_path).name

    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            logger.info(f"[{source_name}] 第 {attempt} 次 OCR 识别...")
            raw = _extract_single_image(image_path)
            if raw is None:
                if attempt == config.MAX_RETRIES:
                    _log_error(image_path, "OCR 未识别到文本")
                    return None
                time.sleep(1)
                continue

            record = _normalize_record(raw, source_name)
            herb_count = len(record.get("_herbs", []))
            logger.info(
                f"[{source_name}] 提取成功："
                f"患者={record.get('姓名')}, "
                f"就诊类型={record.get('就诊类型', '未知')}, "
                f"药材={herb_count}味"
            )
            return record

        except Exception as e:
            logger.error(f"[{source_name}] 第{attempt}次处理失败: {e}")
            if attempt == config.MAX_RETRIES:
                _log_error(image_path, f"处理失败（{config.MAX_RETRIES}次重试）: {e}")
                return None
            time.sleep(2 ** attempt)

    return None


def extract_from_directory(dir_path: str) -> list:
    """
    处理目录下的所有图片，按文件名前缀分组（同一患者），
    再按就诊日期拆分为不同就诊记录。
    返回规范化记录列表。
    """
    d = Path(dir_path)
    if not d.is_dir():
        logger.error(f"不是有效目录: {dir_path}")
        return []

    exts = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    images = sorted(
        [str(p) for p in d.iterdir() if p.is_file() and p.suffix.lower() in exts]
    )

    if not images:
        logger.warning(f"目录中没有找到图片: {dir_path}")
        return []

    # 按文件名前缀分组（同一患者）
    groups = _group_images_by_patient(images)
    logger.info(f"发现 {len(images)} 张图片，分为 {len(groups)} 个患者组")

    results = []
    for group in groups:
        # Step 1: 逐张提取
        group_records = []
        for img_path in group:
            source_name = Path(img_path).name
            for attempt in range(1, config.MAX_RETRIES + 1):
                try:
                    logger.info(f"[{source_name}] 第 {attempt} 次 OCR 识别...")
                    raw = _extract_single_image(img_path)
                    if raw:
                        group_records.append(raw)
                        herb_count = len(raw.get("处方", []))
                        logger.info(
                            f"[{source_name}] 提取成功："
                            f"患者={raw.get('姓名')}, "
                            f"就诊类型={raw.get('就诊类型', '未知')}, "
                            f"药材={herb_count}味"
                        )
                        break
                    else:
                        logger.warning(f"[{source_name}] 未提取到数据")
                        if attempt < config.MAX_RETRIES:
                            time.sleep(1)
                except Exception as e:
                    logger.error(f"[{source_name}] 第{attempt}次处理失败: {e}")
                    if attempt < config.MAX_RETRIES:
                        time.sleep(2 ** attempt)

        if not group_records:
            continue

        # Step 1.5: 在同组内传播患者姓名（有名字的 → 没名字的）
        group_name = None
        for rec in group_records:
            n = rec.get("姓名")
            if n and n not in KNOWN_HERBS:
                group_name = n
                break
        if group_name:
            for rec in group_records:
                if not rec.get("姓名") or rec.get("姓名") in KNOWN_HERBS:
                    rec["姓名"] = group_name

        # Step 2: 合并同一患者所有图片为一条记录
        merged = _merge_records(group_records)
        source_files = ", ".join(r.get("_source", "") for r in group_records)
        record = _normalize_record(merged, source_files)
        results.append(record)

        time.sleep(0.5)  # 组间短暂停顿

    return results


def _log_error(image_path: str, reason: str):
    """将失败记录追加写入错误日志。"""
    with open(config.ERROR_LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {image_path}  ->  {reason}\n")
    logger.error(f"已记录失败: {image_path}")

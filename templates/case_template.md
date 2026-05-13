# 中医医案记录

**生成时间**：{{ generated_at }}

---

{# ═══════════════════════════════════════════════════════
   第一部分：病人一般情况和诊疗过程
   ═══════════════════════════════════════════════════════ #}

{% if patient_info or chief_complaint or present_illness or past_history or tongue_diagnosis or pulse_diagnosis or western_diagnosis or lab_results %}
## 一、病人一般情况和诊疗过程

{% if patient_info %}
**患者信息**：{{ patient_info }}

{% endif %}
{% if chief_complaint %}
**主诉**：{{ chief_complaint }}

{% endif %}
{% if present_illness %}
**现病史**：{{ present_illness }}

{% endif %}
{% if past_history %}
**既往史**：{{ past_history }}

{% endif %}
{% if tongue_diagnosis %}
**舌象**：{{ tongue_diagnosis }}

{% endif %}
{% if pulse_diagnosis %}
**脉象**：{{ pulse_diagnosis }}

{% endif %}
{% if diagnosis %}
**中医诊断**：{{ diagnosis }}

{% endif %}
{% if western_diagnosis %}
**西医诊断**：{{ western_diagnosis }}

{% endif %}
{% if lab_results %}
**理化检查**：{{ lab_results }}

{% endif %}
{% endif %}

{# ═══════════════════════════════════════════════════════
   第二部分：辨证分析与立法
   ═══════════════════════════════════════════════════════ #}

{% if syndrome_analysis or treatment_principle or base_formula %}
## 二、辨证分析与立法

{% if syndrome_analysis %}
{{ syndrome_analysis }}

{% endif %}
{% if treatment_principle %}
**治则**：{{ treatment_principle }}

{% endif %}
{% if base_formula %}
**方剂**：{{ base_formula }}

{% endif %}
{% endif %}

{# ═══════════════════════════════════════════════════════
   第三部分：处方
   ═══════════════════════════════════════════════════════ #}

{% if prescription or external_prescription or acupoint %}
## 三、处方

{% if prescription %}
{{ prescription }}

{% endif %}
{% if external_prescription %}
**外用药**：{{ external_prescription }}

{% endif %}
{% if acupoint %}
**取穴**：
{% for point in acupoint %}
- △ {{ point }}
{% endfor %}

{% endif %}
{% endif %}

{# ═══════════════════════════════════════════════════════
   第四部分：医嘱
   ═══════════════════════════════════════════════════════ #}

{% if doctor_advice %}
## 四、医嘱

{# 医嘱内容中可能包含 inline markers 原始文本，这些已在处方部分
   结构化展示，此处过滤掉含标记符号的行和"取穴："连接行 #}
{% for line in doctor_advice.split('\n') %}
{% if '△' not in line and '★' not in line and '→' not in line and not line.strip().startswith('取穴') %}
{{ line }}
{% endif %}
{% endfor %}

{% endif %}

{# ═══════════════════════════════════════════════════════
   复诊记录（如有）
   ═══════════════════════════════════════════════════════ #}

{% if followup_visits %}
## 复诊

{% for visit in followup_visits %}
### {{ visit.visit_label | default("复诊") }}

{% if visit.changes %}
{{ visit.changes }}

{% endif %}
{% if visit.analysis %}
{{ visit.analysis }}

{% endif %}
{% if visit.prescription %}
**处方调整**：{{ visit.prescription }}

{% endif %}
{% if visit.advice %}
**医嘱**：{{ visit.advice }}

{% endif %}
{% endfor %}
{% endif %}

{# ═══════════════════════════════════════════════════════
   第五部分：体会
   ═══════════════════════════════════════════════════════ #}

{% if experience %}
## 五、体会

{{ experience }}

{% endif %}

{# ═══════════════════════════════════════════════════════
   附加信息（标记提取）
   ═══════════════════════════════════════════════════════ #}

{% if key_symptom or treatment_direction %}
---

### 标记提取摘要

{% if key_symptom %}
**★ 重点症状**：
{% for symptom in key_symptom %}
- ★ {{ symptom }}
{% endfor %}

{% endif %}
{% if treatment_direction %}
**→ 治则指向**：
{% for direction in treatment_direction %}
- → {{ direction }}
{% endfor %}

{% endif %}
{% endif %}

{% if unmatched_sections %}
---

### 未分类内容

{% for section in unmatched_sections %}
{{ section }}

{% endfor %}
{% endif %}

---

> 本医案由 EduAssist 自动生成，请医师审核确认。

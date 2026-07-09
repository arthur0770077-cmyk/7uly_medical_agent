# -*- coding: utf-8 -*-
"""Medical standards, lightweight RAG retrieval, and workflow metadata."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


REFERENCE_STANDARDS: list[dict[str, Any]] = [
    {
        "id": "CLSI_EP28",
        "name": "实验室参考区间建立与验证原则",
        "organization": "Clinical and Laboratory Standards Institute",
        "scope": "检验项目参考区间的建立、转移与验证。系统采用报告给出的参考范围优先，本地知识库范围仅作课程原型演示。",
        "url": "https://clsi.org/standards/products/method-evaluation/documents/ep28/",
        "authority_level": "方法学标准",
    },
    {
        "id": "ADA_DIABETES_CARE",
        "name": "糖尿病诊疗标准中的血糖与 HbA1c 阈值",
        "organization": "American Diabetes Association",
        "scope": "空腹血糖、HbA1c 等糖代谢指标的筛查、风险提示与需复核场景。",
        "url": "https://diabetesjournals.org/care/issue",
        "authority_level": "临床指南",
    },
    {
        "id": "KDIGO_CKD",
        "name": "慢性肾脏病评估与管理指南",
        "organization": "KDIGO",
        "scope": "肌酐、eGFR、尿检等肾功能评估需结合年龄、性别、肌肉量、既往病史和复查。",
        "url": "https://kdigo.org/guidelines/ckd-evaluation-and-management/",
        "authority_level": "临床指南",
    },
    {
        "id": "AASLD_LIVER_TESTS",
        "name": "肝功能异常评估共识",
        "organization": "AASLD / hepatology consensus sources",
        "scope": "ALT、AST、胆红素等肝胆指标异常的组合解释与线下就医提示。",
        "url": "https://www.aasld.org/",
        "authority_level": "专业学会资料",
    },
    {
        "id": "CBC_CRITICAL_VALUES",
        "name": "血常规危急值与复核原则",
        "organization": "医院检验危急值制度 / 血液学共识",
        "scope": "血红蛋白、血小板、白细胞等极端异常值触发医生复核和线下就医建议。",
        "url": "https://www.ncbi.nlm.nih.gov/books/",
        "authority_level": "临床安全规则",
    },
    {
        "id": "REPRODUCTIVE_HORMONES",
        "name": "性激素六项组合解读原则",
        "organization": "妇产科/内分泌实验室参考区间原则",
        "scope": "孕酮、泌乳素、FSH、LH、睾酮、雌二醇等指标需结合月经周期、妊娠/备孕、用药和本报告参考区间解释。",
        "url": "https://www.ncbi.nlm.nih.gov/books/",
        "authority_level": "课程原型知识库",
    },
]


STANDARD_BY_CODE = {
    "WBC": "CBC_CRITICAL_VALUES",
    "NEUT": "CBC_CRITICAL_VALUES",
    "HGB": "CBC_CRITICAL_VALUES",
    "PLT": "CBC_CRITICAL_VALUES",
    "ALT": "AASLD_LIVER_TESTS",
    "AST": "AASLD_LIVER_TESTS",
    "TBIL": "AASLD_LIVER_TESTS",
    "FPG": "ADA_DIABETES_CARE",
    "HBA1C": "ADA_DIABETES_CARE",
    "CREA": "KDIGO_CKD",
    "PROG": "REPRODUCTIVE_HORMONES",
    "PRL": "REPRODUCTIVE_HORMONES",
    "FSH": "REPRODUCTIVE_HORMONES",
    "LH": "REPRODUCTIVE_HORMONES",
    "TESTO": "REPRODUCTIVE_HORMONES",
    "E2": "REPRODUCTIVE_HORMONES",
}


KNOWLEDGE_GRAPH_EDGES = [
    {"from": "FPG", "to": "HBA1C", "relation": "糖代谢联合评估"},
    {"from": "ALT", "to": "AST", "relation": "肝细胞损伤组合评估"},
    {"from": "TBIL", "to": "ALT", "relation": "肝胆异常伴随判断"},
    {"from": "CREA", "to": "eGFR", "relation": "肾功能需结合估算滤过率"},
    {"from": "HGB", "to": "PLT", "relation": "贫血/出血风险与凝血风险联动"},
    {"from": "WBC", "to": "NEUT", "relation": "感染或炎症风险联动"},
    {"from": "PROG", "to": "E2", "relation": "月经周期/妊娠背景联合评估"},
    {"from": "LH", "to": "FSH", "relation": "卵巢轴和周期阶段联合判断"},
    {"from": "PRL", "to": "LH", "relation": "泌乳素异常可能影响性腺轴"},
    {"from": "TESTO", "to": "LH", "relation": "高雄激素线索需结合LH/FSH和症状"},
]


RAG_CORPUS: list[dict[str, str]] = [
    {
        "id": "rag-clsi-ref-interval",
        "title": "参考区间治理原则",
        "source_id": "CLSI_EP28",
        "text": "检验指标解释应优先采用原报告所在实验室给出的参考范围。课程原型内置范围只用于缺少报告参考区间时的演示，不应替代本地实验室验证后的参考区间。",
    },
    {
        "id": "rag-diabetes",
        "title": "糖代谢指标解释",
        "source_id": "ADA_DIABETES_CARE",
        "text": "空腹血糖和 HbA1c 需要结合是否空腹、近期用药、妊娠状态和复查结果。一次异常只能提示风险，不能单独完成诊断。",
    },
    {
        "id": "rag-liver",
        "title": "肝功能组合判断",
        "source_id": "AASLD_LIVER_TESTS",
        "text": "ALT、AST、胆红素异常需要结合饮酒、药物、病毒性肝炎、脂肪肝和症状。明显升高、黄疸或腹痛等情况应进入医生复核。",
    },
    {
        "id": "rag-kidney",
        "title": "肾功能指标解释",
        "source_id": "KDIGO_CKD",
        "text": "肌酐受年龄、性别、肌肉量和饮食影响，肾功能评估通常需要结合 eGFR、尿检和既往肾病史。持续升高或伴少尿、水肿应就医。",
    },
    {
        "id": "rag-cbc-critical",
        "title": "血常规红旗风险",
        "source_id": "CBC_CRITICAL_VALUES",
        "text": "血红蛋白极低、血小板极低、白细胞极高或伴黑便、呕血、胸痛、呼吸困难等红旗症状时，应立即转人工医生审核或线下就医。",
    },
    {
        "id": "rag-reproductive-hormones",
        "title": "性激素六项组合解读",
        "source_id": "REPRODUCTIVE_HORMONES",
        "text": "孕酮、雌二醇、LH、FSH、泌乳素和睾酮不能脱离月经周期、妊娠/备孕状态和用药背景单独诊断。系统优先采用报告参考区间，并建议由妇产科或内分泌医生结合病史复核。",
    },
    {
        "id": "rag-governance",
        "title": "医疗 AI 治理边界",
        "source_id": "WHO_LMM_HEALTH",
        "text": "医疗大模型和智能体应强调透明性、可解释性、人工监督、隐私保护和安全边界。系统输出应定位为辅助解释和随访管理，不应替代诊断或治疗决策。",
    },
]


AGENT_WORKFLOW = {
    "name": "检验体检报告解读与复诊随访智能体工作流",
    "priority": "外部大模型 API 降为可选增强项，默认采用规则引擎、RAG 检索和人工审核闭环。",
    "nodes": [
        {"id": "user_profile", "name": "用户画像读取/更新", "type": "memory"},
        {"id": "ocr_ingest", "name": "OCR/PDF 解析", "type": "tool"},
        {"id": "normalize_metrics", "name": "指标标准化", "type": "parser"},
        {"id": "retrieve_evidence", "name": "医学标准/RAG 检索", "type": "retriever"},
        {"id": "risk_stratify", "name": "风险分层与红旗识别", "type": "rule"},
        {"id": "patient_explain", "name": "患者解释与追问", "type": "dialog"},
        {"id": "doctor_review", "name": "医生复核/人工审核", "type": "human_in_loop"},
        {"id": "followup_plan", "name": "复诊随访计划", "type": "planner"},
        {"id": "persist_audit", "name": "数据库持久化与审计", "type": "storage"},
    ],
    "edges": [
        ["user_profile", "ocr_ingest"],
        ["ocr_ingest", "normalize_metrics"],
        ["normalize_metrics", "retrieve_evidence"],
        ["retrieve_evidence", "risk_stratify"],
        ["risk_stratify", "patient_explain"],
        ["risk_stratify", "doctor_review"],
        ["doctor_review", "followup_plan"],
        ["patient_explain", "followup_plan"],
        ["followup_plan", "persist_audit"],
    ],
}


def get_standard(standard_id: str | None) -> dict[str, Any] | None:
    return next((item for item in REFERENCE_STANDARDS if item["id"] == standard_id), None)


def standard_for_code(code: str) -> dict[str, Any] | None:
    return get_standard(STANDARD_BY_CODE.get((code or "").upper()))


def search_rag(query: str, codes: list[str] | None = None, limit: int = 5) -> list[dict[str, Any]]:
    tokens = {token.lower() for token in (query or "").replace("/", " ").split() if token.strip()}
    code_tokens = {code.upper() for code in (codes or [])}
    scored: list[tuple[int, dict[str, Any]]] = []
    for doc in RAG_CORPUS:
        text = f"{doc['title']} {doc['text']} {doc.get('source_id', '')}"
        score = sum(2 for token in tokens if token in text.lower())
        if doc.get("source_id") in code_tokens:
            score += 3
        for code in code_tokens:
            source = STANDARD_BY_CODE.get(code)
            if source and source == doc.get("source_id"):
                score += 4
        if score or not tokens and not code_tokens:
            enriched = dict(doc)
            enriched["standard"] = get_standard(doc.get("source_id"))
            scored.append((score, enriched))
    return [item for _, item in sorted(scored, key=lambda pair: pair[0], reverse=True)[:limit]]


@dataclass
class AgentDecision:
    intent: str
    reply: str
    missing_fields: list[str]
    next_action: str


def triage_patient_message(message: str, profile: dict[str, Any] | None = None) -> AgentDecision:
    text = (message or "").strip()
    profile = profile or {}
    missing = []
    for key, label in [
        ("chronic_diseases", "既往慢病"),
        ("medications", "近期用药"),
        ("pregnancy_status", "妊娠/备孕状态"),
        ("allergies", "过敏史"),
    ]:
        if not profile.get(key) and not profile.get(key.replace("_", "")):
            missing.append(label)

    red_terms = ["胸痛", "呼吸困难", "黑便", "呕血", "意识", "晕厥", "剧烈腹痛", "出血"]
    if any(term in text for term in red_terms):
        return AgentDecision(
            intent="red_flag",
            reply="你提到的症状属于需要优先确认的红旗信息。请不要只依赖系统解释，建议尽快联系医生或线下就医；我会把该信息标记给医生端复核。",
            missing_fields=missing,
            next_action="doctor_review",
        )

    if "复查" in text or "随访" in text:
        return AgentDecision(
            intent="followup",
            reply="我会结合风险分层、医生复核状态和你的既往情况生成随访提醒。若医生端修改了复查项目或日期，患者端会同步显示。",
            missing_fields=missing,
            next_action="followup_plan",
        )

    if not missing:
        return AgentDecision(
            intent="context_ready",
            reply="好的，我已经记录了你的个人情况。现在可以直接上传报告、粘贴检验结果，或继续告诉我具体不舒服的地方。",
            missing_fields=[],
            next_action="ready_for_report",
        )

    return AgentDecision(
        intent="context_collection",
        reply="我会先把报告指标结构化，再结合参考区间、红旗阈值和你的个人画像解释结果。为了让结论更可靠，请补充慢病史、近期用药、妊娠状态和明显不适症状。",
        missing_fields=missing,
        next_action="collect_context",
    )

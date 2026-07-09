# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DEEPSEEK_DISABLE"] = "1"

from app import (  # noqa: E402
    AGENT,
    AUDIT_PATH,
    DATASETS,
    FOLLOWUPS_PATH,
    HISTORY_PATH,
    OCR,
    REVIEWS_PATH,
    SAMPLES,
    add_history,
    enriched_followups,
    read_state,
    rows_from_report_text,
    save_json,
)


def synthetic_pdf_data_url() -> str:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "ALT 86 U/L\nFPG 7.2 mmol/L\nHbA1c 6.6 %", fontsize=12)
    data = doc.tobytes()
    doc.close()
    return "data:application/pdf;base64," + base64.b64encode(data).decode("ascii")


def main() -> None:
    state_paths = [REVIEWS_PATH, FOLLOWUPS_PATH, HISTORY_PATH, AUDIT_PATH]
    backup = {path: path.read_text(encoding="utf-8") if path.exists() else None for path in state_paths}
    try:
        run_checks()
    finally:
        for path, content in backup.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(content, encoding="utf-8")

    print("smoke tests passed")


def run_checks() -> None:
    assert len(SAMPLES) >= 3, "sample reports missing"
    assert len(DATASETS) >= 8, "dataset catalog missing"

    mild = SAMPLES[1]
    mild_rows = rows_from_report_text(mild["report_text"])
    hba1c_rows = [row for row in mild_rows if row["name"].startswith("HBA1C")]
    assert hba1c_rows and float(hba1c_rows[0]["value"]) == 6.6, "HbA1c parsing regressed"

    mild_result = AGENT.analyze(mild["report_text"], mild["patient"])
    assert mild_result["risk"]["risk_level"] in {"medium", "high", "red"}
    assert mild_result["explanations"], "no explanations generated"
    assert mild_result["review_task"], "high-risk sample should create a doctor review task"
    review = AGENT._build_review_task(
        mild_result["task_id"],
        mild_result["created_at"],
        mild["patient"],
        mild_result["parsed"],
        mild_result["explanations"],
        {**mild_result["risk"], "items": []},
        mild_result["followup"],
    )
    assert review["all_items"], "doctor detail should include all indicators"
    assert review["patient_context"]["raw_text"], "doctor detail should include raw report text"

    add_history(mild_result)
    history = read_state(HISTORY_PATH)
    assert history and history[0]["task_id"] == mild_result["task_id"], "history write failed"

    pdf_result = OCR.recognize(synthetic_pdf_data_url(), "synthetic-lab-report.pdf", mild, mild["patient"])
    assert pdf_result["engine"] == "pypdf_text_layer", "PDF text layer extraction failed"
    assert any(row["name"].startswith("ALT") for row in pdf_result["rows"]), "PDF ALT parsing failed"
    assert any(row["name"].startswith("FPG") for row in pdf_result["rows"]), "PDF FPG parsing failed"

    save_json(
        FOLLOWUPS_PATH,
        [
            {
                "follow_id": "FU-OVERDUE",
                "task_id": "CASE-OVERDUE",
                "owner": "测试患者",
                "title": "复查反馈",
                "priority": "中",
                "due_date": "2000-01-01",
                "questions": ["是否已复查？"],
                "status": "待提醒",
            }
        ],
    )
    overdue_items = enriched_followups()
    assert overdue_items[0]["overdue"] is True, "overdue flag not calculated"
    assert "逾期" in overdue_items[0]["status"], "overdue status label missing"

    red = SAMPLES[2]
    red_result = AGENT.analyze(red["report_text"], red["patient"])
    assert red_result["risk"]["risk_level"] == "red"
    assert red_result["risk"]["requires_review"] is True
    assert red_result["risk"]["red_flags"], "red flags not detected"
    assert OCR.status()["preferred"], "OCR status unavailable"


if __name__ == "__main__":
    main()

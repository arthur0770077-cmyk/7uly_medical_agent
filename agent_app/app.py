# -*- coding: utf-8 -*-
"""Course-design prototype for lab report interpretation and follow-up.

The app is intentionally rule-backed and auditable. It demonstrates an
agent-style pipeline without claiming clinical diagnosis ability.
"""
from __future__ import annotations

import base64
import io
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from relational_store import STORE
from standards import (
    AGENT_WORKFLOW,
    KNOWLEDGE_GRAPH_EDGES,
    REFERENCE_STANDARDS,
    RAG_CORPUS,
    search_rag,
    standard_for_code,
    triage_patient_message,
)


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA = ROOT / "data"
REVIEWS_PATH = DATA / "reviews.json"
FOLLOWUPS_PATH = DATA / "followups.json"
AUDIT_PATH = DATA / "audit_log.json"
HISTORY_PATH = DATA / "history.json"

UNIT_PATTERN = r"(?:10\^9/L|10\*9/L|g/L|U/L|umol/L|mmol/L|%|mg/L|ng/mL|mIU/mL|mIU/L|nmol/L|pg/mL)"
VALUE_WITH_UNIT_RE = re.compile(rf"(?:^|[\s:：])([-+]?\d+(?:\.\d+)?)(?=\s*{UNIT_PATTERN}|\s*$)", re.I)


def load_local_env() -> None:
    """Load local secrets for development without committing them."""
    for env_path in (ROOT / ".env.local", ROOT.parent / ".env.local"):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            clean_key = key.strip().lstrip("\ufeff")
            os.environ.setdefault(clean_key, value.strip().strip('"').strip("'"))


load_local_env()


def load_json(name: str) -> Any:
    with (DATA / name).open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


KNOWLEDGE = load_json("knowledge_base.json")
SAMPLES = load_json("sample_reports.json")
DATASETS = load_json("datasets_catalog.json")
MEDICAL_KNOWLEDGE_SEED = load_json("medical_knowledge_seed.json")


def init_persistence() -> None:
    STORE.init_db()
    STORE.seed_knowledge_documents(MEDICAL_KNOWLEDGE_SEED)


init_persistence()


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def read_state(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8-sig") or "[]")


def append_audit(actor: str, action: str, target: str, detail: str = "") -> None:
    events = read_state(AUDIT_PATH)
    events.insert(
        0,
        {
            "event_id": short_id("AUD"),
            "time": now_iso(),
            "actor": actor,
            "action": action,
            "target": target,
            "detail": detail,
        },
    )
    save_json(AUDIT_PATH, events[:100])


def add_history(result: dict[str, Any]) -> None:
    history = read_state(HISTORY_PATH)
    history.insert(
        0,
        {
            "task_id": result["task_id"],
            "created_at": result["created_at"],
            "patient": result["patient"],
            "risk": result["risk"],
            "explanations": result["explanations"],
            "followup": result["followup"],
            "review_task": result["review_task"],
        },
    )
    save_json(HISTORY_PATH, history[:200])


def enriched_followups() -> list[dict[str, Any]]:
    today = datetime.now().date()
    items = read_state(FOLLOWUPS_PATH)
    reviews_by_task = {item.get("task_id"): item for item in read_state(REVIEWS_PATH)}
    for item in items:
        review = reviews_by_task.get(item.get("task_id"))
        if review and not item.get("doctor_review"):
            item["doctor_review"] = review.get("doctor_review") or {}
            if any(word in str(review.get("status", "")) for word in ("已提交", "已复核", "已完成")):
                item["doctor_reviewed"] = True
                if item.get("status") not in {"已反馈", "已完成"}:
                    item["status"] = "医生已复核"
        status = item.get("status", "")
        terminal = any(word in status for word in ["已反馈", "已完成"])
        try:
            due = datetime.strptime(item.get("due_date", ""), "%Y-%m-%d").date()
            item["overdue"] = due < today and not terminal
            if item["overdue"]:
                item["status"] = "逾期未反馈"
        except Exception:
            item["overdue"] = False
    return items


def compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def decode_data_url(image_data: str) -> bytes:
    if not image_data:
        return b""
    payload = image_data.split(",", 1)[1] if "," in image_data else image_data
    return base64.b64decode(payload)


def short_id(prefix: str) -> str:
    return prefix + "-" + uuid.uuid4().hex[:8].upper()


class OcrService:
    """Real local OCR adapter with transparent fallback for demos."""

    def __init__(self) -> None:
        self._rapid_engine: Any | None = None
        self._paddle_engine: Any | None = None

    def status(self) -> dict[str, Any]:
        engines = []
        if importlib.util.find_spec("paddleocr"):
            engines.append("paddleocr")
        if importlib.util.find_spec("rapidocr_onnxruntime"):
            engines.append("rapidocr_onnxruntime")
        if importlib.util.find_spec("pypdf"):
            engines.append("pypdf_text_layer")
        if importlib.util.find_spec("fitz"):
            engines.append("pymupdf_pdf_render")
        if importlib.util.find_spec("pytesseract"):
            engines.append("pytesseract")
        if shutil.which("tesseract"):
            engines.append("tesseract_cli")
        return {
            "available": engines,
            "preferred": engines[0] if engines else "manual_review",
            "fallback": "manual_review",
            "quality_plan": [
                "优先解析 PDF 真实文本层，避免截图 OCR 误差。",
                "图片 OCR 使用 EXIF 方向修正、放大、CLAHE、自适应二值化和倾斜校正。",
                "本地安装 paddleocr 后会自动启用高精度中文表格识别；Render 轻量部署默认使用 RapidOCR。",
                "识别结果必须经用户校对后再进入风险分层和医生复核流程。",
            ],
        }

    def recognize(self, image_data: str, image_name: str, sample: dict[str, Any], patient: dict[str, Any]) -> dict[str, Any]:
        image_bytes = b""
        errors: list[str] = []
        try:
            image_bytes = decode_data_url(image_data)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"图片解码失败：{exc}")

        text = ""
        engine = "sample_ocr_fallback"
        confidence = None

        if image_bytes:
            is_pdf = image_name.lower().endswith(".pdf") or image_data.startswith("data:application/pdf")
            if is_pdf:
                pdf_result = self._recognize_pdf(image_bytes, errors)
                if pdf_result["text"]:
                    text = pdf_result["text"]
                    engine = pdf_result["engine"]
                    confidence = pdf_result["confidence"]
            if not compact_text(text) and not is_pdf:
                image_result = self._recognize_image(image_bytes, errors)
                if image_result["text"]:
                    text = image_result["text"]
                    engine = image_result["engine"]
                    confidence = image_result["confidence"]

        sample_used = False
        needs_manual_review = False

        if not compact_text(text):
            text = ""
            message = "OCR未能识别出有效指标，请在页面中按“项目 数值 单位”的格式手动补录或校对。"
            needs_manual_review = True
        elif engine == "pypdf_text_layer":
            message = "已从 PDF 真实文本层提取报告内容，并转换为结构化指标。"
        elif engine.startswith("pymupdf_pdf_render"):
            message = "已将扫描 PDF 页面渲染成图片，并调用本地 OCR 转换为结构化指标。"
        else:
            message = "已使用本地OCR引擎识别图片，并尝试转换为结构化指标。"

        rows = rows_from_report_text(text)
        if compact_text(text) and not rows:
            needs_manual_review = True
            message = "OCR读到了文字，但没有识别出可结构化指标，请先校对或按示例格式补录。"
        return {
            "text": text,
            "rows": rows,
            "patient": patient or sample["patient"],
            "engine": engine,
            "engine_status": self.status(),
            "confidence": confidence,
            "image_name": image_name,
            "image_bytes": len(image_bytes),
            "message": message,
            "errors": errors[-3:],
            "needs_manual_review": needs_manual_review,
            "sample_used": sample_used,
            "manual_template": "",
            "manual_example": "ALT 86 U/L\nAST 62 U/L\nTBIL 24.8 umol/L\nFPG 7.2 mmol/L\nHbA1c 6.6 %",
        }

    def _recognize_image(self, image_bytes: bytes, errors: list[str]) -> dict[str, Any]:
        candidates = [
            self._recognize_with_paddleocr(image_bytes, errors),
            {**self._recognize_with_rapidocr(image_bytes, errors), "engine": "rapidocr_onnxruntime"},
            self._recognize_with_tesseract(image_bytes, errors),
        ]
        best = {"text": "", "confidence": None, "engine": "sample_ocr_fallback", "score": -1.0}
        for item in candidates:
            text = normalize_ocr_text(item.get("text", ""))
            if not text:
                continue
            score = len(rows_from_report_text(text)) * 10 + (item.get("confidence") or 0) + min(len(text), 1200) / 1200
            if score > best["score"]:
                best = {**item, "text": text, "score": score}
        return best

    def _recognize_with_paddleocr(self, image_bytes: bytes, errors: list[str]) -> dict[str, Any]:
        if not importlib.util.find_spec("paddleocr"):
            return {"text": "", "confidence": None, "engine": "paddleocr"}
        try:
            from paddleocr import PaddleOCR

            if self._paddle_engine is None:
                self._paddle_engine = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
            best = {"text": "", "confidence": None, "score": -1.0, "engine": "paddleocr"}
            for variant_name, variant_bytes in self._image_variants(image_bytes, errors, include_rotations=False):
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    tmp.write(variant_bytes)
                    image_path = Path(tmp.name)
                try:
                    result = self._paddle_engine.ocr(str(image_path), cls=True)
                finally:
                    image_path.unlink(missing_ok=True)
                lines: list[str] = []
                confidences: list[float] = []
                for page in result or []:
                    for row in page or []:
                        if len(row) < 2:
                            continue
                        payload = row[1]
                        if isinstance(payload, (list, tuple)) and payload:
                            text = str(payload[0]).strip()
                            if text:
                                lines.append(text)
                            if len(payload) > 1 and isinstance(payload[1], (int, float)):
                                confidences.append(float(payload[1]))
                text = normalize_ocr_text("\n".join(lines))
                confidence = round(sum(confidences) / len(confidences), 3) if confidences else None
                score = len(rows_from_report_text(text)) * 10 + (confidence or 0) + min(len(text), 1200) / 1200
                if score > best["score"]:
                    best = {"text": text, "confidence": confidence, "score": score, "engine": f"paddleocr:{variant_name}"}
                if len(rows_from_report_text(best["text"])) >= 4 and (best["confidence"] or 0) >= 0.85:
                    break
            return {"text": best["text"], "confidence": best["confidence"], "engine": best["engine"]}
        except Exception as exc:  # noqa: BLE001
            errors.append(f"PaddleOCR调用失败：{exc}")
            return {"text": "", "confidence": None, "engine": "paddleocr"}

    def _recognize_with_rapidocr(self, image_bytes: bytes, errors: list[str]) -> dict[str, Any]:
        if not importlib.util.find_spec("rapidocr_onnxruntime"):
            return {"text": "", "confidence": None}
        try:
            from rapidocr_onnxruntime import RapidOCR

            if self._rapid_engine is None:
                self._rapid_engine = RapidOCR()
            best = {"text": "", "confidence": None, "score": -1.0, "variant": ""}
            for variant_name, variant_bytes in self._image_variants(image_bytes, errors, include_rotations=False):
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    tmp.write(variant_bytes)
                    image_path = Path(tmp.name)
                try:
                    result, _ = self._rapid_engine(str(image_path))
                finally:
                    image_path.unlink(missing_ok=True)
                lines, confidences = self._rapid_result_to_lines(result)
                if not lines:
                    continue
                text = normalize_ocr_text("\n".join(lines))
                confidence = round(sum(confidences) / len(confidences), 3) if confidences else None
                score = len(rows_from_report_text(text)) * 10 + (confidence or 0) + min(len(text), 1000) / 1000
                if score > best["score"]:
                    best = {"text": text, "confidence": confidence, "score": score, "variant": variant_name}
                if len(rows_from_report_text(best["text"])) >= 4 and (best["confidence"] or 0) >= 0.85:
                    break
            return {"text": best["text"], "confidence": best["confidence"]}
        except Exception as exc:  # noqa: BLE001
            errors.append(f"RapidOCR失败：{exc}")
            return {"text": "", "confidence": None}

    @staticmethod
    def _rapid_result_to_lines(result: Any) -> tuple[list[str], list[float]]:
        lines: list[str] = []
        confidences: list[float] = []
        for row in result or []:
            text = ""
            score = None
            if isinstance(row, dict):
                text = str(row.get("text") or row.get("rec_text") or "").strip()
                score = row.get("confidence") or row.get("score")
            elif isinstance(row, (list, tuple)):
                if len(row) >= 3 and isinstance(row[1], str):
                    text = row[1].strip()
                    score = row[2]
                elif len(row) >= 2 and isinstance(row[-1], (list, tuple)):
                    payload = row[-1]
                    if payload:
                        text = str(payload[0]).strip()
                    if len(payload) > 1:
                        score = payload[1]
                elif len(row) >= 2:
                    text = str(row[1]).strip()
                    score = row[2] if len(row) > 2 else None
            if text:
                lines.append(text)
            if isinstance(score, (int, float)):
                confidences.append(float(score))
        return lines, confidences

    @staticmethod
    def _image_variants(image_bytes: bytes, errors: list[str], include_rotations: bool = False) -> list[tuple[str, bytes]]:
        variants: list[tuple[str, bytes]] = []
        try:
            from PIL import Image, ImageEnhance, ImageFilter, ImageOps

            def encode_png(image: Any) -> bytes:
                buf = io.BytesIO()
                image.save(buf, format="PNG", optimize=True)
                return buf.getvalue()

            with Image.open(io.BytesIO(image_bytes)) as img:
                rgb = ImageOps.exif_transpose(img).convert("RGB")
                max_variants = max(1, min(6, int(os.getenv("OCR_MAX_VARIANTS", "2"))))
                max_side = max(rgb.width, rgb.height, 1)
                min_side = min(rgb.width, rgb.height, 1)
                scale_down = min(1.0, 1200 / max_side)
                scale_up = 1.0 if min_side >= 760 else min(1.15, 760 / min_side)
                scale = scale_down if scale_down < 1.0 else scale_up
                if abs(scale - 1.0) > 0.05:
                    rgb = rgb.resize((int(rgb.width * scale), int(rgb.height * scale)))
                variants.append(("normalized", encode_png(rgb)))

                if include_rotations and os.getenv("OCR_ENABLE_ROTATION_VARIANTS") == "1":
                    for angle in (90, 180, 270):
                        rotated = rgb.rotate(angle, expand=True)
                        variants.append((f"rotate_{angle}", encode_png(rotated)))
                gray = ImageOps.grayscale(rgb)
                enhanced = ImageEnhance.Contrast(gray).enhance(2.0).filter(ImageFilter.SHARPEN)
                if len(variants) < max_variants:
                    variants.append(("enhanced", encode_png(enhanced)))

                if len(variants) < max_variants and importlib.util.find_spec("cv2") and importlib.util.find_spec("numpy"):
                    import cv2
                    import numpy as np

                    base_arr = np.array(rgb)
                    gray_for_crop = cv2.cvtColor(base_arr, cv2.COLOR_RGB2GRAY)
                    non_white = gray_for_crop < 248
                    coords_crop = np.column_stack(np.where(non_white))
                    crop_sources: list[tuple[str, Any]] = []
                    if coords_crop.size:
                        y0, x0 = coords_crop.min(axis=0)
                        y1, x1 = coords_crop.max(axis=0)
                        margin = 18
                        y0 = max(0, int(y0) - margin)
                        x0 = max(0, int(x0) - margin)
                        y1 = min(base_arr.shape[0], int(y1) + margin)
                        x1 = min(base_arr.shape[1], int(x1) + margin)
                        crop = base_arr[y0:y1, x0:x1]
                        if crop.size and crop.shape[0] > 80 and crop.shape[1] > 80:
                            crop_sources.append(("content_crop", crop))
                            h, w = crop.shape[:2]
                            if h > 360 and len(crop_sources) < 2 and max_variants >= 5:
                                crop_sources.append(("table_content_crop", crop[int(h * 0.30): int(h * 0.94), :]))
                    for crop_name, crop_arr in crop_sources:
                        h, w = crop_arr.shape[:2]
                        target_w = min(1200, max(w, 850))
                        factor = max(1.0, min(1.35, target_w / max(w, 1)))
                        resized = cv2.resize(crop_arr, (int(w * factor), int(h * factor)), interpolation=cv2.INTER_AREA)
                        ok, encoded = cv2.imencode(".png", resized)
                        if ok:
                            variants.append((crop_name, encoded.tobytes()))
                        if len(variants) >= max_variants:
                            break
                        crop_gray = cv2.cvtColor(resized, cv2.COLOR_RGB2GRAY)
                        crop_clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8)).apply(crop_gray)
                        ok, encoded = cv2.imencode(".png", crop_clahe)
                        if ok and len(variants) < max_variants:
                            variants.append((f"{crop_name}_clahe", encoded.tobytes()))
                        if len(variants) >= max_variants:
                            break

                    if len(variants) < max_variants:
                        arr = np.array(enhanced)
                        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(arr)
                        ok, encoded = cv2.imencode(".png", clahe)
                        if ok:
                            variants.append(("clahe", encoded.tobytes()))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Image preprocessing failed: {exc}")
        if not variants:
            variants.append(("original", image_bytes))
        return variants

    def _recognize_pdf(self, pdf_bytes: bytes, errors: list[str]) -> dict[str, Any]:
        text = self._extract_pdf_text_layer(pdf_bytes, errors)
        if compact_text(text):
            return {"text": normalize_ocr_text(text), "confidence": 1.0, "engine": "pypdf_text_layer"}

        if not importlib.util.find_spec("fitz"):
            errors.append("扫描版PDF需要PyMuPDF渲染后再OCR，当前环境未安装PyMuPDF。")
            return {"text": "", "confidence": None, "engine": "sample_ocr_fallback"}

        try:
            import fitz

            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            page_texts = []
            confidences = []
            for page in doc[: min(len(doc), 5)]:
                pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
                png_bytes = pix.tobytes("png")
                result = self._recognize_image(png_bytes, errors)
                if result["text"]:
                    page_texts.append(result["text"])
                    if result["confidence"] is not None:
                        confidences.append(result["confidence"])
            doc.close()
            return {
                "text": normalize_ocr_text("\n".join(page_texts)),
                "confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
                "engine": "pymupdf_pdf_render+best_ocr",
            }
        except Exception as exc:  # noqa: BLE001
            errors.append(f"PDF渲染OCR失败：{exc}")
            return {"text": "", "confidence": None, "engine": "sample_ocr_fallback"}

    @staticmethod
    def _extract_pdf_text_layer(pdf_bytes: bytes, errors: list[str]) -> str:
        if not importlib.util.find_spec("pypdf"):
            return ""
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(pdf_bytes))
            pages = []
            for page in reader.pages[:10]:
                pages.append(page.extract_text() or "")
            return "\n".join(pages)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"PDF文本层提取失败：{exc}")
            return ""

    def _recognize_with_tesseract(self, image_bytes: bytes, errors: list[str]) -> dict[str, Any]:
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(image_bytes)
                image_path = Path(tmp.name)
            try:
                if importlib.util.find_spec("pytesseract"):
                    from PIL import Image, ImageEnhance, ImageOps
                    import pytesseract

                    with Image.open(image_path) as img:
                        processed = ImageOps.grayscale(img)
                        processed = ImageEnhance.Contrast(processed).enhance(1.6)
                        text = pytesseract.image_to_string(processed, lang="chi_sim+eng")
                    return {"text": normalize_ocr_text(text), "confidence": None, "engine": "pytesseract"}

                exe = shutil.which("tesseract")
                if exe:
                    proc = subprocess.run(
                        [exe, str(image_path), "stdout", "-l", "chi_sim+eng", "--psm", "6"],
                        capture_output=True,
                        text=True,
                        timeout=20,
                        check=False,
                    )
                    if proc.returncode == 0:
                        return {"text": normalize_ocr_text(proc.stdout), "confidence": None, "engine": "tesseract_cli"}
                    errors.append(f"Tesseract失败：{proc.stderr.strip()[:120]}")
            finally:
                image_path.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Tesseract调用失败：{exc}")
        return {"text": "", "confidence": None, "engine": "sample_ocr_fallback"}


def normalize_ocr_text(text: str) -> str:
    text = (text or "").replace("｜", "|").replace("：", " ")
    replacements = {
        "｜": "|",
        "丨": "|",
        "│": "|",
        "：": ":",
        "﹕": ":",
        "μmol": "umol",
        "µmol": "umol",
        "×10^9/L": "10^9/L",
        "x10^9/L": "10^9/L",
        "↑": " high",
        "↓": " low",
        "—": "-",
        "–": "-",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    text = re.sub(r"(?i)hba\s*1\s*c", "HbA1c", text)
    text = re.sub(r"(?i)\b(ALT|AST|WBC|HGB|PLT|FPG|CREA|TBIL)([-+]?\d)", r"\1 \2", text)
    text = re.sub(r"([A-Za-z]{2,8})([-+]?\d+(?:\.\d+)?)(?=\s|$)", r"\1 \2", text)
    text = re.sub(r"\s*\|\s*", " ", text)
    return "\n".join(compact_text(line) for line in text.splitlines() if compact_text(line))


NUMBER_PATTERN = r"[-+]?\d+(?:\.\d+)?"
RANGE_PATTERN = re.compile(
    rf"(?P<low>{NUMBER_PATTERN})\s*(?:-|~|–|—|至|到)\s*(?P<high>{NUMBER_PATTERN})|"
    rf"(?P<op><=|>=|≤|≥|<|>)\s*(?P<single>{NUMBER_PATTERN})",
    re.I,
)
UNIT_RE = re.compile(rf"^{UNIT_PATTERN}$", re.I)
UNIT_SEARCH_RE = re.compile(UNIT_PATTERN, re.I)
NOISE_REPORT_LINES = {
    "项目",
    "结果",
    "单位",
    "参考区间",
    "报告详细",
    "报告解读",
    "就诊人",
    "开方医生",
    "开方科室",
    "报告时间",
    "采样时间",
    "检验者",
    "审核者",
    "病案号",
    "收样时间",
}
POSTFIX_FRAGMENT_LINES = {"蛋白胆固醇", "蛋白", "胆固醇", "素"}


def clean_metric_name(name: str) -> str:
    cleaned = compact_text(name)
    cleaned = re.sub(r"^[*#·•\-—\s]+", "", cleaned)
    cleaned = re.sub(r"[:：,，;；。]+$", "", cleaned)
    cleaned = cleaned.replace(" ", "")
    return cleaned


def metric_key(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", clean_metric_name(name)).lower()


def looks_like_report_noise(line: str) -> bool:
    text = compact_text(line)
    stripped = clean_metric_name(text)
    if not stripped:
        return True
    if stripped in NOISE_REPORT_LINES:
        return True
    if "http" in text.lower() or ".cn" in text.lower() or ".com" in text.lower():
        return True
    if re.fullmatch(r"\d{1,2}:\d{2}.*", text):
        return True
    if re.fullmatch(r".*5G[A-Za-z]?\s*\(?\d{1,3}\)?.*", text):
        return True
    if re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}.*", text):
        return True
    if stripped in {"high", "low", "↑", "↓"}:
        return True
    return False


def parse_reference_range(text: str) -> dict[str, Any] | None:
    candidate = compact_text(text)
    if not candidate:
        return None
    match = RANGE_PATTERN.search(candidate)
    if not match:
        return None
    if match.group("low") and match.group("high"):
        low = float(match.group("low"))
        high = float(match.group("high"))
        if low > high:
            low, high = high, low
        return {"reference_min": low, "reference_max": high, "reference_text": match.group(0)}
    single = float(match.group("single"))
    op = match.group("op")
    if op in {"<", "<=", "≤"}:
        return {"reference_min": None, "reference_max": single, "reference_text": match.group(0)}
    return {"reference_min": single, "reference_max": None, "reference_text": match.group(0)}


def extract_numeric_value(text: str) -> float | None:
    candidate = compact_text(text)
    if not candidate or parse_reference_range(candidate):
        return None
    numbers = re.findall(NUMBER_PATTERN, candidate)
    if not numbers:
        return None
    try:
        return float(numbers[0])
    except ValueError:
        return None


def extract_unit(text: str) -> str:
    match = UNIT_SEARCH_RE.search(text or "")
    return match.group(0) if match else ""


def looks_like_metric_name(line: str) -> bool:
    text = clean_metric_name(line)
    if looks_like_report_noise(text):
        return False
    if parse_reference_range(text):
        return False
    if extract_numeric_value(text) is not None:
        return False
    if extract_unit(text) and UNIT_RE.fullmatch(text):
        return False
    if len(text) < 2 or len(text) > 32:
        return False
    return bool(re.search(r"[\u4e00-\u9fffA-Za-z]", text))


def parse_generic_report_rows(text: str) -> list[dict[str, Any]]:
    lines = [compact_text(line) for line in normalize_ocr_text(text).splitlines() if compact_text(line)]
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, float, str]] = set()

    def add_row(name: str, value: float, unit: str, reference: dict[str, Any], source: str) -> None:
        metric_name = clean_metric_name(name)
        if not metric_name or looks_like_report_noise(metric_name):
            return
        row_key = (metric_key(metric_name), round(value, 4), unit.lower())
        if row_key in seen:
            return
        seen.add(row_key)
        ref_min = reference.get("reference_min")
        ref_max = reference.get("reference_max")
        status = "normal"
        if ref_min is not None and value < float(ref_min):
            status = "low"
        elif ref_max is not None and value > float(ref_max):
            status = "high"
        rows.append(
            {
                "code": "AUTO_" + uuid.uuid5(uuid.NAMESPACE_DNS, metric_key(metric_name)).hex[:8].upper(),
                "name": metric_name,
                "value": value,
                "unit": unit,
                "reference_min": ref_min,
                "reference_max": ref_max,
                "reference_text": reference.get("reference_text") or "",
                "status": status,
                "line": source,
                "confidence": 0.78,
                "dynamic": True,
            }
        )

    same_line_re = re.compile(
        rf"(?P<name>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9*#·•()（）/\-\s]{{1,40}}?)"
        rf"\s+(?P<value>{NUMBER_PATTERN})\s*(?:↑|↓|high|low)?\s*(?P<unit>{UNIT_PATTERN})\s+"
        rf"(?P<ref>(?:{NUMBER_PATTERN}\s*(?:-|~|–|—|至|到)\s*{NUMBER_PATTERN})|(?:<=|>=|≤|≥|<|>)\s*{NUMBER_PATTERN})",
        re.I,
    )
    for line in lines:
        match = same_line_re.search(line)
        if match:
            reference = parse_reference_range(match.group("ref"))
            if reference:
                add_row(match.group("name"), float(match.group("value")), match.group("unit"), reference, line)

    for idx, line in enumerate(lines):
        if clean_metric_name(line) in POSTFIX_FRAGMENT_LINES and idx + 1 < len(lines) and looks_like_metric_name(lines[idx + 1]):
            continue
        if not looks_like_metric_name(line):
            continue
        name_parts = [line]
        scan_start = idx + 1
        if scan_start < len(lines) and looks_like_metric_name(lines[scan_start]) and len(clean_metric_name(lines[scan_start])) <= 6:
            next_two = lines[scan_start + 1: scan_start + 4]
            if any(extract_numeric_value(candidate) is not None for candidate in next_two):
                name_parts.append(lines[scan_start])
                scan_start += 1

        value = None
        value_idx = None
        unit = ""
        unit_idx = None
        reference = None
        ref_idx = None
        for look_idx in range(scan_start, min(len(lines), idx + 10)):
            candidate = lines[look_idx]
            if value is None:
                value = extract_numeric_value(candidate)
                if value is not None:
                    value_idx = look_idx
                    unit = extract_unit(candidate) or unit
                    if not reference:
                        reference = parse_reference_range(candidate)
                    continue
            if value is not None and not unit:
                found_unit = extract_unit(candidate)
                if found_unit:
                    unit = found_unit
                    unit_idx = look_idx
                    continue
            if value is not None and reference is None:
                reference = parse_reference_range(candidate)
                if reference:
                    ref_idx = look_idx
                    break
            if look_idx > scan_start and looks_like_metric_name(candidate) and value is None:
                break

        if value is None or not unit or reference is None:
            continue

        if ref_idx is not None and ref_idx + 1 < len(lines):
            fragment = clean_metric_name(lines[ref_idx + 1])
            if (
                fragment
                and looks_like_metric_name(fragment)
                and len(fragment) <= 8
                and not fragment.startswith(("①", "②", "③"))
                and any(token in clean_metric_name("".join(name_parts)) for token in ("密度脂", "促黄体"))
            ):
                name_parts.append(fragment)

        source_end = ref_idx if ref_idx is not None else (unit_idx or value_idx or idx)
        source = " ".join(lines[idx: min(len(lines), source_end + 2)])
        add_row("".join(name_parts), float(value), unit, reference, source)
    return rows


def dynamic_knowledge_from_row(row: dict[str, Any]) -> dict[str, Any]:
    name = row["name"]
    unit = row.get("unit", "")
    ref_min = row.get("reference_min")
    ref_max = row.get("reference_max")
    low = float(ref_min) if ref_min is not None else float("-inf")
    high = float(ref_max) if ref_max is not None else float("inf")
    reference_text = row.get("reference_text") or (
        f"{ref_min} - {ref_max} {unit}" if ref_min is not None and ref_max is not None else ""
    )
    return {
        "code": row["code"],
        "name": name,
        "unit": unit,
        "reference_min": low,
        "reference_max": high,
        "reference_text": reference_text,
        "red_flag_low": None,
        "red_flag_high": None,
        "high_explanation": f"{name} 高于本次报告给出的参考区间，需要结合年龄、性别、采样状态和既往病史由医生判断意义。",
        "low_explanation": f"{name} 低于本次报告给出的参考区间，需要结合年龄、性别、采样状态和既往病史由医生判断意义。",
        "source": "OCR报告参考区间（动态识别指标，非内置医学知识库）",
        "dynamic": True,
    }


class ReportParserAgent:
    def __init__(self, knowledge: list[dict[str, Any]]):
        self.knowledge = knowledge

    def parse(self, report_text: str, patient: dict[str, Any]) -> dict[str, Any]:
        lines = [compact_text(line) for line in report_text.splitlines() if compact_text(line)]
        matches: list[dict[str, Any]] = []
        for item in self.knowledge:
            found_index = self._find_line_index(lines, item)
            if found_index is None:
                continue
            found_line = lines[found_index]
            value, source_line, confidence = self._extract_value_with_context(lines, found_index, item)
            if value is None:
                continue
            matches.append(
                {
                    "code": item["code"],
                    "name": item["name"],
                    "value": value,
                    "unit": item["unit"],
                    "line": source_line or found_line,
                    "confidence": confidence,
                }
            )
        matched_codes = {item["code"] for item in matches}
        matched_alias_keys: set[str] = set()
        for item in self.knowledge:
            if item["code"] not in matched_codes:
                continue
            for alias in [item["code"], item["name"], *item.get("aliases", [])]:
                matched_alias_keys.add(metric_key(alias))

        for row in parse_generic_report_rows(report_text):
            row_name_key = metric_key(row["name"])
            if row_name_key in matched_alias_keys:
                continue
            duplicate_known = False
            for item in self.knowledge:
                if item["code"] in matched_codes:
                    aliases = [item["name"], item["code"], *item.get("aliases", [])]
                    if any(metric_key(alias) and metric_key(alias) in row_name_key for alias in aliases):
                        duplicate_known = True
                        break
            if duplicate_known:
                continue
            dynamic_item = dynamic_knowledge_from_row(row)
            matches.append(
                {
                    "code": row["code"],
                    "name": row["name"],
                    "value": row["value"],
                    "unit": row["unit"],
                    "line": row["line"],
                    "confidence": row.get("confidence", 0.78),
                    "knowledge": dynamic_item,
                    "dynamic": True,
                }
            )
        return {
            "patient": patient,
            "raw_text": report_text,
            "items": matches,
            "unparsed_lines": [line for line in lines if not any(m["line"] == line for m in matches)],
        }

    @staticmethod
    def _find_line(lines: list[str], item: dict[str, Any]) -> str | None:
        index = ReportParserAgent._find_line_index(lines, item)
        return lines[index] if index is not None else None

    @staticmethod
    def _find_line_index(lines: list[str], item: dict[str, Any]) -> int | None:
        aliases = [item["name"], item["code"], *item.get("aliases", [])]
        for idx, line in enumerate(lines):
            if any(ReportParserAgent._alias_span(line, alias) for alias in aliases):
                return idx
            if re.search(r"[\u4e00-\u9fff]", line) and not re.search(r"\d", line):
                joined = "".join(lines[idx: idx + 3])
                if any(
                    not re.fullmatch(r"[A-Za-z0-9]+", alias) and ReportParserAgent._alias_span(joined, alias)
                    for alias in aliases
                ):
                    return idx
        return None

    @staticmethod
    def _extract_value_with_context(lines: list[str], index: int, item: dict[str, Any]) -> tuple[float | None, str, float]:
        line = lines[index]
        value = ReportParserAgent._extract_value(line, item)
        if value is not None:
            confidence = 0.92 if any(alias.upper() in line.upper() for alias in item.get("aliases", [])) else 0.84
            return value, line, confidence

        # OCR on mobile reports often returns a table as separate lines:
        # item name / value / unit / reference range. Join a short forward window
        # so the parser can recover rows such as "血清孕酮" + "0.40" + "ng/ml".
        window = " ".join(lines[index: index + 6])
        value = ReportParserAgent._extract_value(window, item)
        if value is not None:
            return value, window, 0.86

        numeric_lines = []
        for tail_line in lines[index + 1: index + 6]:
            if re.search(UNIT_PATTERN, tail_line, re.I):
                numeric_lines.append(tail_line)
                continue
            if re.search(r"[-+]?\d+(?:\.\d+)?", tail_line):
                numeric_lines.append(tail_line)
            if len(numeric_lines) >= 2:
                break
        fallback_window = f"{line} {' '.join(numeric_lines)}"
        value = ReportParserAgent._extract_value(fallback_window, item)
        if value is not None:
            return value, fallback_window, 0.8
        return None, line, 0.0

    @staticmethod
    def _extract_value(line: str, item: dict[str, Any]) -> float | None:
        aliases = sorted([item["name"], item["code"], *item.get("aliases", [])], key=len, reverse=True)
        for alias in aliases:
            span = ReportParserAgent._alias_span(line, alias)
            if span:
                tail = line[span[1]:]
                match = VALUE_WITH_UNIT_RE.search(tail) or re.search(r"[-+]?\d+(?:\.\d+)?", tail)
                if match:
                    return float(match.group(1) if match.lastindex else match.group(0))
        match = VALUE_WITH_UNIT_RE.search(line) or re.search(r"[-+]?\d+(?:\.\d+)?", line)
        if not match:
            return None
        return float(match.group(1) if match.lastindex else match.group(0))

    @staticmethod
    def _alias_span(line: str, alias: str) -> tuple[int, int] | None:
        if re.fullmatch(r"[A-Za-z0-9]+", alias):
            match = re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", line, re.I)
            return match.span() if match else None
        idx = line.upper().find(alias.upper())
        return (idx, idx + len(alias)) if idx >= 0 else None


class RiskStratificationAgent:
    def __init__(self, knowledge: list[dict[str, Any]]):
        self.by_code = {item["code"]: item for item in knowledge}

    def stratify(self, parsed: dict[str, Any]) -> dict[str, Any]:
        symptoms = compact_text(parsed.get("patient", {}).get("symptoms", ""))
        red_symptom_terms = ["胸痛", "呼吸困难", "意识", "昏迷", "呕血", "黑便", "剧烈腹痛"]
        red_symptom_hits = [term for term in red_symptom_terms if self._has_unnegated_term(symptoms, term)]
        interpreted = []
        score = 0
        red_flags = []

        for obs in parsed["items"]:
            item = obs.get("knowledge") or self.by_code.get(obs["code"])
            if not item:
                continue
            value = obs["value"]
            status = "normal"
            severity = 0
            if value < item["reference_min"]:
                status = "low"
                severity = self._severity(item["reference_min"] - value, item)
            elif value > item["reference_max"]:
                status = "high"
                severity = self._severity(value - item["reference_max"], item)
            if item.get("red_flag_low") is not None and value <= item["red_flag_low"]:
                red_flags.append(f"{item['name']}低于红旗阈值")
                severity = max(severity, 3)
            if item.get("red_flag_high") is not None and value >= item["red_flag_high"]:
                red_flags.append(f"{item['name']}高于红旗阈值")
                severity = max(severity, 3)
            score += severity
            interpreted.append({**obs, "status": status, "severity": severity, "knowledge": item})

        if red_symptom_hits:
            red_flags.append("出现红旗症状：" + "、".join(red_symptom_hits))
            score += 4

        if red_flags:
            level = "red"
        elif score >= 5:
            level = "high"
        elif score >= 2:
            level = "medium"
        else:
            level = "low"
        requires_review = level in {"high", "red"} or any(item["confidence"] < 0.85 for item in parsed["items"])
        return {
            "risk_level": level,
            "risk_score": score,
            "red_flags": red_flags,
            "requires_review": requires_review,
            "items": interpreted,
        }

    @staticmethod
    def _severity(delta: float, item: dict[str, Any]) -> int:
        low = item.get("reference_min")
        high = item.get("reference_max")
        if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
            return 1
        if not math.isfinite(low) or not math.isfinite(high):
            return 1
        span = max(high - low, 1)
        ratio = delta / span
        if ratio > 1.2:
            return 3
        if ratio > 0.35:
            return 2
        return 1

    @staticmethod
    def _has_unnegated_term(text: str, term: str) -> bool:
        idx = text.find(term)
        while idx >= 0:
            prefix = text[max(0, idx - 6):idx]
            if not any(neg in prefix for neg in ["无", "未见", "否认", "没有", "无明显"]):
                return True
            idx = text.find(term, idx + len(term))
        return False


class ExplanationAgent:
    def explain(self, risk: dict[str, Any]) -> list[dict[str, Any]]:
        explanations = []
        for obs in risk["items"]:
            item = obs["knowledge"]
            standard = standard_for_code(obs["code"]) or {}
            if obs["status"] == "normal":
                message = f"{item['name']}在参考范围内，本次样例未提示该指标异常。"
            elif obs["status"] == "high":
                message = item["high_explanation"]
            else:
                message = item["low_explanation"]
            explanations.append(
                {
                    "code": obs["code"],
                    "name": obs["name"],
                    "value": obs["value"],
                    "unit": obs["unit"],
                    "reference": item.get("reference_text") or f"{item['reference_min']} - {item['reference_max']} {item['unit']}",
                    "status": obs["status"],
                    "severity": obs["severity"],
                    "message": message,
                    "source": item["source"],
                    "standard_id": standard.get("id"),
                    "standard": standard,
                }
            )
        return explanations


class FollowUpAgent:
    def plan(self, risk: dict[str, Any], patient: dict[str, Any]) -> dict[str, Any]:
        level = risk["risk_level"]
        if level == "red":
            days, priority, title = 0, "紧急", "建议立即联系医生或线下就诊"
        elif level == "high":
            days, priority, title = 7, "高", "一周内完成医生复核或复查"
        elif level == "medium":
            days, priority, title = 30, "中", "一个月内复查并提交反馈"
        else:
            days, priority, title = 90, "低", "按常规体检周期复查"
        due = datetime.now() + timedelta(days=days)
        return {
            "follow_id": short_id("FU"),
            "title": title,
            "priority": priority,
            "due_date": due.strftime("%Y-%m-%d"),
            "questions": [
                "是否已线下就诊或咨询医生？",
                "是否出现新的不适症状？",
                "是否完成复查或准备复查？",
            ],
            "owner": patient.get("name") or "模拟患者",
            "status": "待提醒" if days > 0 else "需立即处理",
        }


class ComprehensiveAssessmentAgent:
    """Build a combined clinical picture from metric clusters and patient context."""

    @staticmethod
    def build(
        parsed: dict[str, Any],
        risk: dict[str, Any],
        patient: dict[str, Any],
        user_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        profile = user_profile or {}
        items = {item["code"]: item for item in risk.get("items", [])}
        abnormal_codes = {code for code, item in items.items() if item.get("status") != "normal"}
        symptoms = compact_text(patient.get("symptoms", ""))
        chronic = compact_text(profile.get("chronic_diseases", "") or profile.get("chronicDiseases", ""))
        medications = compact_text(profile.get("medications", ""))

        clusters: list[dict[str, Any]] = []
        recommendations: list[str] = []
        medication_discussion: list[str] = []
        urgent_actions: list[str] = []
        risk_bonus = 0

        liver_codes = {"ALT", "AST", "TBIL"} & abnormal_codes
        glucose_codes = {"FPG", "HBA1C"} & abnormal_codes
        blood_codes = {"WBC", "NEUT", "HGB", "PLT"} & abnormal_codes
        kidney_codes = {"CREA"} & abnormal_codes
        hormone_all_codes = {"PROG", "PRL", "FSH", "LH", "TESTO", "E2"} & set(items)
        hormone_abnormal_codes = hormone_all_codes & abnormal_codes
        lipid_codes = {
            code
            for code, item in items.items()
            if any(token in item.get("name", "") for token in ("甘油三酯", "胆固醇", "低密度脂", "高密度脂", "LDL", "HDL", "TC", "TG"))
        }
        lipid_abnormal_codes = lipid_codes & abnormal_codes

        if liver_codes:
            confidence = "较高" if len(liver_codes) >= 2 else "中等"
            clusters.append(
                {
                    "id": "hepatic_pattern",
                    "title": "肝功能/肝胆相关异常",
                    "confidence": confidence,
                    "codes": sorted(liver_codes),
                    "reason": "转氨酶和/或胆红素异常需要结合饮酒、药物、病毒性肝炎、脂肪肝和胆道情况判断。",
                }
            )
            recommendations.extend(["复查肝功能并补充 GGT、ALP、白蛋白、凝血功能。", "若有饮酒、保健品或近期新药，应带药品清单给医生核对。"])
            medication_discussion.append("不要自行服用所谓保肝药；是否需要保肝、抗病毒或其他治疗，应由医生明确病因后决定。")
            if "TBIL" in liver_codes or {"ALT", "AST"} <= liver_codes:
                risk_bonus += 1

        if glucose_codes:
            confidence = "较高" if {"FPG", "HBA1C"} <= glucose_codes else "中等"
            clusters.append(
                {
                    "id": "glucose_metabolism",
                    "title": "糖代谢异常风险",
                    "confidence": confidence,
                    "codes": sorted(glucose_codes),
                    "reason": "空腹血糖和糖化血红蛋白需要结合是否空腹、近期用药、体重和既往糖尿病史综合判断。",
                }
            )
            recommendations.extend(["确认抽血是否空腹，建议复查空腹血糖、HbA1c，必要时做口服葡萄糖耐量试验。", "同步记录体重、腰围、血压和血脂，便于判断代谢综合征风险。"])
            medication_discussion.append("若复查仍达到糖尿病诊断范围，可与医生讨论二甲双胍等降糖治疗是否适合；不要自行购买或调整降糖药。")
            if {"FPG", "HBA1C"} <= glucose_codes:
                risk_bonus += 1

        if lipid_codes:
            clusters.append(
                {
                    "id": "lipid_metabolism_panel",
                    "title": "血脂代谢相关指标组合",
                    "confidence": "中等" if lipid_abnormal_codes else "较低",
                    "codes": sorted(lipid_codes),
                    "reason": "甘油三酯、总胆固醇和高/低密度脂蛋白胆固醇需要结合年龄、血压、血糖、吸烟史、家族史和心脑血管病危险分层综合判断。",
                }
            )
            recommendations.extend(
                [
                    "建议保留完整血脂报告，并结合血压、空腹血糖或HbA1c、体重和既往心脑血管病史一起评估。",
                    "如果低密度脂蛋白胆固醇、总胆固醇或甘油三酯异常，应由医生按总体心血管风险决定复查周期和是否需要干预。",
                ]
            )
            medication_discussion.append("降脂药物如他汀、依折麦布或贝特类是否适合，需要医生结合ASCVD风险、肝肾功能和复查结果决定，不建议自行购买服用。")
            if lipid_abnormal_codes:
                risk_bonus += 1

        if liver_codes and glucose_codes:
            clusters.append(
                {
                    "id": "metabolic_liver_overlap",
                    "title": "代谢相关脂肪肝/肝功能异常线索",
                    "confidence": "中等",
                    "codes": sorted(liver_codes | glucose_codes),
                    "reason": "肝酶升高与糖代谢异常同时出现时，需要关注脂肪肝、体重、血脂和胰岛素抵抗相关因素。",
                }
            )
            recommendations.append("建议加做腹部超声、血脂、尿酸，并把近期饮酒和体重变化告诉医生。")
            risk_bonus += 1

        if hormone_all_codes:
            clusters.append(
                {
                    "id": "reproductive_endocrine_panel",
                    "title": "妇产/内分泌性激素组合",
                    "confidence": "中等" if hormone_abnormal_codes else "较低",
                    "codes": sorted(hormone_all_codes),
                    "reason": "性激素六项不能只看单个数值，必须结合月经周期第几天、是否妊娠/备孕、是否使用黄体酮或避孕药等背景解释。",
                }
            )
            recommendations.extend(
                [
                    "补充末次月经日期、抽血处于周期第几天、是否备孕/妊娠、是否使用黄体酮或避孕药等信息。",
                    "如孕酮、雌二醇、LH/FSH等与当前周期阶段不匹配，建议带报告咨询妇产科或内分泌医生。",
                    "如果伴随停经、异常出血、腹痛或已怀孕，应优先线下就医确认。",
                ]
            )
            medication_discussion.append("不要自行使用黄体酮、促排卵药或激素类药物；是否需要补充或调整激素治疗，应由妇产科医生结合周期、超声和复查结果决定。")
            if hormone_abnormal_codes:
                risk_bonus += 1

        if blood_codes:
            clusters.append(
                {
                    "id": "blood_routine_pattern",
                    "title": "血常规异常线索",
                    "confidence": "中等",
                    "codes": sorted(blood_codes),
                    "reason": "白细胞/中性粒、血红蛋白和血小板需要结合发热、感染、出血、贫血症状和近期用药判断。",
                }
            )
            recommendations.append("建议复查血常规，若伴发热、出血、黑便、明显乏力或胸闷，应尽快线下就医。")
            if "HGB" in blood_codes:
                medication_discussion.append("贫血相关铁剂、叶酸或维生素B12需先明确缺铁、失血或营养缺乏原因，再由医生指导使用。")
            if "WBC" in blood_codes or "NEUT" in blood_codes:
                medication_discussion.append("是否需要抗感染药物取决于症状、体温、CRP/降钙素原等证据，不建议自行使用抗生素。")

        if kidney_codes:
            clusters.append(
                {
                    "id": "kidney_function",
                    "title": "肾功能异常线索",
                    "confidence": "中等",
                    "codes": sorted(kidney_codes),
                    "reason": "肌酐异常需结合年龄、肌肉量、尿常规、肾病史和近期脱水/用药情况判断。",
                }
            )
            recommendations.extend(["建议复查肾功能、尿常规和尿微量白蛋白/肌酐比。", "近期避免自行服用可能影响肾功能的止痛药或不明保健品。"])
            medication_discussion.append("降压、降糖或肾脏保护用药需要结合 eGFR 和尿蛋白情况由医生决定。")

        if any(term in symptoms for term in ["胸痛", "呼吸困难", "呕血", "黑便", "意识", "昏迷"]):
            urgent_actions.append("如胸痛、呼吸困难、呕血、黑便或意识异常正在发生，应立即线下就医或急诊处理。")
            risk_bonus += 2

        if medications:
            recommendations.append("请把近期用药、保健品和中成药名称补充给医生，部分药物会影响肝肾功能或血常规。")
        if chronic:
            recommendations.append("既往慢病史会改变风险解释和复查周期，建议随报告一并给医生查看。")

        if not clusters:
            clusters.append(
                {
                    "id": "no_obvious_cluster",
                    "title": "未形成明确异常组合",
                    "confidence": "低",
                    "codes": [],
                    "reason": "当前可结构化指标未显示明确异常组合，仍需结合报告完整项目和个人症状判断。",
                }
            )
            recommendations.append("若没有明显不适，可按常规体检周期复查；如有症状，应补充症状后重新评估。")

        summary = "；".join(cluster["title"] for cluster in clusters[:3])
        return {
            "summary": summary,
            "clusters": clusters,
            "risk_adjustment": risk_bonus,
            "recommendations": list(dict.fromkeys(recommendations))[:8],
            "medication_discussion": list(dict.fromkeys(medication_discussion))[:6],
            "urgent_actions": urgent_actions,
            "context_used": {
                "symptoms": symptoms,
                "chronic_diseases": chronic,
                "medications": medications,
            },
            "disclaimer": "以上为报告解读和就医沟通辅助，不构成诊断或处方。药物需由医生结合病史、体征和复查结果决定。",
        }


class DeepSeekClient:
    def __init__(self) -> None:
        self.api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
        self.timeout = float(os.environ.get("DEEPSEEK_TIMEOUT", "18"))
        self.reasoning_effort = os.environ.get("DEEPSEEK_REASONING_EFFORT", "high")

    def configured(self) -> bool:
        return bool(self.api_key) and os.environ.get("DEEPSEEK_DISABLE", "").lower() not in {"1", "true", "yes"}

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.configured(),
            "base_url": self.base_url,
            "model": self.model,
            "mode": "openai_compatible_chat_completions",
        }

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.2, max_tokens: int = 900) -> dict[str, Any]:
        if not self.configured():
            return {"ok": False, "error": "DEEPSEEK_API_KEY 未配置"}
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if self.model.endswith("-pro"):
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = self.reasoning_effort
        request = Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - configured API endpoint
                data = json.loads(response.read().decode("utf-8"))
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {"ok": True, "content": content, "raw": data}
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")[:500]
            return {"ok": False, "error": f"DeepSeek HTTP {exc.code}: {detail}"}
        except (URLError, TimeoutError, OSError) as exc:
            return {"ok": False, "error": f"DeepSeek 调用失败：{exc}"}

    def enhance_analysis(self, result: dict[str, Any]) -> dict[str, Any]:
        if not self.configured():
            return {"enabled": False, "summary": "", "error": "未配置 DeepSeek API Key"}

        compact_payload = {
            "patient": result.get("patient", {}),
            "risk": result.get("risk", {}),
            "clinical_synthesis": result.get("clinical_synthesis", {}),
            "abnormal_items": [item for item in result.get("explanations", []) if item.get("status") != "normal"],
            "rag_evidence": result.get("rag_evidence", [])[:6],
            "followup": result.get("followup", {}),
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "你是医学报告解读助手，只能基于给定结构化数据解释风险和复查方向。"
                    "不要给诊断结论，不要给处方剂量，不要要求患者自行用药。"
                    "可以列出需要与医生讨论的药物类别或处理方向。"
                    "必须用中文，输出 JSON，不要 Markdown。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请将以下报告评估转成患者能理解的综合说明，JSON字段为："
                    "patient_summary, combined_reasoning, next_steps, medication_discussion, questions_for_doctor, safety_note。\n"
                    + json.dumps(compact_payload, ensure_ascii=False)
                ),
            },
        ]
        response = self.chat(messages, temperature=0.15, max_tokens=1000)
        if not response.get("ok"):
            return {"enabled": True, "summary": "", "error": response.get("error", "DeepSeek 调用失败")}
        content = response.get("content", "")
        parsed = parse_json_object(content)
        return {
            "enabled": True,
            "provider": "deepseek",
            "model": self.model,
            "summary": parsed or {"patient_summary": content},
            "error": "",
        }

    def patient_chat(self, message: str, patient: dict[str, Any], profile: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
        if not self.configured():
            return {"enabled": False, "reply": fallback["reply"], "error": "未配置 DeepSeek API Key"}
        messages = [
            {
                "role": "system",
                "content": (
                    "你是体检报告随访助手。回答要简洁、自然、像真实产品的医疗助手。"
                    "若缺少报告数据或病史，要追问；不能直接诊断或开处方。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"message": message, "patient": patient, "profile": profile, "rule_fallback": fallback},
                    ensure_ascii=False,
                ),
            },
        ]
        response = self.chat(messages, temperature=0.25, max_tokens=550)
        if not response.get("ok"):
            return {"enabled": True, "reply": fallback["reply"], "error": response.get("error", "DeepSeek 调用失败")}
        return {"enabled": True, "reply": response.get("content", "").strip() or fallback["reply"], "error": ""}


def parse_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    match = re.search(r"\{.*\}", cleaned, re.S)
    candidate = match.group(0) if match else cleaned
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


class LabReportAgent:
    def __init__(self) -> None:
        self.parser = ReportParserAgent(KNOWLEDGE)
        self.risk_agent = RiskStratificationAgent(KNOWLEDGE)
        self.explainer = ExplanationAgent()
        self.synthesis_agent = ComprehensiveAssessmentAgent()
        self.followup_agent = FollowUpAgent()
        self.llm = DeepSeekClient()

    def analyze(self, report_text: str, patient: dict[str, Any], user_profile: dict[str, Any] | None = None) -> dict[str, Any]:
        parsed = self.parser.parse(report_text, patient)
        risk = self.risk_agent.stratify(parsed)
        clinical_synthesis = self.synthesis_agent.build(parsed, risk, patient, user_profile)
        self._apply_synthesis_adjustment(risk, clinical_synthesis)
        explanations = self.explainer.explain(risk)
        followup = self.followup_agent.plan(risk, patient)
        codes = [item["code"] for item in parsed.get("items", [])]
        rag_evidence = build_rag_evidence(report_text, codes=codes, limit=8)
        agent_trace = [
            {"node": "user_profile", "status": "ok", "summary": "读取并合并个人画像，用于风险提示和追问。"},
            {"node": "normalize_metrics", "status": "ok", "summary": f"识别到 {len(parsed.get('items', []))} 个可结构化指标。"},
            {"node": "retrieve_evidence", "status": "ok", "summary": f"检索到 {len(rag_evidence)} 条医学标准/RAG 证据。"},
            {"node": "clinical_synthesis", "status": "ok", "summary": clinical_synthesis["summary"]},
            {"node": "risk_stratify", "status": risk["risk_level"], "summary": f"综合风险分 {risk['risk_score']}，需复核：{risk['requires_review']}。"},
            {"node": "followup_plan", "status": followup["priority"], "summary": f"生成随访单 {followup['follow_id']}，截止 {followup['due_date']}。"},
        ]
        task_id = short_id("CASE")
        created_at = now_iso()
        review_task = None

        result = {
            "task_id": task_id,
            "created_at": created_at,
            "patient": patient,
            "parsed": parsed,
            "risk": {k: v for k, v in risk.items() if k != "items"},
            "explanations": explanations,
            "followup": followup,
            "review_task": None,
            "user_profile": user_profile or {},
            "clinical_synthesis": clinical_synthesis,
            "rag_evidence": rag_evidence,
            "knowledge_graph": KNOWLEDGE_GRAPH_EDGES,
            "agent_workflow": AGENT_WORKFLOW,
            "agent_trace": agent_trace,
            "ai_enhancement": {"enabled": False, "summary": "", "error": "pending"},
            "safety_notice": "本系统仅用于课程设计中的报告解释与复诊随访辅助，不构成诊断、处方或治疗建议。",
        }

        ai_enhancement = self.llm.enhance_analysis(result)
        result["ai_enhancement"] = ai_enhancement
        agent_trace.append(
            {
                "node": "deepseek_enhance",
                "status": "ok" if ai_enhancement.get("enabled") and not ai_enhancement.get("error") else "fallback",
                "summary": "DeepSeek 已生成患者化解释。" if ai_enhancement.get("enabled") and not ai_enhancement.get("error") else "使用本地规则解释。",
            }
        )

        if risk["requires_review"]:
            agent_trace.append({"node": "doctor_review", "status": "pending", "summary": "高风险、红旗或低置信度结果进入医生端人工复核。"})
            review_task = self._build_review_task(task_id, created_at, patient, parsed, explanations, risk, followup, clinical_synthesis)
            reviews = read_state(REVIEWS_PATH)
            reviews.insert(0, review_task)
            save_json(REVIEWS_PATH, reviews[:50])
            result["review_task"] = {
                "review_id": review_task["review_id"],
                "case_no": review_task["case_no"],
                "status": review_task["status"],
                "reason": review_task["review_reason"],
            }

        followups = read_state(FOLLOWUPS_PATH)
        followups.insert(0, {**followup, "task_id": task_id, "created_at": created_at, "review_id": review_task["review_id"] if review_task else None})
        save_json(FOLLOWUPS_PATH, followups[:50])
        return result

    @staticmethod
    def _apply_synthesis_adjustment(risk: dict[str, Any], synthesis: dict[str, Any]) -> None:
        adjustment = int(synthesis.get("risk_adjustment") or 0)
        risk["risk_score"] = int(risk.get("risk_score") or 0) + adjustment
        risk["combined_patterns"] = synthesis.get("clusters", [])
        if risk.get("risk_level") != "red":
            if risk["risk_score"] >= 7:
                risk["risk_level"] = "high"
            elif risk["risk_score"] >= 3:
                risk["risk_level"] = "medium"
            else:
                risk["risk_level"] = "low"
        if risk["risk_level"] in {"high", "red"} or adjustment >= 2:
            risk["requires_review"] = True

    def _build_review_task(
        self,
        task_id: str,
        created_at: str,
        patient: dict[str, Any],
        parsed: dict[str, Any],
        explanations: list[dict[str, Any]],
        risk: dict[str, Any],
        followup: dict[str, Any],
        clinical_synthesis: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        abnormal = [e for e in explanations if e["status"] != "normal"]
        review_reason = "红旗风险需立即复核" if risk["risk_level"] == "red" else "高风险或低置信度结果需医生复核"
        return {
            "review_id": short_id("REV"),
            "task_id": task_id,
            "case_no": "病历复核-" + datetime.now().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:4].upper(),
            "patient": {
                "name": patient.get("name", "模拟患者"),
                "age": patient.get("age", ""),
                "sex": patient.get("sex", ""),
                "symptoms": patient.get("symptoms", ""),
            },
            "risk_level": risk["risk_level"],
            "risk_score": risk["risk_score"],
            "red_flags": risk["red_flags"],
            "status": "待医生复核",
            "created_at": created_at,
            "review_reason": review_reason,
            "summary": self._doctor_summary(explanations, risk),
            "patient_context": {
                "symptoms": patient.get("symptoms", ""),
                "raw_text": parsed.get("raw_text", ""),
                "unparsed_lines": parsed.get("unparsed_lines", []),
            },
            "clinical_synthesis": clinical_synthesis or {},
            "disease_assist": {
                "title": (clinical_synthesis or {}).get("summary", "待结合指标综合判断"),
                "clusters": (clinical_synthesis or {}).get("clusters", []),
                "recommendations": (clinical_synthesis or {}).get("recommendations", []),
                "medication_discussion": (clinical_synthesis or {}).get("medication_discussion", []),
                "disclaimer": (clinical_synthesis or {}).get("disclaimer", "辅助判断不等同于诊断。"),
            },
            "all_items": [
                {
                    "code": e["code"],
                    "name": e["name"],
                    "value": e["value"],
                    "unit": e["unit"],
                    "reference": e["reference"],
                    "status": e["status"],
                    "severity": e["severity"],
                    "message": e["message"],
                    "source": e["source"],
                    "standard": e.get("standard"),
                }
                for e in explanations
            ],
            "abnormal_items": [
                {
                    "code": e["code"],
                    "name": e["name"],
                    "value": e["value"],
                    "unit": e["unit"],
                    "status": e["status"],
                    "severity": e["severity"],
                    "message": e["message"],
                }
                for e in abnormal
            ],
            "proposed_followup": followup,
            "doctor_review": {
                "risk_level": risk["risk_level"],
                "decision": "需要复核",
                "due_date": followup["due_date"],
                "tests": self._default_tests(abnormal),
                "opinion": "",
                "patient_message": "",
            },
        }

    @staticmethod
    def _doctor_summary(explanations: list[dict[str, Any]], risk: dict[str, Any]) -> str:
        abnormal = [e for e in explanations if e["status"] != "normal"]
        parts = [f"{e['name']} {e['value']}{e['unit']}({e['status']})" for e in abnormal[:5]]
        if risk["red_flags"]:
            parts.extend(risk["red_flags"])
        return "；".join(parts) or "未见明显异常指标。"

    @staticmethod
    def _default_tests(abnormal: list[dict[str, Any]]) -> str:
        codes = {item["code"] for item in abnormal}
        tests = []
        if {"ALT", "AST", "TBIL"} & codes:
            tests.append("肝功能复查")
        if {"FPG", "HBA1C"} & codes:
            tests.append("空腹血糖/HbA1c复查")
        if {"HGB", "PLT", "WBC", "NEUT"} & codes:
            tests.append("血常规复查")
        if "CREA" in codes:
            tests.append("肾功能复查")
        return "、".join(tests) or "按体检科建议复查"


AGENT = LabReportAgent()
OCR = OcrService()


def rows_from_report_text(text: str) -> list[dict[str, Any]]:
    parsed = AGENT.parser.parse(text, {})
    rows = []
    by_code = {item["code"]: item for item in KNOWLEDGE}
    for obs in parsed["items"]:
        item = obs.get("knowledge") or by_code.get(obs["code"], {})
        value = obs["value"]
        status = "unknown"
        low = item.get("reference_min")
        high = item.get("reference_max")
        if isinstance(low, (int, float)) and value < low:
            status = "low"
        elif isinstance(high, (int, float)) and value > high:
            status = "high"
        elif isinstance(low, (int, float)) or isinstance(high, (int, float)):
            status = "normal"
        reference = item.get("reference_text") or (
            f"{item.get('reference_min')} - {item.get('reference_max')} {item.get('unit', obs.get('unit', ''))}"
            if item.get("reference_min") is not None or item.get("reference_max") is not None
            else ""
        )
        display_name = obs["name"] if obs.get("dynamic") else f"{obs['code']} {obs['name']}"
        rows.append(
            {
                "code": obs["code"],
                "name": display_name,
                "value": value,
                "unit": item.get("unit", obs.get("unit", "")),
                "reference": reference,
                "abnormal": status,
                "dynamic": bool(obs.get("dynamic")),
            }
        )
    return rows


def update_followup_from_review(review: dict[str, Any], body: dict[str, Any]) -> None:
    followups = read_state(FOLLOWUPS_PATH)
    changed = False
    for item in followups:
        if item.get("task_id") == review.get("task_id"):
            item["title"] = body.get("followupTitle") or item.get("title")
            item["due_date"] = body.get("dueDate") or item.get("due_date")
            item["status"] = "医生已复核"
            item["priority"] = body.get("riskLevel") or item.get("priority")
            message = body.get("patientMessage")
            tests = body.get("tests")
            item["doctor_review"] = {
                "risk_level": body.get("riskLevel") or review.get("risk_level"),
                "decision": body.get("decision", "医生已复核"),
                "opinion": body.get("opinion", ""),
                "patient_message": message or "",
                "tests": tests or "",
                "reviewer": body.get("reviewer", "医生端"),
                "reviewed_at": now_iso(),
            }
            if message or tests:
                item["questions"] = [
                    q
                    for q in [
                        f"请确认是否已按医生建议完成：{tests}" if tests else "",
                        message or "",
                        "是否出现新的不适症状？",
                    ]
                    if q
                ]
            item["doctor_reviewed"] = True
            item["updated_at"] = now_iso()
            changed = True
    if changed:
        save_json(FOLLOWUPS_PATH, followups)


def active_reviews(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    closed_words = ("已提交", "已复核", "已完成", "已关闭")
    return [item for item in items if not any(word in str(item.get("status", "")) for word in closed_words)]


def build_rag_evidence(query: str, codes: list[str] | None = None, limit: int = 8) -> list[dict[str, Any]]:
    db_docs = STORE.search_knowledge(query, codes=codes, limit=limit)
    static_docs = search_rag(query, codes=codes, limit=limit)
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for doc in [*db_docs, *static_docs]:
        doc_id = str(doc.get("doc_id") or doc.get("id") or doc.get("source_id") or doc.get("title"))
        if doc_id in seen:
            continue
        seen.add(doc_id)
        merged.append(doc)
        if len(merged) >= limit:
            break
    return merged


def pubmed_search(query: str, limit: int = 5) -> dict[str, Any]:
    query = compact_text(query)
    if not query:
        return {"query": query, "results": [], "error": "query is empty"}
    limit = max(1, min(int(limit or 5), 10))
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    params = urlencode({"db": "pubmed", "term": query, "retmode": "json", "retmax": limit, "sort": "relevance"})
    try:
        search_data = fetch_json_url(base + "esearch.fcgi?" + params, timeout=12)
        ids = search_data.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return {"query": query, "results": [], "source": "PubMed E-utilities"}
        summary_params = urlencode({"db": "pubmed", "id": ",".join(ids), "retmode": "json"})
        summary_data = fetch_json_url(base + "esummary.fcgi?" + summary_params, timeout=12)
        result_obj = summary_data.get("result", {})
        results = []
        for pmid in result_obj.get("uids", []):
            item = result_obj.get(pmid, {})
            results.append(
                {
                    "pmid": pmid,
                    "title": item.get("title", ""),
                    "source": item.get("source", ""),
                    "pubdate": item.get("pubdate", ""),
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    "authors": [author.get("name", "") for author in item.get("authors", [])[:4]],
                }
            )
        return {"query": query, "results": results, "source": "PubMed E-utilities", "error": ""}
    except Exception as exc:  # noqa: BLE001
        return {"query": query, "results": [], "source": "PubMed E-utilities", "error": str(exc)}


def fetch_json_url(url: str, timeout: int = 12) -> dict[str, Any]:
    last_error: Exception | None = None
    headers = {
        "User-Agent": "medical-report-agent-course-design/1.0 (student prototype)",
        "Accept": "application/json",
    }
    for _ in range(2):
        try:
            request = Request(url, headers=headers, method="GET")
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - public metadata endpoints only
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise last_error or RuntimeError("request failed")


NEGATION_WORDS = ("没有", "无", "暂无", "否认", "没", "未")


def merge_profile_from_message(profile: dict[str, Any], message: str) -> tuple[dict[str, Any], list[str]]:
    """Extract simple patient-profile facts from chat and persist them."""
    text = compact_text(message)
    updated = dict(profile or {})
    changed: list[str] = []

    def mark(key: str, value: str, label: str) -> None:
        if value and value != updated.get(key):
            updated[key] = value
            changed.append(label)

    if _negates_any(text, ["过敏史", "过敏", "药物过敏"]):
        mark("allergies", "无", "过敏史")
    elif "过敏" in text:
        match = re.search(r"对([^，。,；;\n]{1,30})过敏", text)
        if match:
            mark("allergies", match.group(1).strip(), "过敏史")

    if _negates_any(text, ["用药史", "用药", "服药", "吃药", "药物"]):
        mark("medications", "无", "近期用药")
    else:
        match = re.search(r"(?:正在|目前|最近|长期|在)?(?:服用|吃|使用)([^，。,；;\n]{1,40})", text)
        if match:
            mark("medications", match.group(1).strip(), "近期用药")

    if _negates_any(text, ["慢病", "基础病", "既往病史", "病史"]):
        mark("chronic_diseases", "无", "既往慢病")
    else:
        diseases = [name for name in ["高血压", "糖尿病", "脂肪肝", "肾病", "肝炎", "冠心病", "贫血"] if name in text]
        if diseases:
            existing = updated.get("chronic_diseases", "")
            merged = "、".join(dict.fromkeys([*(existing.split("、") if existing and existing != "无" else []), *diseases]))
            mark("chronic_diseases", merged, "既往慢病")

    if _negates_any(text, ["怀孕", "妊娠", "备孕"]):
        mark("pregnancy_status", "无", "妊娠/备孕状态")
    elif any(term in text for term in ["怀孕", "妊娠", "备孕"]):
        mark("pregnancy_status", text[:40], "妊娠/备孕状态")

    return updated, changed


def _negates_any(text: str, terms: list[str]) -> bool:
    if not text:
        return False
    for term in terms:
        idx = text.find(term)
        if idx < 0:
            continue
        prefix = text[max(0, idx - 12):idx]
        if any(word in prefix for word in NEGATION_WORDS):
            return True
        if re.search(rf"(?:没有|暂无|否认|无|没|未).{{0,12}}{re.escape(term)}", text):
            return True
    return False


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def do_GET(self) -> None:  # noqa: N802
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)
        if path == "/api/users":
            return self.json_response(STORE.list_users())
        if path == "/api/standards":
            return self.json_response(
                {
                    "standards": REFERENCE_STANDARDS,
                    "knowledge_graph": KNOWLEDGE_GRAPH_EDGES,
                    "rag_corpus": RAG_CORPUS,
                    "knowledge_documents": STORE.knowledge_documents(limit=200),
                }
            )
        if path == "/api/knowledge/search":
            q = (query.get("q") or [""])[0]
            codes = (query.get("codes") or [""])[0].split(",") if query.get("codes") else []
            categories = (query.get("categories") or [""])[0].split(",") if query.get("categories") else []
            return self.json_response({"query": q, "codes": codes, "results": STORE.search_knowledge(q, codes=codes, categories=categories)})
        if path == "/api/knowledge/documents":
            return self.json_response({"documents": STORE.knowledge_documents(limit=300)})
        if path == "/api/knowledge/pubmed":
            q = (query.get("q") or [""])[0]
            limit = int((query.get("limit") or ["5"])[0] or 5)
            return self.json_response(pubmed_search(q, limit=limit))
        if path == "/api/agent/workflow":
            return self.json_response(AGENT_WORKFLOW)
        if path == "/api/agent/conversation":
            user_id = (query.get("userId") or [""])[0] or None
            return self.json_response(STORE.conversation(user_id))
        if path == "/api/deepseek/status":
            return self.json_response(AGENT.llm.status())
        if path == "/api/health":
            return self.json_response({"ok": True, "time": now_iso()})
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if path == "/api/ocr/status":
            return self.json_response(OCR.status())
        if path == "/api/sample-reports":
            return self.json_response(SAMPLES)
        if path == "/api/datasets":
            return self.json_response(DATASETS)
        if path == "/api/reviews":
            reviews = read_state(REVIEWS_PATH)
            include_closed = (query.get("all") or [""])[0].lower() in {"1", "true", "yes"}
            return self.json_response(reviews if include_closed else active_reviews(reviews))
        if path == "/api/reviews/detail":
            review_id = (query.get("reviewId") or query.get("taskId") or [""])[0]
            for item in read_state(REVIEWS_PATH):
                if review_id in {item.get("review_id"), item.get("task_id")}:
                    return self.json_response(item)
            return self.json_response({"error": "未找到审核任务"}, status=404)
        if path == "/api/followups":
            return self.json_response(enriched_followups())
        if path == "/api/audit":
            events = STORE.audit_events()
            return self.json_response(events or read_state(AUDIT_PATH))
        if path == "/api/history":
            patient_name = (query.get("patient") or [""])[0]
            user_id = (query.get("userId") or [""])[0]
            db_history = STORE.history(user_id=user_id or None, patient_name=patient_name or None)
            if db_history:
                return self.json_response(db_history)
            history = read_state(HISTORY_PATH)
            if patient_name:
                history = [item for item in history if item.get("patient", {}).get("name") == patient_name]
            return self.json_response(history[:50])
        if path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        body = self.read_body()
        if path == "/api/users/upsert":
            user = STORE.upsert_user(body.get("patient") or body, body.get("profile"))
            STORE.add_audit(user.get("name", "用户"), "更新用户画像", user.get("user_id", ""), "profile")
            return self.json_response(user)
        if path == "/api/knowledge/documents":
            doc = STORE.add_knowledge_document(body)
            STORE.add_audit(body.get("organization", "知识库"), "新增医学知识文档", doc.get("doc_id", ""), body.get("title", ""))
            return self.json_response(doc)
        if path == "/api/agent/chat":
            patient = body.get("patient") or {}
            profile = body.get("userProfile") or body.get("profile") or {}
            message = body.get("message", "")
            merged_profile, profile_updates = merge_profile_from_message(profile, message)
            user = STORE.upsert_user(patient, merged_profile)
            STORE.add_conversation_turn(user["user_id"], "user", message, {"source": "patient_console"})
            decision = triage_patient_message(message, merged_profile)
            llm_reply = AGENT.llm.patient_chat(
                message,
                patient,
                merged_profile,
                {
                    "reply": decision.reply,
                    "intent": decision.intent,
                    "missing_fields": decision.missing_fields,
                    "next_action": decision.next_action,
                },
            )
            reply = {
                "intent": decision.intent,
                "reply": llm_reply.get("reply") or decision.reply,
                "missing_fields": decision.missing_fields,
                "next_action": decision.next_action,
                "workflow": AGENT_WORKFLOW,
                "ai_enhancement": llm_reply,
                "profile": merged_profile,
                "profile_updates": profile_updates,
            }
            STORE.add_conversation_turn(user["user_id"], "assistant", reply["reply"], reply)
            return self.json_response({"user": user, **reply, "conversation": STORE.conversation(user["user_id"])})
        if path == "/api/analyze":
            report_text = body.get("reportText", "")
            if body.get("sampleId"):
                sample = next((s for s in SAMPLES if s["id"] == body["sampleId"]), None)
                if sample:
                    report_text = sample["report_text"]
            patient = body.get("patient") or {}
            profile = body.get("userProfile") or body.get("profile") or {}
            if not compact_text(report_text):
                return self.json_response({"error": "报告文本不能为空"}, status=400)
            user = STORE.upsert_user(patient, profile)
            patient["user_id"] = user["user_id"]
            result = AGENT.analyze(report_text, patient, profile)
            add_history(result)
            STORE.record_analysis(result, user_id=user["user_id"], source_type="table_or_ocr")
            STORE.add_audit(patient.get("name") or "患者端", "生成报告解读", result["task_id"], result["risk"]["risk_level"])
            append_audit(patient.get("name") or "患者端", "生成报告解读", result["task_id"], result["risk"]["risk_level"])
            return self.json_response(result)
        if path == "/api/ocr":
            sample = next((s for s in SAMPLES if s["id"] == body.get("sampleId")), SAMPLES[0])
            patient = body.get("patient") or {}
            try:
                result = OCR.recognize(
                    body.get("imageData", ""),
                    body.get("imageName") or "uploaded-report-image",
                    sample,
                    patient,
                )
            except Exception as exc:  # noqa: BLE001
                result = {
                    "text": "",
                    "rows": [],
                    "patient": patient,
                    "engine": "ocr_error",
                    "engine_status": OCR.status(),
                    "confidence": None,
                    "image_name": body.get("imageName") or "uploaded-report-image",
                    "image_bytes": len(decode_data_url(body.get("imageData", ""))) if body.get("imageData") else 0,
                    "message": "OCR识别异常，请按页面提示手动校对或补录报告文字。",
                    "errors": [str(exc)],
                    "needs_manual_review": True,
                    "sample_used": False,
                }
            append_audit(patient.get("name") or "患者端", "OCR识别", result["image_name"], result["engine"])
            return self.json_response(result)
        if path == "/api/parse-text":
            text = body.get("text", "")
            return self.json_response({"text": text, "rows": rows_from_report_text(text)})
        if path == "/api/reset":
            save_json(REVIEWS_PATH, [])
            save_json(FOLLOWUPS_PATH, [])
            save_json(AUDIT_PATH, [])
            save_json(HISTORY_PATH, [])
            STORE.clear_runtime_data()
            return self.json_response({"ok": True, "time": now_iso()})
        if path == "/api/followups/feedback":
            followups = read_state(FOLLOWUPS_PATH)
            key = body.get("followId") or body.get("taskId")
            for item in followups:
                if key in {item.get("follow_id"), item.get("task_id")}:
                    item["status"] = "已反馈"
                    item["feedback"] = {
                        "completed": bool(body.get("completed")),
                        "new_symptoms": body.get("newSymptoms", ""),
                        "note": body.get("note", ""),
                        "answers": body.get("answers", []),
                        "submitted_at": now_iso(),
                    }
                    item["updated_at"] = now_iso()
                    save_json(FOLLOWUPS_PATH, followups)
                    append_audit(item.get("owner", "患者端"), "提交随访反馈", item.get("follow_id", ""), item["status"])
                    return self.json_response(item)
            return self.json_response({"error": "未找到随访任务"}, status=404)
        if path == "/api/reviews/decision":
            reviews = read_state(REVIEWS_PATH)
            key = body.get("reviewId") or body.get("taskId")
            for item in reviews:
                if key in {item.get("review_id"), item.get("task_id")}:
                    doctor_review = {
                        "risk_level": body.get("riskLevel", item.get("risk_level")),
                        "decision": body.get("decision", "提交复核意见"),
                        "due_date": body.get("dueDate") or item.get("doctor_review", {}).get("due_date"),
                        "tests": body.get("tests", ""),
                        "opinion": body.get("opinion", ""),
                        "patient_message": body.get("patientMessage", ""),
                        "reviewer": body.get("reviewer", "课程设计医生端"),
                        "updated_at": now_iso(),
                    }
                    item["status"] = "已保存草稿" if body.get("draft") else "已复核"
                    item["risk_level"] = doctor_review["risk_level"]
                    item["doctor_review"] = doctor_review
                    item["updated_at"] = doctor_review["updated_at"]
                    save_json(REVIEWS_PATH, reviews)
                    update_followup_from_review(item, body)
                    append_audit(doctor_review["reviewer"], "提交医生复核" if not body.get("draft") else "保存医生草稿", item.get("review_id", ""), item["status"])
                    return self.json_response(item)
            return self.json_response({"error": "未找到审核任务"}, status=404)
        return self.json_response({"error": "Not found"}, status=404)

    def read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        return json.loads(raw or "{}")

    def json_response(self, value: Any, status: int = 200) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    DATA.mkdir(exist_ok=True)
    STORE.init_db()
    for path in [REVIEWS_PATH, FOLLOWUPS_PATH, AUDIT_PATH, HISTORY_PATH]:
        if not path.exists():
            save_json(path, [])
    default_host = "0.0.0.0" if "PORT" in os.environ else "127.0.0.1"
    host = os.environ.get("HOST", default_host)
    port = int(os.environ.get("PORT", "8787"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Lab report agent prototype: http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()

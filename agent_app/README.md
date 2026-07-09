# 检验/体检报告解读与复诊随访智能体原型

这是课程设计用的可运行原型，覆盖患者端报告录入、图片 OCR、结构化指标解析、风险分层、医生复核、复诊随访和数据依据展示。

## 运行

```powershell
cd C:\Users\26055\Documents\智医工课设\agent_app
python -m pip install -r requirements.txt
python app.py
```

浏览器打开：

```text
http://127.0.0.1:8787
```

## 主要功能

- 患者端：上传报告图片或 PDF、OCR 识别、结构化表格确认、生成报告解读、提交随访反馈和查看历史趋势。
- OCR/PDF：图片优先调用本机 `rapidocr-onnxruntime`，并对原图、增强灰度图、二值化图多路识别后取最佳结果；PDF 优先用 `pypdf` 提取真实文本层，扫描 PDF 用 `PyMuPDF` 渲染页面后再 OCR；识别后提供原文校对区，用户可修正文本并重建结构化表格。
- 智能体：解析常见检验指标，匹配参考范围，生成解释，识别红旗风险和需医生复核场景。
- 医生端：查看审核单、患者指标详情、历史趋势、随访反馈和逾期提醒，调整风险等级、处理结论、复查截止日期、建议复查项目、审核意见和给患者的说明。
- 随访：根据风险等级生成随访单；患者可提交反馈；医生提交复核结论后，可同步调整随访展示内容；逾期未反馈任务会在患者端和医生端突出显示。
- 资料抽屉：右上角齿轮中展示 WHO 治理指南、HealthBench、PubMedQA、MedMCQA、MedQA、AgentClinic、MIMIC-IV、LOINC 等入口，以及 UML 数据流图。
- 审计：记录 OCR、报告解读、医生复核和状态清理等关键事件，供课程原型演示。

## 自测

```powershell
cd C:\Users\26055\Documents\智医工课设
python agent_app\tests\run_smoke_tests.py
node --check agent_app\static\app.js
```

## 数据说明

项目内置两类可直接运行的数据：

- `data/sample_reports.json`：课程设计合成报告样例，不含真实患者隐私。
- `data/knowledge_base.json`：课程原型用指标解释、参考范围和红旗规则。

外部真实数据集没有直接打包进项目，原因是部分数据集体量大、需要授权或有使用协议。例如 MIMIC-IV 需要 PhysioNet 认证和数据使用协议。项目通过 `data/datasets_catalog.json` 提供数据入口、适用场景和接入说明。

## 医学边界

本原型仅用于课程设计、报告理解和复诊随访流程演示，不构成诊断、处方或治疗建议。红旗风险、严重异常、特殊人群和低置信度输出应进入医生复核。

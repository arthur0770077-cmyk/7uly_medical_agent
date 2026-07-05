# 检验体检报告解读与复诊随访智能体重构方案

## 1. 优先级调整

当前版本暂缓外部大模型 API 接入。系统默认采用：

- 本地 OCR/PDF 解析
- 结构化指标解析
- 统一医学标准与红旗规则
- 轻量 RAG 检索
- 医生端人工复核
- 关系型数据库持久化

外部 API 被设计为后续可插拔增强项，用于自然语言生成、复杂追问和医生意见草稿，不作为核心流程的必要依赖。

## 2. 用户数据管理

新增关系型数据库层：`agent_app/relational_store.py`。

默认运行数据库：

```text
agent_app/data/agent_store.sqlite3
```

生产迁移 DDL：

```text
agent_app/schema_mysql.sql
```

核心表：

- `users`：独立个人用户
- `user_profiles`：慢病史、用药史、过敏史、妊娠/特殊状态、生活方式
- `report_cases`：每次报告分析任务
- `observations`：结构化检验指标
- `review_tasks`：医生复核任务
- `followups`：复诊随访单
- `audit_events`：审计日志
- `conversation_turns`：患者端追问与智能体回复

## 3. 统一医学/数据评判标准

新增标准层：`agent_app/standards.py`。

系统不再只说“指标高/低”，而是把每个指标映射到标准来源和解释边界：

- CLSI EP28：实验室参考区间建立与验证原则
- ADA Standards of Care：空腹血糖、HbA1c 等糖代谢阈值
- KDIGO CKD 指南：肌酐/eGFR/肾功能解释边界
- AASLD/肝病学资料：ALT、AST、胆红素组合解释
- 血常规危急值制度：HGB、PLT、WBC 等红旗风险

重要边界：检验指标参考区间应优先采用原报告所在实验室给出的范围，内置范围只用于课程演示和缺失参考区间时的辅助解释。

## 4. 模型专项训练与 RAG

当前阶段采用 RAG 优先，不做微调。原因是：

- 医学微调需要高质量、授权明确、脱敏的数据集
- 课程设计场景不适合把未授权患者数据用于训练
- RAG 更容易审计和更新标准来源

已落地：

- `/api/knowledge/search`：医学知识检索
- `rag_evidence`：每次分析返回证据片段
- `knowledge_graph`：指标之间的知识图谱边

后续可接入的数据来源：

- MIMIC-IV：需 PhysioNet 认证和数据使用协议
- PubMedQA / MedQA / MedMCQA：医学问答训练与评测
- HealthBench：医疗问答与安全评测
- LOINC：检验项目标准编码映射

建议训练路线：

1. 先做 RAG：标准文件、指南摘要、指标解释模板、复核规则入库
2. 再做评测集：构造报告解释、红旗识别、随访建议测试样例
3. 最后做轻量微调：只微调话术与结构化输出，不微调医学事实

## 5. 前后端架构与 API

新增/规范化 API：

```text
GET  /api/users
POST /api/users/upsert
GET  /api/standards
GET  /api/knowledge/search?q=ALT
GET  /api/agent/workflow
GET  /api/agent/conversation?userId=...
POST /api/agent/chat
POST /api/analyze
POST /api/ocr
POST /api/parse-text
GET  /api/history?patient=...
GET  /api/audit
GET  /api/reviews
POST /api/reviews/decision
GET  /api/followups
POST /api/followups/feedback
```

前端新增：

- 用户画像区
- 智能体追问区
- 执行链展示
- 医学依据/RAG 展示

## 6. Skill 深度整合方式

参考 `mastering-langgraph` skill，系统按工作流节点组织：

```text
user_profile
  -> ocr_ingest
  -> normalize_metrics
  -> retrieve_evidence
  -> risk_stratify
  -> patient_explain
  -> doctor_review
  -> followup_plan
  -> persist_audit
```

实现原则：

- State 保留原始数据，不只保存格式化提示词
- 每个节点单一职责
- 风险分层和医生复核通过显式路由触发
- 用户画像、历史趋势、对话记录持久化
- 医疗高风险路径必须有人类审核

## 7. OCR 改善方案

已实现：

- PDF 优先抽取真实文本层
- 扫描 PDF 使用 PyMuPDF 渲染后再 OCR
- 图片 OCR 进行 EXIF 方向修正、高清放大、对比度增强、CLAHE、自适应二值化、倾斜校正
- RapidOCR 结果解析适配多种返回结构
- 可选 PaddleOCR：本地安装后自动优先使用
- 识别结果必须进入人工校对区，再进入结构化分析

建议进一步提升：

- 对报告图片引导用户“平铺、无反光、满页拍摄”
- 上传 PDF 原件优先于手机截图
- 本地安装 PaddleOCR 做中文表格识别
- 后续可加入版面检测模型，将表格行列结构和 OCR 文本分离处理

## 关键资料入口

- CLSI EP28: https://clsi.org/standards/products/method-evaluation/documents/ep28/
- ADA Standards of Care: https://diabetesjournals.org/care/issue
- KDIGO CKD Guideline: https://kdigo.org/guidelines/ckd-evaluation-and-management/
- PaddleOCR: https://github.com/PaddlePaddle/PaddleOCR
- RapidOCR: https://github.com/RapidAI/RapidOCR
- WHO AI for health publications: https://www.who.int/health-topics/artificial-intelligence

const state = {
  samples: [],
  history: [],
  currentUser: null,
  profile: {},
  doctor: null,
  evidenceStore: {},
  speechRecognition: null,
  activePatientView: "chat",
  activeDoctorView: "queue",
};

const MANUAL_METRICS = [
  { code: "ALT", name: "丙氨酸氨基转移酶", unit: "U/L" },
  { code: "AST", name: "天门冬氨酸氨基转移酶", unit: "U/L" },
  { code: "TBIL", name: "总胆红素", unit: "umol/L" },
  { code: "FPG", name: "空腹血糖", unit: "mmol/L" },
  { code: "HBA1C", name: "糖化血红蛋白", unit: "%" },
  { code: "WBC", name: "白细胞计数", unit: "10^9/L" },
  { code: "HGB", name: "血红蛋白", unit: "g/L" },
  { code: "PLT", name: "血小板计数", unit: "10^9/L" },
  { code: "CREA", name: "肌酐", unit: "umol/L" },
  { code: "PROG", name: "血清孕酮", unit: "ng/ml" },
  { code: "PRL", name: "泌乳素", unit: "ng/ml" },
  { code: "FSH", name: "卵泡刺激素", unit: "mIU/ml" },
  { code: "LH", name: "促黄体生成素", unit: "mIU/ml" },
  { code: "TESTO", name: "睾酮", unit: "nmol/L" },
  { code: "E2", name: "雌二醇", unit: "pg/ml" },
];

const CUSTOM_METRIC_ROWS = 4;

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function showToast(message) {
  $("toast").textContent = message;
  $("toast").classList.remove("hidden");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => $("toast").classList.add("hidden"), 2200);
}

function showScreen(screenId) {
  ["portal-screen", "patient-login-screen", "doctor-login-screen", "patient-shell", "doctor-shell"].forEach((id) => {
    $(id).classList.toggle("hidden", id !== screenId);
  });
  closeDrawers();
}

function patientFromRegistration(useDemo = false) {
  if (useDemo) {
    return {
      patient: {
        name: "王同学",
        age: 45,
        sex: "男",
        symptoms: "近期熬夜，偶有乏力，无胸痛、呼吸困难。",
      },
      profile: {
        chronic_diseases: "脂肪肝风险，糖代谢异常待复查",
        medications: "",
        allergies: "",
        pregnancy_status: "无",
      },
    };
  }
  return {
    patient: {
      name: $("reg-name").value.trim(),
      age: Number($("reg-age").value || 0),
      sex: $("reg-sex").value,
      symptoms: $("reg-symptoms").value.trim(),
    },
    profile: {
      chronic_diseases: $("reg-chronic").value.trim(),
      medications: $("reg-meds").value.trim(),
      allergies: "",
      pregnancy_status: "",
    },
  };
}

function currentPatient() {
  return {
    user_id: state.currentUser?.user_id || "",
    name: $("profile-name").value.trim() || state.currentUser?.name || "",
    age: Number($("profile-age").value || state.currentUser?.age || 0),
    sex: $("profile-sex").value || state.currentUser?.sex || "男",
    symptoms: $("profile-symptoms").value.trim(),
  };
}

function currentProfile() {
  return {
    chronic_diseases: $("profile-chronic").value.trim(),
    medications: $("profile-meds").value.trim(),
    allergies: $("profile-allergies").value.trim(),
    pregnancy_status: $("profile-pregnancy").value.trim(),
  };
}

function fillProfile(user, profile = {}) {
  state.currentUser = user;
  state.profile = profile;
  $("side-user-name").textContent = user?.name || "未建档";
  $("profile-name").value = user?.name || "";
  $("profile-age").value = user?.age || "";
  $("profile-sex").value = user?.sex || "男";
  $("profile-symptoms").value = user?.symptoms || "";
  $("profile-chronic").value = profile.chronic_diseases || profile.chronicDiseases || "";
  $("profile-meds").value = profile.medications || "";
  $("profile-allergies").value = profile.allergies || "";
  $("profile-pregnancy").value = profile.pregnancy_status || profile.pregnancyStatus || "";
}

async function createProfile(useDemo = false) {
  const payload = patientFromRegistration(useDemo);
  if (!payload.patient.name) {
    $("reg-name").focus();
    showToast("请先填写姓名");
    return;
  }
  const user = await api("/api/users/upsert", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  fillProfile({ ...user, symptoms: payload.patient.symptoms }, payload.profile);
  showScreen("patient-shell");
  setPatientView("chat");
  await Promise.all([loadHistory(), loadFollowups()]);
}

async function saveProfile() {
  const patient = currentPatient();
  const profile = currentProfile();
  const user = await api("/api/users/upsert", {
    method: "POST",
    body: JSON.stringify({ patient, profile }),
  });
  fillProfile({ ...user, symptoms: patient.symptoms }, profile);
  $("profile-status").textContent = `已保存：${user.name}`;
  showToast("档案已保存");
  await loadHistory();
}

function loginDoctor(useDemo = false) {
  const doctor = {
    name: useDemo ? "课程演示医生" : $("doctor-name").value.trim(),
    department: useDemo ? "全科医学/检验复核" : $("doctor-dept").value.trim(),
  };
  if (!doctor.name) {
    $("doctor-name").focus();
    showToast("请填写医生姓名");
    return;
  }
  state.doctor = doctor;
  $("doctor-user-name").textContent = `${doctor.name} · ${doctor.department || "医生端"}`;
  showScreen("doctor-shell");
  setDoctorView("queue");
  loadDoctorData();
}

function setPatientView(view) {
  state.activePatientView = view;
  document.querySelectorAll(".patient-view").forEach((el) => el.classList.remove("active"));
  document.querySelectorAll("[data-patient-view]").forEach((el) => el.classList.toggle("active", el.dataset.patientView === view));
  $(`patient-${view}-view`).classList.add("active");
  const titles = {
    chat: ["报告解读", "上传报告、粘贴结果，或直接向我提问。"],
    followup: ["复诊随访", "查看复查提醒、反馈状态和逾期任务。"],
    history: ["历史趋势", "按个人档案沉淀历次报告。"],
  };
  $("patient-view-title").textContent = titles[view][0];
  $("patient-view-subtitle").textContent = titles[view][1];
  if (view === "followup") loadFollowups();
  if (view === "history") loadHistory();
}

function setDoctorView(view) {
  state.activeDoctorView = view;
  document.querySelectorAll(".doctor-view").forEach((el) => el.classList.remove("active"));
  document.querySelectorAll("[data-doctor-view]").forEach((el) => el.classList.toggle("active", el.dataset.doctorView === view));
  $(`doctor-${view}-view`).classList.add("active");
  const titles = { queue: "复核队列", patients: "患者概览", standards: "审核依据" };
  $("doctor-view-title").textContent = titles[view] || "医生工作台";
  if (view === "queue") loadReviews();
  if (view === "patients") loadDoctorPatients();
  if (view === "standards") loadDoctorStandards();
}

function renderSampleChips() {
  return state.samples
    .map((sample) => `<button class="prompt-chip" type="button" data-sample-id="${escapeHtml(sample.id)}">${escapeHtml(sample.name)}</button>`)
    .join("");
}

function renderWelcome() {
  $("chat-log").innerHTML = `
    <section class="welcome">
      <img src="/assets/healthmind-mark.svg" alt="" class="welcome-mark" />
      <h2>有什么报告需要我帮你看看？</h2>
      <p>可以上传体检报告图片或 PDF，也可以直接粘贴检验结果。我会先整理指标，再给出风险提示、复查建议和是否需要医生复核。</p>
      <div id="sample-chips" class="prompt-grid">${renderSampleChips()}</div>
    </section>
  `;
}

function addMessage(role, content, extraHtml = "") {
  const welcome = document.querySelector(".welcome");
  if (welcome) welcome.remove();
  $("chat-log").insertAdjacentHTML(
    "beforeend",
    `
    <article class="message ${escapeHtml(role)}">
      <div class="bubble">${content}</div>
      ${extraHtml}
    </article>
    `
  );
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
}

function addThinking(label = "正在思考") {
  const welcome = document.querySelector(".welcome");
  if (welcome) welcome.remove();
  const id = `thinking-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  $("chat-log").insertAdjacentHTML(
    "beforeend",
    `
    <article id="${id}" class="message assistant thinking-message">
      <div class="bubble thinking-bubble">
        <span>${escapeHtml(label)}</span>
        <i></i><i></i><i></i>
      </div>
    </article>
    `
  );
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
  return id;
}

function removeThinking(id) {
  if (!id) return;
  const node = $(id);
  if (node) node.remove();
}

function riskLabel(level) {
  return { low: "低风险", medium: "中风险", high: "高风险", red: "红旗风险" }[level] || level || "待判断";
}

function statusLabel(status) {
  return { normal: "正常", high: "升高", low: "降低", unknown: "待确认" }[status] || status || "待确认";
}

function valueWithUnit(item) {
  const value = item?.value ?? "";
  const unit = item?.unit ?? "";
  return `${value}${unit ? ` ${unit}` : ""}`;
}

function isReportLike(text) {
  return /([A-Z]{2,5}|白细胞|血红蛋白|血小板|血糖|肌酐|胆红素|转氨酶)[：:\s]*[-+]?\d/i.test(text);
}

async function sendComposer(event) {
  event.preventDefault();
  const text = $("chat-input").value.trim();
  if (!text) return;
  $("chat-input").value = "";
  autoGrow($("chat-input"));
  addMessage("user", escapeHtml(text));

  let thinkingId = "";
  try {
    if (isReportLike(text)) {
      await analyzeReport(text);
      return;
    }

    thinkingId = addThinking("正在思考，正在理解你的补充信息");
    const result = await api("/api/agent/chat", {
      method: "POST",
      body: JSON.stringify({ patient: currentPatient(), profile: currentProfile(), message: text }),
    });
    removeThinking(thinkingId);
    state.currentUser = result.user || state.currentUser;
    if (result.profile) fillProfile({ ...state.currentUser, symptoms: currentPatient().symptoms }, result.profile);
    const missing = result.missing_fields?.length
      ? `<p class="subtle-note">建议补充：${escapeHtml(result.missing_fields.join("、"))}</p>`
      : "";
    const updates = result.profile_updates?.length
      ? `<p class="subtle-note">已更新档案：${escapeHtml(result.profile_updates.join("、"))}</p>`
      : "";
    addMessage("assistant", `${escapeHtml(result.reply)}${updates}${missing}`);
  } catch (error) {
    removeThinking(thinkingId);
    addMessage("assistant", "这次处理没有成功，请稍后再试，或先检查服务是否还在运行。");
    console.error(error);
  }
}

async function analyzeSample(sampleId) {
  const sample = state.samples.find((item) => item.id === sampleId);
  if (!sample) return;
  addMessage("user", `请帮我解读样例：${escapeHtml(sample.name)}`);
  await analyzeReport(sample.report_text, []);
}

async function analyzeReport(reportText) {
  const thinkingId = addThinking("正在思考，正在综合判断报告");
  try {
    const result = await api("/api/analyze", {
      method: "POST",
      body: JSON.stringify({
        patient: currentPatient(),
        userProfile: currentProfile(),
        reportText,
      }),
    });
    removeThinking(thinkingId);
    addMessage("assistant", "我整理好了，这份报告的重点如下。", renderResultCard(result));
    await Promise.all([loadHistory(), loadFollowups()]);
  } catch (error) {
    removeThinking(thinkingId);
    addMessage("assistant", "报告分析没有成功。可以先检查指标格式，例如：ALT 86 U/L。");
    console.error(error);
  }
}

function stashEvidence(result) {
  const id = `ev-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  state.evidenceStore[id] = {
    risk: result.risk || {},
    items: result.rag_evidence || [],
    redFlags: result.risk?.red_flags || [],
    explanations: result.explanations || [],
    clinicalSynthesis: result.clinical_synthesis || {},
    aiEnhancement: result.ai_enhancement || {},
  };
  return id;
}

function renderResultCard(result) {
  const risk = result.risk || {};
  const abnormal = (result.explanations || []).filter((item) => item.status !== "normal");
  const topItems = abnormal.length ? abnormal : (result.explanations || []).slice(0, 4);
  const evidenceId = stashEvidence(result);
  const reviewText = risk.requires_review ? "建议由医生复核后再作为正式沟通依据。" : "可按常规随访观察。";
  const synthesis = result.clinical_synthesis || {};
  return `
    <section class="result-card">
      <div class="result-head">
        <div class="risk-summary risk-${escapeHtml(risk.risk_level)}">
          <strong>${riskLabel(risk.risk_level)}</strong>
          <span>风险分：${escapeHtml(risk.risk_score)}。${reviewText}</span>
          <span>${escapeHtml(result.followup?.title || "")}${result.followup?.due_date ? ` · ${escapeHtml(result.followup.due_date)}` : ""}</span>
        </div>
      </div>
      ${renderAiSummary(result.ai_enhancement)}
      ${renderClinicalSynthesis(synthesis)}
      <div class="metric-list">
        ${topItems.map(metricTemplate).join("")}
      </div>
      <button class="evidence-link" type="button" data-evidence-id="${evidenceId}">查看判断依据</button>
    </section>
  `;
}

function renderAiSummary(ai) {
  const summary = ai?.summary || {};
  const text = typeof summary === "string" ? summary : summary.patient_summary;
  if (!text) return "";
  const nextSteps = Array.isArray(summary.next_steps) ? summary.next_steps : [];
  return `
    <section class="ai-summary">
      <strong>AI 综合解释</strong>
      <p>${escapeHtml(text)}</p>
      ${nextSteps.length ? `<ul>${nextSteps.slice(0, 4).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}
    </section>
  `;
}

function renderClinicalSynthesis(synthesis) {
  const clusters = synthesis?.clusters || [];
  const recommendations = synthesis?.recommendations || [];
  const medication = synthesis?.medication_discussion || [];
  const urgent = synthesis?.urgent_actions || [];
  if (!clusters.length && !recommendations.length && !medication.length) return "";
  return `
    <section class="synthesis-panel">
      <header>
        <strong>综合判断</strong>
        <span>${escapeHtml(synthesis.summary || "")}</span>
      </header>
      <div class="cluster-list">
        ${clusters.slice(0, 4).map((cluster) => `
          <article class="cluster-card">
            <strong>${escapeHtml(cluster.title)}</strong>
            <p>${escapeHtml(cluster.reason)}</p>
            <span class="subtle-note">置信度：${escapeHtml(cluster.confidence)} · 相关指标：${escapeHtml((cluster.codes || []).join("、") || "暂无")}</span>
          </article>
        `).join("")}
      </div>
      ${urgent.length ? `<div class="urgent-box">${urgent.map((item) => `<p>${escapeHtml(item)}</p>`).join("")}</div>` : ""}
      ${recommendations.length ? `<div class="advice-list"><strong>下一步建议</strong><ul>${recommendations.slice(0, 5).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>` : ""}
      ${medication.length ? `<div class="medication-list"><strong>可与医生讨论的用药/处理方向</strong><ul>${medication.slice(0, 5).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul><p class="subtle-note">${escapeHtml(synthesis.disclaimer || "不要自行用药，药物需由医生判断。")}</p></div>` : ""}
    </section>
  `;
}

function metricTemplate(item) {
  return `
    <article class="metric-row">
      <header>
        <strong>${escapeHtml(item.name)} ${escapeHtml(item.code)}</strong>
        <span class="tag ${escapeHtml(item.status)}">${statusLabel(item.status)}</span>
      </header>
      <div>${escapeHtml(valueWithUnit(item))} · 参考 ${escapeHtml(item.reference)}</div>
      <p>${escapeHtml(item.message)}</p>
    </article>
  `;
}

function openEvidence(id) {
  const data = state.evidenceStore[id];
  if (!data) return;
  const redFlags = data.redFlags.length
    ? `<article class="standard-card"><strong>红旗提示</strong><p>${escapeHtml(data.redFlags.join("、"))}</p></article>`
    : "";
  const synthesis = data.clinicalSynthesis?.clusters?.length
    ? `<article class="standard-card"><strong>综合判断来源</strong><p>${escapeHtml(data.clinicalSynthesis.summary || "")}</p><span class="subtle-note">${escapeHtml(data.clinicalSynthesis.clusters.map((item) => item.title).join("、"))}</span></article>`
    : "";
  const sourceCards = data.items.length
    ? data.items.map(evidenceTemplate).join("")
    : `<article class="standard-card"><strong>本次依据</strong><p>系统优先使用报告中的参考区间，并结合内置课程知识库生成解释。具体诊疗仍需医生结合病史、体征和复查结果判断。</p></article>`;
  $("evidence-drawer-body").innerHTML = `
    <p class="subtle-note">这里展示本次结论背后的参考来源。它用于解释为什么提示风险，不等同于医生诊断。</p>
    ${redFlags}
    ${synthesis}
    ${sourceCards}
  `;
  openDrawer("evidence-drawer");
}

function evidenceTemplate(item) {
  const organization = item.standard?.organization || item.organization || item.source_id || "";
  const url = item.standard?.url || item.url || "";
  return `
    <article class="standard-card">
      <strong>${escapeHtml(item.title || item.source_id || "参考依据")}</strong>
      <p>${escapeHtml(item.text || item.scope || "")}</p>
      <span class="subtle-note">${escapeHtml(organization)}${item.evidence_level ? ` · ${escapeHtml(item.evidence_level)}` : ""}</span>
      ${url ? `<a class="evidence-link" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">查看来源</a>` : ""}
    </article>
  `;
}

async function handleFileChange(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  addMessage("user", `我上传了一份报告：${escapeHtml(file.name)}`);
  const reader = new FileReader();
  reader.onload = async () => {
    const thinkingId = addThinking("正在识别报告，图片越复杂可能越久");
    try {
      const result = await api("/api/ocr", {
        method: "POST",
        body: JSON.stringify({
          sampleId: state.samples[1]?.id || state.samples[0]?.id,
          imageName: file.name,
          imageData: reader.result,
          patient: currentPatient(),
        }),
      });
      removeThinking(thinkingId);
      addMessage("assistant", "我先把报告转成可校对内容。请核对文字或补录表格后再解读。", renderOcrReview(result, reader.result, file.type));
    } catch (error) {
      removeThinking(thinkingId);
      addMessage("assistant", "报告识别失败了。可以先把报告里的文字复制进输入框，我会继续解读。");
      console.error(error);
    }
  };
  reader.readAsDataURL(file);
  event.target.value = "";
}

function renderOcrReview(result, previewData = "", fileType = "") {
  const text = result.text || "";
  const example = result.manual_example || "ALT 86 U/L\nAST 62 U/L\nFPG 7.2 mmol/L";
  const warning = result.needs_manual_review
    ? "OCR没有可靠识别出结构化指标。请在下面按示例补录或校对后再解读。"
    : (result.message || "请先校对识别结果。");
  const rows = result.rows?.length
    ? `<div class="ocr-rows">${result.rows.map((row) => `<span class="tag">${escapeHtml(row.name)} ${escapeHtml(row.value)} ${escapeHtml(row.unit)}</span>`).join("")}</div>`
    : "";
  const preview = previewData && !fileType.includes("pdf")
    ? `<img class="ocr-preview-image" src="${escapeHtml(previewData)}" alt="上传的报告预览" />`
    : previewData
      ? `<div class="ocr-preview-pdf">已上传 PDF。若 PDF 是扫描件且识别失败，请按下方表格补录关键指标。</div>`
      : "";
  return `
    <section class="result-card ocr-review">
      <p class="subtle-note">${escapeHtml(warning)}</p>
      ${preview}
      ${rows}
      <textarea id="ocr-review-text" placeholder="如果识别结果为空或不准，可以直接按这个格式补录：&#10;${escapeHtml(example)}">${escapeHtml(text)}</textarea>
      <div class="manual-metric-panel">
        <strong>手动补录关键指标</strong>
        <p class="subtle-note">OCR 不准时，直接在这里填数值。系统会把这些指标合并进分析。</p>
        <div class="manual-metric-grid">
          ${MANUAL_METRICS.map((item) => `
            <label class="manual-metric-row">
              <span>${escapeHtml(item.code)} ${escapeHtml(item.name)}</span>
              <input data-manual-code="${escapeHtml(item.code)}" data-manual-unit="${escapeHtml(item.unit)}" placeholder="${escapeHtml(item.unit)}" />
            </label>
          `).join("")}
        </div>
      </div>
      <button class="primary" type="button" data-analyze-ocr>用这个结果解读</button>
    </section>
  `;
}

function collectOcrReviewText(container) {
  const text = container.querySelector("textarea")?.value.trim() || "";
  const manualLines = Array.from(container.querySelectorAll("[data-manual-code]"))
    .map((input) => {
      const value = input.value.trim();
      if (!value) return "";
      return `${input.dataset.manualCode} ${value} ${input.dataset.manualUnit}`;
    })
    .filter(Boolean);
  return [text, ...manualLines].filter(Boolean).join("\n");
}

function formatOcrRowLine(row) {
  const reference = row.reference ? ` 参考 ${row.reference}` : "";
  return `${row.name || row.code || "指标"} ${row.value ?? ""} ${row.unit || ""}${reference}`.trim();
}

function renderOcrReview(result, previewData = "", fileType = "") {
  const recognizedRows = Array.isArray(result.rows) ? result.rows : [];
  const hiddenText = [
    recognizedRows.map(formatOcrRowLine).join("\n"),
    result.text || "",
  ].filter(Boolean).join("\n");
  const warning = recognizedRows.length
    ? `已识别 ${recognizedRows.length} 项指标。请核对数值，确认后再解读。`
    : "OCR 没有可靠识别出结构化指标。可以直接在下方补录任意指标后继续解读。";
  const statusText = { high: "偏高", low: "偏低", normal: "正常", unknown: "待确认" };
  const rows = recognizedRows.length
    ? `<div class="ocr-row-table">${recognizedRows.map((row) => `
        <div class="ocr-row-pill ${escapeHtml(row.abnormal || "unknown")}">
          <strong>${escapeHtml(row.name || row.code || "指标")}</strong>
          <span>${escapeHtml(row.value)} ${escapeHtml(row.unit || "")}</span>
          <em>${escapeHtml(statusText[row.abnormal] || "待确认")}</em>
          ${row.reference ? `<small>参考 ${escapeHtml(row.reference)}</small>` : ""}
        </div>
      `).join("")}</div>`
    : "";
  const preview = previewData && !fileType.includes("pdf")
    ? `<img class="ocr-preview-image" src="${escapeHtml(previewData)}" alt="上传的报告预览" />`
    : previewData
      ? `<div class="ocr-preview-pdf">已上传 PDF。若 PDF 是扫描件且识别失败，可在下方补录关键指标。</div>`
      : "";
  const customRows = Array.from({ length: CUSTOM_METRIC_ROWS }).map((_, index) => `
    <div class="custom-metric-row">
      <input data-custom-name placeholder="指标名 ${index + 1}" />
      <input data-custom-value placeholder="结果" />
      <input data-custom-unit placeholder="单位" />
      <input data-custom-ref placeholder="参考区间，如 0.29-1.70" />
    </div>
  `).join("");
  return `
    <section class="result-card ocr-review">
      <p class="subtle-note">${escapeHtml(warning)}</p>
      ${preview}
      ${rows}
      <textarea class="ocr-hidden-text" hidden>${escapeHtml(hiddenText)}</textarea>
      <div class="manual-metric-panel">
        <strong>补录或修正指标</strong>
        <p class="subtle-note">固定项目可直接填数值；陌生项目填到“任意指标”里，系统会按报告参考区间先做基础判断。</p>
        <div class="manual-metric-grid">
          ${MANUAL_METRICS.map((item) => `
            <label class="manual-metric-row">
              <span>${escapeHtml(item.code)} ${escapeHtml(item.name)}</span>
              <input data-manual-code="${escapeHtml(item.code)}" data-manual-unit="${escapeHtml(item.unit)}" placeholder="${escapeHtml(item.unit)}" />
            </label>
          `).join("")}
        </div>
        <div class="custom-metric-panel">
          <strong>任意指标</strong>
          ${customRows}
        </div>
      </div>
      <button class="primary" type="button" data-analyze-ocr>用这个结果解读</button>
    </section>
  `;
}

function collectOcrReviewText(container) {
  const hiddenText = container.querySelector(".ocr-hidden-text")?.value.trim() || "";
  const manualLines = Array.from(container.querySelectorAll("[data-manual-code]"))
    .map((input) => {
      const value = input.value.trim();
      if (!value) return "";
      return `${input.dataset.manualCode} ${value} ${input.dataset.manualUnit}`;
    })
    .filter(Boolean);
  const customLines = Array.from(container.querySelectorAll(".custom-metric-row"))
    .map((row) => {
      const name = row.querySelector("[data-custom-name]")?.value.trim() || "";
      const value = row.querySelector("[data-custom-value]")?.value.trim() || "";
      const unit = row.querySelector("[data-custom-unit]")?.value.trim() || "";
      const reference = row.querySelector("[data-custom-ref]")?.value.trim() || "";
      if (!name || !value) return "";
      return `${name} ${value} ${unit} ${reference}`.trim();
    })
    .filter(Boolean);
  return [hiddenText, ...manualLines, ...customLines].filter(Boolean).join("\n");
}

async function loadFollowups() {
  const items = await api("/api/followups");
  $("followup-list").innerHTML = items.length
    ? items.map(followupTemplate).join("")
    : `<div class="empty">暂无随访任务。完成一次报告解读后会自动生成。</div>`;
}

function followupTemplate(item) {
  const done = item.status === "已反馈";
  const review = item.doctor_review || {};
  const reviewPanel = item.doctor_reviewed || review.opinion || review.patient_message || review.tests
    ? `
      <section class="doctor-feedback-panel">
        <strong>医生复核结果</strong>
        <p>结论：${escapeHtml(review.decision || item.status || "医生已复核")} · 风险：${riskLabel(review.risk_level || item.priority)}</p>
        ${review.opinion ? `<p>医生意见：${escapeHtml(review.opinion)}</p>` : ""}
        ${review.patient_message ? `<p>给患者的话：${escapeHtml(review.patient_message)}</p>` : ""}
        ${review.tests ? `<p>建议复查/处理：${escapeHtml(review.tests)}</p>` : ""}
        ${review.reviewed_at ? `<p class="subtle-note">复核时间：${escapeHtml(review.reviewed_at)}</p>` : ""}
      </section>
    `
    : "";
  return `
    <article class="task-card" data-follow-id="${escapeHtml(item.follow_id || item.task_id)}">
      <header>
        <strong>${escapeHtml(item.title || "复诊提醒")}</strong>
        <span class="tag ${item.overdue ? "overdue" : ""}">${item.overdue ? "逾期" : escapeHtml(item.priority || "待办")}</span>
      </header>
      <p>截止日期：${escapeHtml(item.due_date || "")} · 状态：${escapeHtml(item.status || "")}</p>
      ${reviewPanel}
      ${(item.questions || []).length ? `<div class="followup-questions">${item.questions.map((q) => `<span>${escapeHtml(q)}</span>`).join("")}</div>` : ""}
      <textarea class="feedback-note" rows="2" placeholder="补充近期情况或复查结果"${done ? " disabled" : ""}>${escapeHtml(item.feedback?.note || "")}</textarea>
      <button type="button" data-submit-feedback ${done ? "disabled" : ""}>提交反馈</button>
    </article>
  `;
}

async function submitFeedback(card) {
  const note = card.querySelector(".feedback-note").value;
  await api("/api/followups/feedback", {
    method: "POST",
    body: JSON.stringify({ followId: card.dataset.followId, note, newSymptoms: note, answers: [], completed: Boolean(note) }),
  });
  showToast("随访反馈已提交");
  await loadFollowups();
}

async function loadHistory() {
  const name = state.currentUser?.name || "";
  const userId = state.currentUser?.user_id || "";
  const history = await api(`/api/history?patient=${encodeURIComponent(name)}&userId=${encodeURIComponent(userId)}`);
  state.history = history;
  $("sidebar-history").innerHTML = history.length
    ? history.slice(0, 8).map(historyMiniTemplate).join("")
    : `<div class="subtle-note">暂无历史报告</div>`;
  $("history-list").innerHTML = history.length
    ? history.map(historyCardTemplate).join("")
    : `<div class="empty">暂无历史记录。</div>`;
  renderHistoryTrend(history);
}

function renderHistoryTrend(history) {
  const container = $("history-trend");
  if (!container) return;
  if (!history.length) {
    container.innerHTML = `<div class="empty">暂无可绘制的趋势。完成两次以上报告解读后，会显示风险分和关键指标曲线。</div>`;
    return;
  }
  container.innerHTML = `
    <header>
      <strong>趋势曲线</strong>
      <span>风险分与异常指标次数</span>
    </header>
    <canvas id="trend-canvas" width="920" height="260" aria-label="历史趋势曲线"></canvas>
  `;
  const canvas = $("trend-canvas");
  const ctx = canvas.getContext("2d");
  const points = history.slice().reverse().slice(-12).map((item, index) => ({
    index,
    date: (item.created_at || "").slice(5, 10) || `#${index + 1}`,
    riskScore: Number(item.risk?.risk_score || 0),
    abnormalCount: (item.explanations || []).filter((x) => x.status !== "normal").length,
  }));
  drawTrend(ctx, canvas.width, canvas.height, points);
}

function drawTrend(ctx, width, height, points) {
  ctx.clearRect(0, 0, width, height);
  const pad = { left: 52, right: 28, top: 28, bottom: 44 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const maxValue = Math.max(5, ...points.map((p) => Math.max(p.riskScore, p.abnormalCount)));

  ctx.strokeStyle = "#dfe6ee";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let i = 0; i <= 4; i += 1) {
    const y = pad.top + (innerH / 4) * i;
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
  }
  ctx.stroke();

  ctx.fillStyle = "#7a8595";
  ctx.font = "13px Microsoft YaHei, Arial";
  points.forEach((point, idx) => {
    const x = pad.left + (points.length === 1 ? innerW / 2 : (innerW / (points.length - 1)) * idx);
    ctx.fillText(point.date, x - 18, height - 18);
  });

  const toXY = (point, idx, field) => ({
    x: pad.left + (points.length === 1 ? innerW / 2 : (innerW / (points.length - 1)) * idx),
    y: pad.top + innerH - (point[field] / maxValue) * innerH,
  });
  drawLine(ctx, points, "riskScore", "#236c75", toXY);
  drawLine(ctx, points, "abnormalCount", "#a46a18", toXY);

  ctx.fillStyle = "#236c75";
  ctx.fillRect(pad.left, 10, 12, 3);
  ctx.fillText("风险分", pad.left + 18, 15);
  ctx.fillStyle = "#a46a18";
  ctx.fillRect(pad.left + 92, 10, 12, 3);
  ctx.fillText("异常指标数", pad.left + 110, 15);
}

function drawLine(ctx, points, field, color, toXY) {
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 3;
  ctx.beginPath();
  points.forEach((point, idx) => {
    const xy = toXY(point, idx, field);
    if (idx === 0) ctx.moveTo(xy.x, xy.y);
    else ctx.lineTo(xy.x, xy.y);
  });
  ctx.stroke();
  points.forEach((point, idx) => {
    const xy = toXY(point, idx, field);
    ctx.beginPath();
    ctx.arc(xy.x, xy.y, 4, 0, Math.PI * 2);
    ctx.fill();
  });
}

function historyMiniTemplate(item) {
  return `
    <button class="history-mini" type="button" data-history-task="${escapeHtml(item.task_id || "")}">
      <strong>${escapeHtml((item.created_at || "").slice(0, 10) || "报告")}</strong>
      <span>${riskLabel(item.risk?.risk_level)} · ${escapeHtml(item.task_id || "")}</span>
    </button>
  `;
}

function historyCardTemplate(item) {
  const abnormal = (item.explanations || []).filter((x) => x.status !== "normal").slice(0, 4);
  return `
    <article class="history-card" data-history-task="${escapeHtml(item.task_id || "")}">
      <header>
        <strong>${escapeHtml((item.created_at || "").slice(0, 10))}</strong>
        <span class="tag ${escapeHtml(item.risk?.risk_level || "")}">${riskLabel(item.risk?.risk_level)}</span>
      </header>
      <p>风险分：${escapeHtml(item.risk?.risk_score ?? "")}</p>
      <div>${abnormal.map((x) => `<span class="tag ${escapeHtml(x.status)}">${escapeHtml(x.code)} ${escapeHtml(valueWithUnit(x))}</span>`).join(" ") || "未见明显异常"}</div>
    </article>
  `;
}

function openHistoryCase(taskId) {
  const item = state.history.find((historyItem) => historyItem.task_id === taskId);
  if (!item) return;
  setPatientView("chat");
  addMessage("assistant", `已打开 ${escapeHtml((item.created_at || "").slice(0, 10) || "历史")} 的报告记录。`, renderResultCard(item));
}

async function loadDoctorData() {
  await Promise.all([loadReviews(), loadDoctorPatients()]);
  if (state.activeDoctorView === "standards") await loadDoctorStandards();
}

async function loadReviews() {
  const reviews = await api("/api/reviews");
  $("review-list").innerHTML = reviews.length
    ? reviews.map(reviewTemplate).join("")
    : `<div class="empty">暂无医生复核任务。患者端生成高风险或红旗报告后会进入这里。</div>`;
  renderDoctorKpis(reviews);
}

function renderDoctorKpis(reviews) {
  const high = reviews.filter((item) => ["high", "red"].includes(item.risk_level)).length;
  const pending = reviews.filter((item) => !String(item.status || "").includes("提交")).length;
  $("doctor-kpis").innerHTML = `
    <article class="kpi-card"><strong>${reviews.length}</strong><span>复核任务</span></article>
    <article class="kpi-card"><strong>${high}</strong><span>高风险或红旗</span></article>
    <article class="kpi-card"><strong>${pending}</strong><span>待医生确认</span></article>
  `;
}

function reviewTemplate(item) {
  const review = item.doctor_review || {};
  const abnormal = item.abnormal_items || [];
  const diseaseAssist = item.disease_assist || item.clinical_synthesis || {};
  return `
    <article class="review-card" data-review-id="${escapeHtml(item.review_id || item.task_id)}">
      <header>
        <div>
          <strong>${escapeHtml(item.case_no || item.review_id || item.task_id)}</strong>
          <p>${escapeHtml(item.patient?.name || "")} · ${escapeHtml(item.patient?.age || "")}岁 · ${escapeHtml(item.patient?.sex || "")}</p>
        </div>
        <span class="tag ${escapeHtml(item.risk_level)}">${riskLabel(item.risk_level)}</span>
      </header>
      <p>${escapeHtml(item.summary || item.review_reason || "")}</p>
      ${renderDoctorDiseaseAssist(diseaseAssist)}
      <div>${abnormal.slice(0, 8).map((x) => `<span class="tag ${escapeHtml(x.status)}">${escapeHtml(x.code)} ${escapeHtml(valueWithUnit(x))}</span>`).join(" ")}</div>
      <div class="review-form">
        <label>
          风险判断
          <select class="doctor-risk">
            ${["low", "medium", "high", "red"].map((level) => `<option value="${level}"${(review.risk_level || item.risk_level) === level ? " selected" : ""}>${riskLabel(level)}</option>`).join("")}
          </select>
        </label>
        <label>
          复查日期
          <input class="doctor-due" type="date" value="${escapeHtml(review.due_date || item.proposed_followup?.due_date || "")}" />
        </label>
        <label class="wide-field">
          医生意见
          <textarea class="doctor-opinion" rows="3">${escapeHtml(review.opinion || "")}</textarea>
        </label>
        <label class="wide-field">
          给患者的话
          <textarea class="doctor-message" rows="3">${escapeHtml(review.patient_message || "")}</textarea>
        </label>
      </div>
      <button class="primary" type="button" data-submit-review>提交复核结论</button>
    </article>
  `;
}

function renderDoctorDiseaseAssist(assist) {
  const clusters = assist.clusters || [];
  const recommendations = assist.recommendations || [];
  const medication = assist.medication_discussion || [];
  if (!clusters.length && !recommendations.length) return "";
  return `
    <section class="doctor-assist-panel">
      <strong>辅助疾病判断</strong>
      <p>${escapeHtml(assist.title || assist.summary || "需结合病史和复查综合判断")}</p>
      <div class="doctor-assist-clusters">
        ${clusters.slice(0, 3).map((cluster) => `
          <span class="tag medium">${escapeHtml(cluster.title)} · ${escapeHtml(cluster.confidence || "待确认")}</span>
        `).join("")}
      </div>
      ${recommendations.length ? `<p class="subtle-note">建议：${escapeHtml(recommendations.slice(0, 3).join("；"))}</p>` : ""}
      ${medication.length ? `<p class="subtle-note">用药讨论：${escapeHtml(medication.slice(0, 2).join("；"))}</p>` : ""}
    </section>
  `;
}

async function submitReview(card) {
  await api("/api/reviews/decision", {
    method: "POST",
    body: JSON.stringify({
      reviewId: card.dataset.reviewId,
      riskLevel: card.querySelector(".doctor-risk").value,
      dueDate: card.querySelector(".doctor-due").value,
      opinion: card.querySelector(".doctor-opinion").value,
      patientMessage: card.querySelector(".doctor-message").value,
      decision: "医生已复核",
      status: "已复核",
      reviewer: state.doctor?.name || "医生端",
    }),
  });
  showToast("医生复核意见已提交");
  await Promise.all([loadReviews(), loadFollowups()]);
}

async function loadDoctorPatients() {
  const users = await api("/api/users");
  const reviews = await api("/api/reviews?all=1");
  const userRows = Array.isArray(users) ? users : users.users || [];
  const reviewPatients = reviews.map((item) => ({
    name: item.patient?.name || "未命名患者",
    age: item.patient?.age,
    sex: item.patient?.sex,
    risk: item.risk_level,
    summary: item.summary || item.review_reason || "",
    indicators: item.abnormal_items || [],
  }));
  const rows = userRows.length
    ? userRows.map((user) => ({
        name: user.name,
        age: user.age,
        sex: user.sex,
        risk: "档案",
        summary: user.symptoms || "已建立个人档案",
        indicators: [],
      }))
    : reviewPatients;
  $("doctor-patient-list").innerHTML = rows.length
    ? rows.map(patientOverviewTemplate).join("")
    : `<div class="empty">暂无患者档案。患者端注册或完成报告解读后会出现在这里。</div>`;
}

function patientOverviewTemplate(item) {
  return `
    <article class="patient-overview-card">
      <header>
        <div>
          <strong>${escapeHtml(item.name || "未命名患者")}</strong>
          <p>${escapeHtml(item.age || "")}岁 · ${escapeHtml(item.sex || "")}</p>
        </div>
        <span class="tag ${escapeHtml(item.risk || "")}">${riskLabel(item.risk) || "档案"}</span>
      </header>
      <p>${escapeHtml(item.summary || "暂无补充情况")}</p>
      <div>${(item.indicators || []).slice(0, 6).map((x) => `<span class="tag ${escapeHtml(x.status)}">${escapeHtml(x.code)} ${escapeHtml(valueWithUnit(x))}</span>`).join(" ")}</div>
    </article>
  `;
}

async function loadDoctorStandards() {
  const data = await api("/api/standards");
  const docs = data.knowledge_documents || [];
  $("knowledge-summary").innerHTML = `
    <span>本地知识文档：${docs.length}</span>
    <span>标准来源：${(data.standards || []).length}</span>
    <span>知识图谱关系：${(data.knowledge_graph || []).length}</span>
  `;
  $("doctor-standards-list").innerHTML = docs.length
    ? docs.map(evidenceTemplate).join("")
    : (data.standards || []).map(evidenceTemplate).join("");
}

async function searchKnowledge(pubmed = false) {
  const query = $("knowledge-query").value.trim();
  if (!query) {
    showToast("请输入要检索的医学关键词");
    return;
  }
  $("doctor-standards-list").innerHTML = `<div class="empty">正在检索...</div>`;
  if (pubmed) {
    const data = await api(`/api/knowledge/pubmed?q=${encodeURIComponent(query)}&limit=6`);
    $("knowledge-summary").innerHTML = `<span>PubMed 检索：${escapeHtml(query)}</span><span>结果：${data.results?.length || 0}</span>${data.error ? `<span>${escapeHtml(data.error)}</span>` : ""}`;
    $("doctor-standards-list").innerHTML = data.results?.length
      ? data.results.map(pubmedTemplate).join("")
      : `<div class="empty">没有检索到 PubMed 结果。</div>`;
    return;
  }
  const data = await api(`/api/knowledge/search?q=${encodeURIComponent(query)}`);
  $("knowledge-summary").innerHTML = `<span>本地 RAG 检索：${escapeHtml(query)}</span><span>结果：${data.results?.length || 0}</span>`;
  $("doctor-standards-list").innerHTML = data.results?.length
    ? data.results.map(evidenceTemplate).join("")
    : `<div class="empty">本地知识库暂未命中，可以换关键词或补充知识文档。</div>`;
}

function pubmedTemplate(item) {
  return `
    <article class="standard-card">
      <strong>${escapeHtml(item.title || item.pmid)}</strong>
      <p>${escapeHtml([item.source, item.pubdate].filter(Boolean).join(" · "))}</p>
      ${item.authors?.length ? `<span class="subtle-note">${escapeHtml(item.authors.join("、"))}</span>` : ""}
      <a class="evidence-link" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">PubMed PMID: ${escapeHtml(item.pmid)}</a>
    </article>
  `;
}

function openDrawer(id) {
  document.querySelectorAll(".drawer").forEach((el) => el.classList.add("hidden"));
  $(id).classList.remove("hidden");
}

function closeDrawers() {
  document.querySelectorAll(".drawer").forEach((el) => el.classList.add("hidden"));
}

function autoGrow(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(textarea.scrollHeight, 140)}px`;
}

function handleComposerKeydown(event) {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  $("composer").requestSubmit();
}

function toggleVoiceInput() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    showToast("当前浏览器不支持语音输入，可以使用 Chrome/Edge 试试");
    return;
  }
  if (state.speechRecognition) {
    state.speechRecognition.stop();
    return;
  }
  const recognition = new SpeechRecognition();
  recognition.lang = "zh-CN";
  recognition.interimResults = true;
  recognition.continuous = false;
  state.speechRecognition = recognition;
  $("voice-trigger").classList.add("listening");
  $("voice-trigger").textContent = "聆听中";

  recognition.onresult = (event) => {
    const text = Array.from(event.results)
      .map((result) => result[0]?.transcript || "")
      .join("");
    $("chat-input").value = text;
    autoGrow($("chat-input"));
  };
  recognition.onerror = () => showToast("语音识别没有成功，请再试一次");
  recognition.onend = () => {
    state.speechRecognition = null;
    $("voice-trigger").classList.remove("listening");
    $("voice-trigger").textContent = "语音";
  };
  recognition.start();
}

function bindEvents() {
  document.querySelectorAll("[data-role-entry]").forEach((button) => {
    button.addEventListener("click", () => showScreen(button.dataset.roleEntry === "doctor" ? "doctor-login-screen" : "patient-login-screen"));
  });
  document.querySelectorAll("[data-back-portal]").forEach((button) => button.addEventListener("click", () => showScreen("portal-screen")));

  $("create-profile-btn").addEventListener("click", () => createProfile(false));
  $("demo-profile-btn").addEventListener("click", () => createProfile(true));
  $("doctor-login-btn").addEventListener("click", () => loginDoctor(false));
  $("doctor-demo-btn").addEventListener("click", () => loginDoctor(true));
  $("patient-logout-btn").addEventListener("click", () => showScreen("portal-screen"));
  $("doctor-logout-btn").addEventListener("click", () => showScreen("portal-screen"));
  $("switch-user-btn").addEventListener("click", () => showScreen("patient-login-screen"));

  $("new-chat-btn").addEventListener("click", () => {
    renderWelcome();
    setPatientView("chat");
  });
  document.querySelectorAll("[data-patient-view]").forEach((btn) => btn.addEventListener("click", () => setPatientView(btn.dataset.patientView)));
  document.querySelectorAll("[data-doctor-view]").forEach((btn) => btn.addEventListener("click", () => setDoctorView(btn.dataset.doctorView)));
  $("doctor-refresh-btn").addEventListener("click", () => loadDoctorData());
  $("knowledge-search-btn").addEventListener("click", () => searchKnowledge(false));
  $("pubmed-search-btn").addEventListener("click", () => searchKnowledge(true));
  $("knowledge-query").addEventListener("keydown", (event) => {
    if (event.key === "Enter") searchKnowledge(false);
  });
  $("sidebar-toggle").addEventListener("click", () => document.querySelector(".patient-sidebar").classList.toggle("open"));

  $("profile-btn").addEventListener("click", () => openDrawer("profile-drawer"));
  document.querySelectorAll("[data-close-drawer]").forEach((btn) => btn.addEventListener("click", closeDrawers));
  $("save-profile-btn").addEventListener("click", saveProfile);
  $("composer").addEventListener("submit", sendComposer);
  $("upload-trigger").addEventListener("click", () => $("report-file-input").click());
  $("voice-trigger").addEventListener("click", toggleVoiceInput);
  $("report-file-input").addEventListener("change", handleFileChange);
  $("chat-input").addEventListener("input", () => autoGrow($("chat-input")));
  $("chat-input").addEventListener("keydown", handleComposerKeydown);

  document.body.addEventListener("click", async (event) => {
    const sampleButton = event.target.closest("[data-sample-id]");
    if (sampleButton) analyzeSample(sampleButton.dataset.sampleId);

    const historyButton = event.target.closest("[data-history-task]");
    if (historyButton) openHistoryCase(historyButton.dataset.historyTask);

    const evidenceButton = event.target.closest("[data-evidence-id]");
    if (evidenceButton) openEvidence(evidenceButton.dataset.evidenceId);

    const ocrButton = event.target.closest("[data-analyze-ocr]");
    if (ocrButton) {
      const text = collectOcrReviewText(ocrButton.closest(".ocr-review"));
      if (text) await analyzeReport(text);
      else showToast("请先校对 OCR 文本或手动补录至少一个指标");
    }

    const feedbackButton = event.target.closest("[data-submit-feedback]");
    if (feedbackButton) await submitFeedback(feedbackButton.closest(".task-card"));

    const reviewButton = event.target.closest("[data-submit-review]");
    if (reviewButton) await submitReview(reviewButton.closest(".review-card"));
  });
}

async function init() {
  bindEvents();
  state.samples = await api("/api/sample-reports");
  renderWelcome();
}

init().catch((error) => {
  console.error(error);
  document.body.insertAdjacentHTML("beforeend", `<div class="empty">系统初始化失败，请确认服务已启动。</div>`);
});

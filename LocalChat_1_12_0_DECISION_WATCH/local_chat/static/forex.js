"use strict";
// Forex desk page. No inline styles in markup (CSP): sizes are set through the CSSOM.
const TFS = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"];
const STYLE_FA = { M1: "اسکالپ", M5: "اسکالپ", M15: "اینترادی", M30: "اینترادی", H1: "اینترادی", H4: "سوینگ", D1: "سوینگ" };
const PLAN = {
  M1: "M1 M5 M15 H1", M5: "M1 M5 M15 H1 H4", M15: "M5 M15 H1 H4 D1", M30: "M15 M30 H1 H4 D1",
  H1: "M15 H1 H4 D1 W1", H4: "H1 H4 D1 W1", D1: "H4 D1 W1",
};
const KEY = "forex-desk-v1";
const $ = (id) => document.getElementById(id);

const state = { tf: "M15", analysisId: null, history: [], busy: false, caps: {}, report: null };

function load() {
  try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { return {}; }
}
function save(extra) {
  try { localStorage.setItem(KEY, JSON.stringify({ ...load(), ...extra })); } catch (e) { /* private mode */ }
}

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) node.append(c);
  return node;
}
const pct = (v) => `${Math.round(v * 100)}%`;
const fmt = (v, d) => (v == null ? "—" : Number(v).toFixed(d));
const ltr = (text) => `\u2066${text}\u2069`;   // keep numbers intact inside right-to-left text
const pips = (v, r) => (v == null ? "—" : ltr(r.pip ? `${(v / r.pip).toFixed(1)} pip` : Number(v).toFixed(r.digits)));

function setStatus(text, error) {
  const box = $("status");
  box.textContent = text || "";
  box.classList.toggle("error", !!error);
}

// ---------- safe markdown-lite ----------
function inline(text) {
  const frag = document.createDocumentFragment();
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  for (const p of parts) {
    if (p.startsWith("**") && p.endsWith("**") && p.length > 4) frag.append(el("strong", { text: p.slice(2, -2) }));
    else if (p.startsWith("`") && p.endsWith("`") && p.length > 2) frag.append(el("code", { text: p.slice(1, -1) }));
    else frag.append(document.createTextNode(p));
  }
  return frag;
}
function renderMarkdown(target, source) {
  target.textContent = "";
  let list = null;
  for (const raw of source.split("\n")) {
    const line = raw.trimEnd();
    const bullet = /^\s*(?:[-*•]|\d+[.)])\s+(.*)$/.exec(line);
    if (bullet) {
      const ordered = /^\s*\d/.test(line);
      if (!list || list.tagName !== (ordered ? "OL" : "UL")) { list = el(ordered ? "ol" : "ul"); target.append(list); }
      list.append(el("li", {}, inline(bullet[1])));
      continue;
    }
    list = null;
    const head = /^#{1,4}\s+(.*)$/.exec(line);
    if (head) target.append(el("h3", {}, inline(head[1])));
    else if (line.trim()) target.append(el("p", {}, inline(line)));
  }
}

// ---------- NDJSON ----------
async function streamPost(url, body, onEvent) {
  const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!response.ok) {
    let data = {};
    try { data = await response.json(); } catch (e) { /* not json */ }
    const error = new Error(data.error || `خطای سرور (${response.status})`);
    error.code = data.code; error.status = response.status;
    throw error;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index;
    while ((index = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, index).trim();
      buffer = buffer.slice(index + 1);
      if (!line) continue;
      try { onEvent(JSON.parse(line)); } catch (e) { if (e instanceof SyntaxError) continue; throw e; }
    }
  }
  if (buffer.trim()) { try { onEvent(JSON.parse(buffer)); } catch (e) { /* partial tail */ } }
}

// ---------- rendering ----------
function renderDecision(d) {
  const box = $("decision");
  box.textContent = "";
  if (!d) { box.hidden = true; return; }
  box.hidden = false;
  const kind = { plan: "plan", watch: "watch", no_trade: "none" }[d.action] || "none";
  const title = { plan: "سناریوی آماده", watch: "فقط پایش", no_trade: "معامله نکن" }[d.action] || "";
  box.className = `card decision ${kind}`;
  box.append(el("div", { class: "dhead" }, el("span", { class: `badge dbadge ${kind}`, text: title }), el("b", { text: d.headline })));
  if (d.reasons && d.reasons.length) box.append(el("ul", { class: "dreasons" }, d.reasons.map((t) => el("li", { text: t }))));
  if (d.steps && d.steps.length) {
    box.append(el("h3", { text: "چه کار کنم؟" }), el("ol", { class: "dsteps" }, d.steps.map((t) => el("li", { text: t }))));
  }
  box.append(el("p", { class: "dnote", text: "این تصمیم را کد با قواعد ثابت می‌سازد و به مدل زبانی وابسته نیست؛ متن تحلیلگر پایین صفحه فقط توضیح آن است." }));
}

function renderSummary(r) {
  const box = $("summary");
  box.textContent = "";
  box.append(
    el("span", { class: "sym", text: r.symbol }),
    el("span", { class: "price", text: `${fmt(r.market.bid, r.digits)} / ${fmt(r.market.ask, r.digits)}` }),
    el("span", { class: "meta", text: `اسپرد ${pips(r.market.spread, r)} · ${r.market.text} · سشن: ${r.market.sessions.join("+") || "خارج از سشن اصلی"} · ${r.market.fetched_utc} UTC` }),
    el("span", { class: "meta", text: `معامله روی ${r.timeframe} (${r.style_label})` }),
  );
  if (r.lean_text) box.append(el("div", { class: `lean ${r.lean || ""}`, text: r.lean_text }));
  for (const w of r.warnings) box.append(el("div", { class: "warn", text: "⚠ " + w }));
  renderNews(box, r.news);
}

function renderNews(box, news) {
  if (!news) return;
  const wrap = el("div", { class: "news" });
  if (!news.ok) {
    wrap.append(el("div", { class: "legend", text: `تقویم خبری در دسترس نیست: ${news.error}` }));
  } else if (!news.events.length) {
    wrap.append(el("div", { class: "legend", text: `تقویم خبری ${news.currencies.join("/")}: رویداد مهمی تا ۲۴ ساعت آینده ثبت نشده.` }));
  } else {
    wrap.append(el("b", { text: `تقویم خبری ${news.currencies.join("/")}` }));
    for (const e of news.events) {
      const when = e.minutes >= 0 ? `${Math.floor(e.minutes / 60)}h ${e.minutes % 60}m بعد` : `${-e.minutes}m پیش`;
      const hot = e.impact === "high";
      wrap.append(el("div", { class: `ev-row ${hot ? "high" : ""}` },
        el("span", { class: "ev-tag", text: hot ? "مهم" : "متوسط" }),
        el("span", { text: `${e.currency} ${e.title}` }),
        el("span", { class: "legend", text: ltr(`${e.utc.slice(11)} UTC · ${when}`) + (e.forecast || e.previous ? ` · پیش‌بینی ${e.forecast || "-"} / قبلی ${e.previous || "-"}` : "") })));
    }
  }
  box.append(wrap);
}

function probRow(kind, title, p, ci, base) {
  const track = el("div", { class: "track" });
  const fill = el("div", { class: "fill" });
  fill.style.width = pct(p);
  const whisker = el("div", { class: "whisker" });
  whisker.style.insetInlineStart = pct(ci[0]);
  whisker.style.width = `${Math.max(1, (ci[1] - ci[0]) * 100)}%`;
  const mark = el("div", { class: "basemark" });
  mark.style.insetInlineStart = pct(base);
  track.append(fill, whisker, mark);
  return el("div", { class: `prob ${kind}` },
    el("div", { class: "label" }, el("span", { text: title }), el("b", { text: `${pct(p)}  [${Math.round(ci[0] * 100)}–${Math.round(ci[1] * 100)}]` })),
    track);
}

function renderProb(r) {
  const card = $("probCard");
  card.textContent = "";
  card.append(el("h2", { text: `احتمال تجربی ${r.timeframe}` }));
  const q = r.quant;
  if (!q) {
    card.append(el("p", { class: "legend", text: r.warnings.find((w) => w.startsWith("تاریخچه")) || "احتمال تجربی در دسترس نیست." }));
    return;
  }
  card.append(
    el("p", { class: "legend", text: `${q.horizon_bars} کندل بعدی (≈ ${q.horizon_text}) · حرکت مبنا ±${pips(q.barrier_price, r)} (${ltr(q.barrier_atr.toFixed(1) + "×ATR")})` }),
    probRow("up", "صعودی (بالا خوردن اول)", q.probs.up, q.ci80.up, q.base.up),
    probRow("down", "نزولی (پایین خوردن اول)", q.probs.down, q.ci80.down, q.base.down),
    probRow("range", "رنج (هیچ‌کدام)", q.probs.range, q.ci80.range, q.base.range),
    el("p", { class: "legend", text: "عدد درشت = احتمال، [بازه ۸۰٪]، خط سفید = نرخ پایهٔ تاریخی همین نماد." }),
  );
  const v = q.validation;
  if (q.grade === "بدون لبه" || q.grade === "ناکافی") {
    card.append(el("div", { class: "banner bad", text: q.grade === "بدون لبه"
      ? "⚠ در آزمون پیشرو، مدل روی این نماد و تایم‌فریم از نرخ پایه بهتر نبود؛ این درصدها تقریباً همان فراوانی تاریخی‌اند و برای ورود دلیل کافی نیستند."
      : "⚠ تاریخچه برای آزمون پیشرو کافی نبود؛ درصدها را فقط تقریبی بدان." }));
  }
  if (q.news_damping < 1) {
    card.append(el("div", { class: "banner warn", text: "خبر مهم داخل افق مدل است؛ درصدها اضافه بر اعتبار مدل هم به‌سمت نرخ پایه کشیده شده‌اند." }));
  }
  const gradeClass = q.grade === "قابل اتکا" ? "good" : q.grade === "ضعیف" ? "mid" : "bad";
  const line = el("p", {}, "اعتبار آزمون پیشرو: ", el("span", { class: `badge ${gradeClass}`, text: q.grade }));
  card.append(line, el("p", { class: "legend", text: q.grade_text +
    (v ? ` (Brier skill ${(v.skill * 100).toFixed(1)}٪ روی ${v.tested} کندل)` : "") +
    (q.damping < 1 ? ` درصدها ${Math.round((1 - q.damping) * 100)}٪ به‌سمت نرخ پایه کشیده شده‌اند.` : "") }));
  if (v && v.reliability && v.reliability.length) {
    const details = el("details", { class: "calib" }, el("summary", { text: `کالیبراسیون (خطای میانگین ${v.ece == null ? "—" : (v.ece * 100).toFixed(1) + "٪"})` }));
    const table = el("table");
    table.append(el("tr", {}, ["وقتی مدل می‌گفت", "واقعاً اتفاق افتاد", "تعداد"].map((t) => el("th", { text: t }))));
    for (const b of v.reliability) {
      table.append(el("tr", {}, el("td", { class: "num", text: pct(b.predicted) }), el("td", { class: "num", text: pct(b.actual) }), el("td", { class: "num", text: String(b.count) })));
    }
    details.append(table, el("p", { class: "legend", text: "هرچه دو ستون اول به هم نزدیک‌تر باشند، درصدهای مدل واقعی‌ترند." }));
    card.append(details);
  }
  const s = q.state;
  card.append(el("p", { class: "legend", text: `وضعیت فعلی: روند ${s.trend} · بایاس تایم‌فریم‌های بالاتر ${s.htf} · ${s.location} · ${s.regime} · سشن ${s.session} · ${q.labeled_bars} موقعیت تاریخی (معادل مستقل ≈ ${Math.round(q.effective_samples)})` }));
}

function renderChart(r) {
  const card = $("chartCard");
  card.textContent = "";
  card.append(el("h2", { text: `چارت ${r.symbol} ${r.timeframe}` }));
  if (r.chart) card.append(el("img", { class: "chartimg", src: r.chart, alt: `چارت ${r.symbol}` }));
  else card.append(el("p", { class: "legend", text: "چارت ساخته نشد." }));
  card.append(el("div", { id: "visionBox" }));
}

function renderVision(outcome) {
  const box = $("visionBox");
  if (!box) return;
  box.textContent = "";
  const wrap = el("div", { class: "vision" });
  if (!outcome.ok) {
    wrap.append(el("p", { class: "legend", text: outcome.error }));
  } else {
    const r = outcome.read;
    const agreement = { agree: ["هم‌جهت با مدل کمّی", "agree"], conflict: ["در تعارض با مدل کمّی", "conflict"], neutral: ["خنثی نسبت به مدل کمّی", ""] }[outcome.agreement || "neutral"];
    wrap.append(
      el("p", {}, el("b", { text: `خوانش بینایی (${outcome.model}): ` }), el("span", { class: `badge ${agreement[1]}`, text: agreement[0] })),
      el("p", { text: `روند: ${r.trend || "—"} · بایاس: ${r.bias} (اطمینان ${r.confidence})` }),
      el("p", { text: `ساختار: ${r.structure || "—"}` }),
      el("p", { text: `الگو: ${r.patterns.join("، ") || "الگوی روشنی دیده نشد"}` }),
      el("p", { text: `ناحیهٔ واکنش: ${r.nearest_reaction_zone || "—"}` }),
      el("p", { class: "legend", text: "نظر دوم است و روی درصدها اثری ندارد؛ در تعارض، وزن با مدل کمّی است." }),
    );
  }
  box.append(wrap);
}

function renderScenarios(r) {
  const box = $("scenarios");
  box.textContent = "";
  if (!r.scenarios.length) {
    box.append(el("p", { class: "legend", text: "بدون تاریخچهٔ کافی، سناریوی احتمال‌دار ساخته نمی‌شود." }));
    return;
  }
  for (const sc of r.scenarios) {
    const long = sc.direction === "long";
    const o = sc.odds;
    const ev = sc.expectancy_net_r;
    const dl = el("dl", { class: "kv" });
    const add = (k, v, cls) => { dl.append(el("dt", { text: k }), el("dd", { class: cls || "", text: v })); };
    add("تأیید", sc.trigger != null ? `کلوز ${long ? "بالای" : "زیر"} ${fmt(sc.trigger, r.digits)}` : "ادامهٔ حرکت در جهت سناریو");
    add("ورود فرضی", fmt(sc.entry, r.digits));
    add("حد ضرر / ابطال", `${fmt(sc.stop, r.digits)} (${pips(sc.risk, r)} · ${sc.stop_basis})`);
    add("هدف ۱", `${fmt(sc.target1, r.digits)} (R:R 1:${sc.rr1.toFixed(1)})`);
    add("هدف ۲", `${fmt(sc.target2, r.digits)} (1:${sc.rr2.toFixed(1)})`);
    add("رسیدن به هدف ۱ اول", `${pct(o.target)} [${Math.round(o.target_ci80[0] * 100)}–${Math.round(o.target_ci80[1] * 100)}]`);
    add("خوردن حد ضرر اول", pct(o.stop));
    add("بی‌نتیجه تا پایان افق", pct(o.timeout));
    add("امید ریاضی خالص", `${ev >= 0 ? "+" : ""}${ev.toFixed(2)} R (بعد از اسپرد)`, `ev ${ev >= 0 ? "pos" : "neg"}`);
    box.append(el("div", { class: `sc ${sc.direction}` },
      el("h3", { text: long ? "سناریوی لانگ ▲" : "سناریوی شورت ▼" }), dl,
      el("p", { class: "legend", text: `بر پایهٔ ${o.samples} موقعیت مشابه تاریخی (معادل مستقل ≈ ${Math.round(o.effective)})` })));
  }
}

function renderFrames(r) {
  const table = $("frames");
  table.textContent = "";
  const head = el("tr", {}, ["تایم‌فریم", "روند", "ساختار", "RSI", "ADX", "فاصله از EMA50", "حمایت", "مقاومت"].map((t) => el("th", { text: t })));
  table.append(el("thead", {}, head));
  const body = el("tbody");
  for (const f of r.frames) {
    const row = el("tr", { class: f.name === r.timeframe ? "trade" : "" });
    const num = (t) => el("td", { class: "num", text: t });
    row.append(
      el("td", { text: `${f.name}${f.name === r.timeframe ? " ◄ معامله" : ""}` }),
      el("td", { text: `${f.trend} (${f.score > 0 ? "+" : ""}${f.score}/${f.max})` }),
      el("td", { text: f.structure + (f.bos ? ` · ${f.bos}` : "") }),
      num(fmt(f.rsi, 0)), num(fmt(f.adx, 0)),
      num(f.ema50_distance_atr == null ? "—" : `${f.ema50_distance_atr >= 0 ? "+" : ""}${f.ema50_distance_atr.toFixed(1)} ATR`),
      num(f.supports.slice(0, 2).map((p) => fmt(p, r.digits)).join("  ") || "—"),
      num(f.resistances.slice(0, 2).map((p) => fmt(p, r.digits)).join("  ") || "—"));
    body.append(row);
  }
  table.append(body);
}

function renderReport(r) {
  state.report = r;
  $("results").hidden = false;
  renderDecision(r.decision); renderSummary(r); renderProb(r); renderChart(r); renderScenarios(r); renderFrames(r);
}

// ---------- actions ----------
function setBusy(busy) {
  state.busy = busy;
  $("run").disabled = busy;
  $("askBtn").disabled = busy;
}

async function explain(question) {
  const out = $("narrative");
  if (question) {
    out.append(el("div", { class: "q", text: question }));
  } else {
    out.textContent = "";
    state.history = [];
  }
  const answer = el("div", { class: "prose" });
  out.append(answer);
  answer.textContent = "در حال نوشتن…";
  let text = "";
  const model = $("textModel").value;
  if (!model) { answer.textContent = "هیچ مدل متنی نصب یا انتخاب نشده است."; return; }
  const send = async () => streamPost("/api/forex/explain", {
    analysis_id: state.analysisId, model, question: question || undefined, history: state.history.slice(-6),
  }, (event) => {
    if (event.error) throw new Error(event.error);
    const piece = event.message && event.message.content;
    if (piece) { text += piece; renderMarkdown(answer, text); }
  });
  try {
    await send();
  } catch (error) {
    if (error.code === "analysis_expired") {
      setStatus("تحلیل منقضی شده بود؛ داده‌ی تازه گرفته می‌شود…");
      await analyze(true);
      text = "";
      await send();
    } else {
      throw error;
    }
  }
  if (!text) answer.textContent = "مدل پاسخی نداد.";
  state.history.push({ role: "user", content: question || "گزارش کامل" }, { role: "assistant", content: text.slice(0, 1500) });
}

async function analyze(silent) {
  if (state.busy && !silent) return;
  const symbol = $("symbol").value.trim();
  if (!symbol) { setStatus("نام جفت‌ارز را بنویس.", true); return; }
  const body = {
    symbol, timeframe: state.tf, vision: $("useVision").checked,
    vision_model: $("visionModel").value || undefined,
  };
  save({ symbol, tf: state.tf, useVision: body.vision, visionModel: $("visionModel").value, textModel: $("textModel").value });
  setBusy(true);
  setStatus("در حال تحلیل…");
  let failed = false;
  try {
    await streamPost("/api/forex/analyze", body, (event) => {
      if (event.notice) setStatus(event.notice);
      if (event.error) { failed = true; setStatus(event.error, true); }
      if (event.report) { state.analysisId = event.analysis_id; renderReport(event.report); }
      if (event.vision) renderVision(event.vision);
      if (event.decision) renderDecision(event.decision);
      if (event.done) state.analysisId = event.analysis_id;
    });
    if (failed) return;
    // keep the chat's default timeframe in step with what the person trades on (best effort)
    fetch("/api/settings/forex", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ default_timeframe: state.tf, use_vision: body.vision }) }).catch(() => {});
    if (!silent) {
      setStatus("تحلیل کمّی آماده است؛ گزارش تحلیلگر نوشته می‌شود…");
      await explain("");
      setStatus("");
    }
  } catch (error) {
    setStatus(error.status === 403 ? "ابتدا در صفحهٔ گفت‌وگو هویت سازنده را تأیید کن، سپس برگرد." : error.message, true);
  } finally {
    setBusy(false);
  }
}

async function ask(event) {
  event.preventDefault();
  const input = $("askInput");
  const question = input.value.trim();
  if (!question || state.busy || !state.analysisId) return;
  input.value = "";
  setBusy(true);
  try { await explain(question); setStatus(""); }
  catch (error) { setStatus(error.message, true); }
  finally { setBusy(false); }
}

// ---------- setup ----------
function renderTf() {
  const seg = $("tfSeg");
  seg.textContent = "";
  for (const tf of TFS) {
    const b = el("button", { type: "button", role: "radio", "aria-checked": String(tf === state.tf), text: tf });
    b.addEventListener("click", () => { state.tf = tf; renderTf(); });
    seg.append(b);
  }
  $("planHint").textContent = `${STYLE_FA[state.tf]} · تایم‌فریم‌های تحلیل: ${PLAN[state.tf]}`;
}

function fillModels(settings) {
  const names = Object.keys(state.caps);
  const saved = load();
  const vision = names.filter((n) => (state.caps[n] || []).includes("vision"));
  const text = names.filter((n) => !(state.caps[n] || []).includes("embedding"));
  const vSel = $("visionModel"), tSel = $("textModel");
  vSel.textContent = ""; tSel.textContent = "";
  vSel.append(el("option", { value: "", text: vision.length ? "خودکار" : "هیچ مدل بینایی نصب نیست" }));
  for (const n of vision) vSel.append(el("option", { value: n, text: n }));
  for (const n of text) tSel.append(el("option", { value: n, text: n }));
  let chat = "";
  try { chat = localStorage.getItem("local-chat-model") || ""; } catch (e) { /* ignore */ }
  const pick = [settings.text_model, saved.textModel, chat].find((n) => n && text.includes(n)) || text.find((n) => !vision.includes(n)) || text[0] || "";
  if (pick) tSel.value = pick;
  const v = [settings.vision_model, saved.visionModel].find((n) => n && vision.includes(n));
  if (v) vSel.value = v;
}

async function init() {
  const saved = load();
  if (saved.symbol) $("symbol").value = saved.symbol;
  if (TFS.includes(saved.tf)) state.tf = saved.tf;
  if (typeof saved.useVision === "boolean") $("useVision").checked = saved.useVision;
  let settings = {};
  try {
    const data = await (await fetch("/api/settings")).json();
    settings = data.forex || {};
    if (!saved.tf && TFS.includes(settings.default_timeframe)) state.tf = settings.default_timeframe;
    if (typeof settings.news_enabled === "boolean") $("newsOn").checked = settings.news_enabled;
    if (typeof saved.useVision !== "boolean" && typeof settings.use_vision === "boolean") $("useVision").checked = settings.use_vision;
  } catch (e) { /* settings are optional */ }
  renderTf();
  try {
    const data = await (await fetch("/api/models/capabilities")).json();
    state.caps = data.capabilities || {};
  } catch (e) { state.caps = {}; }
  fillModels(settings);
  try {
    const status = await (await fetch("/api/forex/status")).json();
    const chip = $("mt5Chip");
    chip.textContent = !status.installed ? "MT5: ماژول نصب نیست" : status.connected ? "MT5: متصل" : "MT5: آمادهٔ اتصال";
    chip.classList.add(status.installed ? "ok" : "bad");
  } catch (e) { $("mt5Chip").textContent = "MT5: نامشخص"; }
  $("newsOn").addEventListener("change", () => {
    fetch("/api/settings/forex", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ news_enabled: $("newsOn").checked }) }).catch(() => {});
  });
  $("run").addEventListener("click", () => analyze(false));
  $("symbol").addEventListener("keydown", (e) => { if (e.key === "Enter") analyze(false); });
  $("askForm").addEventListener("submit", ask);
}
init();

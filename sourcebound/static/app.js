// @ts-check
/**
 * Sourcebound front end. Plain ES module, no build step; type-checked with `tsc --checkJs`.
 * All server data is rendered with textContent / DOM nodes — never innerHTML — so text from
 * sources cannot inject markup.
 */

/** @typedef {{id: string, title: string, kind: string, origin: string, chars: number, pages: number|null, chunks: number, is_sample: boolean, created_at: number}} Source */
/** @typedef {{step: number, kind: string, title: string, detail: string, status: string, tool: string|null, duration_ms: number}} TraceEvent */
/** @typedef {{text: string, citations: number[], support: string, support_score: number}} Sentence */
/** @typedef {{query: string, passages: number, evidence: number, coverage: number, top_score: number}} Attempt */
/** @typedef {{id: string, question: string, status: string, sentences: Sentence[], attempts: Attempt[]}} Section */
/** @typedef {{id: number, source_id: string, source_title: string, page: number|null, quote: string, context: string, chunk_id: string}} Citation */
/** @typedef {{sentence: string, figure: string, chunk_id: string, citation: number}} ConflictSide */
/** @typedef {{kind: string, about: string[], a: ConflictSide, b: ConflictSide, summary: string}} Conflict */
/** @typedef {{question: string, mode: string, model: string|null, status: string, sections: Section[], citations: Citation[], conflicts: Conflict[], removed_claims: {sub_question: string, text: string, reason: string}[], limitations: string[], stats: {duration_ms: number, tool_calls: number, llm_calls: number, sources_in_scope: number, passages_in_scope: number, sub_questions: number}, trace: TraceEvent[]}} ResearchReport */
/** @typedef {{id: string, question: string, status: string, mode: string, created_at: number, finished_at: number|null, events: TraceEvent[], report: ResearchReport|null, error: string|null}} Run */
/** @typedef {{id: string, question: string, status: string, mode: string, created_at: number, finished_at: number|null}} RunSummary */
/** @typedef {{llm_available: boolean, llm_model: string|null, allow_url_fetch: boolean, max_upload_mb: number, max_sources: number, retention_hours: number, version: string, sample_questions: string[]}} Config */

const POLL_MS = 600;
const STATUS_TEXT = /** @type {Record<string, string>} */ ({
  complete: "Complete — every sub-question has supporting evidence",
  partial: "Partial — some sub-questions lack strong evidence",
  failed: "No answer — your sources did not contain relevant evidence",
  queued: "Waiting to start",
  running: "Researching",
});
const SECTION_TEXT = /** @type {Record<string, string>} */ ({
  answered: "Answered",
  weak: "Thin evidence",
  not_found: "No evidence found",
  skipped: "Not researched — limit reached",
});
const KIND_TEXT = /** @type {Record<string, string>} */ ({ pdf: "PDF", text: "Text", markdown: "Markdown", html: "HTML", url: "Web page" });

// ---------------------------------------------------------------------------
// Small DOM helpers
// ---------------------------------------------------------------------------

/**
 * @template {keyof HTMLElementTagNameMap} K
 * @param {K} tag
 * @param {Record<string, string|boolean|number|undefined>} [attrs]
 * @param {...(Node|string|null|undefined|false)} children
 * @returns {HTMLElementTagNameMap[K]}
 */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === false) continue;
    if (key === "text") node.textContent = String(value);
    else if (key === "class") node.className = String(value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

/**
 * @template {HTMLElement} T
 * @param {string} id
 * @returns {T}
 */
function $(id) {
  const node = document.getElementById(id);
  if (!node) throw new Error(`Missing element #${id}`);
  return /** @type {T} */ (node);
}

/** @param {number} seconds */
function timeAgo(seconds) {
  const diff = Date.now() / 1000 - seconds;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  return new Date(seconds * 1000).toLocaleDateString();
}

/** @param {number} n @param {string} word */
function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

// ---------------------------------------------------------------------------
// Workspace id and API client
// ---------------------------------------------------------------------------

const WS_KEY = "sourcebound.workspace";

function newWorkspaceId() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function loadWorkspaceId() {
  try {
    const existing = localStorage.getItem(WS_KEY);
    if (existing && /^[a-f0-9]{32}$/.test(existing)) return existing;
    const fresh = newWorkspaceId();
    localStorage.setItem(WS_KEY, fresh);
    return fresh;
  } catch {
    return newWorkspaceId(); // storage blocked: this tab gets a temporary workspace
  }
}

let workspaceId = loadWorkspaceId();

class ApiError extends Error {
  /** @param {string} message @param {number} status */
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

/**
 * @param {string} path
 * @param {{method?: string, json?: unknown, form?: FormData}} [options]
 * @returns {Promise<any>}
 */
async function api(path, options = {}) {
  /** @type {Record<string, string>} */
  const headers = { "X-Workspace-Id": workspaceId };
  /** @type {BodyInit|undefined} */
  let body;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  } else if (options.form) {
    body = options.form;
  }
  let response;
  try {
    response = await fetch(path, { method: options.method || "GET", headers, body });
  } catch {
    throw new ApiError("Cannot reach the Sourcebound server. Check that it is running.", 0);
  }
  if (response.status === 204) return null;
  let data = null;
  try {
    data = await response.json();
  } catch {
    /* non-JSON error page */
  }
  if (!response.ok) {
    throw new ApiError((data && data.error) || `Request failed (HTTP ${response.status}).`, response.status);
  }
  return data;
}

/** @param {string} path @param {string} filename */
async function downloadExport(path, filename) {
  const response = await fetch(path, { headers: { "X-Workspace-Id": workspaceId } });
  if (!response.ok) throw new ApiError("The export could not be created.", response.status);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = el("a", { href: url, download: filename });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

/** @type {{config: Config|null, sources: Source[], excluded: Set<string>, runs: RunSummary[], run: Run|null, pollTimer: number|undefined, uploading: string[]}} */
const state = { config: null, sources: [], excluded: new Set(), runs: [], run: null, pollTimer: undefined, uploading: [] };

/** @param {string} message @param {boolean} [isError] */
function announce(message, isError = false) {
  const line = $("status-line");
  line.textContent = message;
  line.classList.toggle("error", isError);
}

/** @param {unknown} error */
function messageOf(error) {
  return error instanceof Error ? error.message : "Something went wrong.";
}

// ---------------------------------------------------------------------------
// Library
// ---------------------------------------------------------------------------

function selectedSourceIds() {
  return state.sources.filter((s) => !state.excluded.has(s.id)).map((s) => s.id);
}

function renderScope() {
  const total = state.sources.length;
  const chosen = selectedSourceIds().length;
  const note = $("scope-note");
  if (!total) note.textContent = "Add a source to start.";
  else if (chosen === total) note.textContent = `Searching all ${plural(total, "source")}.`;
  else note.textContent = `Searching ${chosen} of ${plural(total, "source")}.`;
  const runBtn = /** @type {HTMLButtonElement} */ ($("run-btn"));
  const busy = state.run !== null && (state.run.status === "queued" || state.run.status === "running");
  runBtn.disabled = busy;
  runBtn.textContent = busy ? "Researching…" : "Research";
}

function renderSources() {
  const list = $("source-list");
  list.replaceChildren();
  for (const source of state.sources) {
    const checkboxId = `src-${source.id}`;
    const checkbox = el("input", { type: "checkbox", id: checkboxId });
    checkbox.checked = !state.excluded.has(source.id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.excluded.delete(source.id);
      else state.excluded.add(source.id);
      renderScope();
    });
    const size = source.pages ? plural(source.pages, "page") : `${Math.max(1, Math.round(source.chars / 1000))}k characters`;
    const meta = [KIND_TEXT[source.kind] || source.kind, size].join(", ");
    const remove = el("button", { type: "button", class: "icon-btn", "aria-label": `Delete ${source.title}`, text: "Delete" });
    remove.addEventListener("click", () => deleteSource(source));
    list.append(
      el(
        "li",
        { class: "source-item" },
        checkbox,
        el(
          "label",
          { for: checkboxId },
          el("span", { class: "source-title", text: source.title }),
          el("span", { class: "source-meta" }, meta, source.is_sample ? el("span", { class: "tag-sample", text: ", fictional sample" }) : null),
        ),
        remove,
      ),
    );
  }
  for (const name of state.uploading) {
    list.append(el("li", { class: "pending-item", text: `Adding ${name}…` }));
  }
  $("source-count").textContent = state.sources.length ? `(${state.sources.length})` : "";
  $("source-empty").hidden = state.sources.length > 0 || state.uploading.length > 0;
  renderSamples();
  renderScope();
}

function renderSamples() {
  const hasSample = state.sources.some((s) => s.is_sample);
  const box = $("samples");
  box.hidden = !hasSample || !state.config;
  const chips = $("sample-chips");
  chips.replaceChildren();
  for (const question of state.config ? state.config.sample_questions : []) {
    const chip = el("button", { type: "button", class: "chip", text: question });
    chip.addEventListener("click", () => {
      const field = /** @type {HTMLTextAreaElement} */ ($("question"));
      field.value = question;
      field.focus();
    });
    chips.append(chip);
  }
}

async function refreshSources() {
  const data = await api("/api/sources");
  state.sources = data.sources;
  const known = new Set(state.sources.map((s) => s.id));
  for (const id of state.excluded) if (!known.has(id)) state.excluded.delete(id);
  renderSources();
}

/** @param {Source} source */
async function deleteSource(source) {
  try {
    await api(`/api/sources/${source.id}`, { method: "DELETE" });
    announce(`Deleted “${source.title}”.`);
    await refreshSources();
  } catch (error) {
    announce(messageOf(error), true);
  }
}

/** @param {FileList|File[]} files */
async function uploadFiles(files) {
  const maxBytes = (state.config ? state.config.max_upload_mb : 10) * 1024 * 1024;
  const added = [];
  const failed = [];
  for (const file of Array.from(files)) {
    if (file.size > maxBytes) {
      failed.push(`${file.name}: larger than ${state.config ? state.config.max_upload_mb : 10} MB`);
      continue;
    }
    state.uploading.push(file.name);
    renderSources();
    try {
      const form = new FormData();
      form.append("file", file, file.name);
      await api("/api/sources/upload", { method: "POST", form });
      added.push(file.name);
    } catch (error) {
      failed.push(`${file.name}: ${messageOf(error)}`);
    } finally {
      state.uploading = state.uploading.filter((n) => n !== file.name);
    }
    await refreshSources();
  }
  if (failed.length) announce(`Could not add ${failed.join("; ")}`, true);
  else if (added.length) announce(`Added ${plural(added.length, "source")}.`);
}

async function loadSample() {
  const button = /** @type {HTMLButtonElement} */ ($("load-sample"));
  button.disabled = true;
  try {
    await api("/api/sources/sample", { method: "POST" });
    await refreshSources();
    announce("Sample library loaded. Pick a question below, or write your own.");
  } catch (error) {
    announce(messageOf(error), true);
  } finally {
    button.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// History
// ---------------------------------------------------------------------------

function renderHistory() {
  const list = $("history-list");
  list.replaceChildren();
  $("history-empty").hidden = state.runs.length > 0;
  for (const run of state.runs) {
    const open = el(
      "button",
      { type: "button", class: "history-open", "aria-current": state.run && state.run.id === run.id ? "true" : undefined },
      el("span", { class: "history-q", text: run.question }),
      el("span", { class: "history-meta", text: `${(STATUS_TEXT[run.status] || run.status).split(" —")[0]}, ${timeAgo(run.created_at)}` }),
    );
    open.addEventListener("click", () => openRun(run.id));
    list.append(el("li", { class: "history-item" }, open));
  }
}

async function refreshHistory() {
  const data = await api("/api/runs");
  state.runs = data.runs;
  renderHistory();
}

// ---------------------------------------------------------------------------
// Runs
// ---------------------------------------------------------------------------

/** @param {TraceEvent[]} events @param {HTMLElement} list @param {boolean} working */
function renderLog(events, list, working) {
  list.replaceChildren();
  for (const event of events) {
    list.append(
      el(
        "li",
        { "data-kind": event.kind, "data-status": event.status },
        el("span", { class: "dot", "aria-hidden": "true" }),
        el(
          "div",
          {},
          el("span", { class: "log-title", text: event.title }),
          event.duration_ms ? el("span", { class: "log-ms", text: ` (${event.duration_ms} ms)` }) : null,
          el("div", { class: "log-detail", text: event.detail }),
        ),
      ),
    );
  }
  if (working) {
    list.append(
      el(
        "li",
        { class: "working", "data-kind": "working" },
        el("span", { class: "dot", "aria-hidden": "true" }),
        el("div", {}, el("span", { class: "log-title", text: events.length ? "Working…" : "Starting…" })),
      ),
    );
  }
}

/** @param {Run} run */
function renderRun(run) {
  state.run = run;
  $("intro").hidden = true;
  $("run").hidden = false;
  $("run-question").textContent = run.question;
  const working = run.status === "queued" || run.status === "running";
  $("run-meta").textContent = working
    ? `${STATUS_TEXT[run.status]}, started ${timeAgo(run.created_at)}`
    : `Run ${timeAgo(run.created_at)}`;
  $("progress").hidden = !working;
  renderLog(run.events, $("progress-log"), working);

  const errorBox = $("run-error");
  errorBox.hidden = !run.error;
  errorBox.textContent = run.error || "";
  $("report").hidden = !run.report;
  if (run.report) renderReport(run.report);
  renderScope();
  renderHistory();
}

/**
 * @param {Citation[]} citations
 * @param {number} n
 * @param {boolean} [contra]
 */
function citeChip(citations, n, contra = false) {
  const citation = citations.find((c) => c.id === n);
  const label = citation ? `Evidence ${n}: ${citation.source_title}${citation.page ? `, page ${citation.page}` : ""}` : `Evidence ${n}`;
  const chip = el("button", { type: "button", class: contra ? "cite contra" : "cite", "aria-label": label, title: label, text: String(n) });
  chip.addEventListener("click", () => openCitation(n, chip));
  return chip;
}

/** @param {ResearchReport} report */
function renderReport(report) {
  const verdict = $("verdict");
  verdict.dataset.status = report.status;
  const modeText =
    report.mode === "llm"
      ? `Written by ${report.model || "an AI model"} from the evidence; every sentence was checked against its citations.`
      : "Extractive mode: each highlighted sentence is quoted word-for-word from your sources.";
  verdict.replaceChildren(
    el("p", { class: "verdict-title", text: STATUS_TEXT[report.status] || report.status }),
    el("p", { class: "verdict-mode", text: modeText }),
  );

  const sections = $("sections");
  sections.replaceChildren();
  for (const section of report.sections) {
    const body = el("div", { class: "answer-body" });
    if (section.sentences.length) {
      for (const sentence of section.sentences) {
        body.append(
          el(
            "p",
            {},
            el("span", { class: sentence.support === "weak" ? "claim weak" : "claim", text: sentence.text }),
            " ",
            ...sentence.citations.map((n) => citeChip(report.citations, n)),
            sentence.support === "weak" ? el("span", { class: "weak-note", text: "partly supported" }) : null,
          ),
        );
      }
    } else {
      body.append(el("p", { class: "no-evidence", text: "No supporting evidence was found in the selected sources." }));
    }
    const tries = section.attempts.length;
    sections.append(
      el(
        "section",
        { class: "answer", "aria-labelledby": `sec-${section.id}` },
        el("h2", { id: `sec-${section.id}`, text: section.question }),
        el("span", { class: "answer-status", "data-status": section.status, text: SECTION_TEXT[section.status] || section.status }),
        body,
        tries
          ? el("p", {
              class: "attempts",
              text: tries > 1 ? `Searched ${tries} times; the first query “${section.attempts[0].query}” was revised.` : "Found on the first search.",
            })
          : null,
      ),
    );
  }

  const conflictList = $("conflict-list");
  conflictList.replaceChildren();
  $("conflicts").hidden = report.conflicts.length === 0;
  for (const conflict of report.conflicts) {
    /** @param {ConflictSide} side */
    const sideBox = (side) =>
      el(
        "div",
        { class: "conflict-side" },
        el("span", { class: "conflict-figure", text: side.figure }),
        `“${side.sentence}” `,
        citeChip(report.citations, side.citation, true),
      );
    conflictList.append(
      el(
        "li",
        { class: "conflict" },
        el("p", { class: "conflict-summary", text: conflict.summary }),
        el("div", { class: "conflict-sides" }, sideBox(conflict.a), sideBox(conflict.b)),
      ),
    );
  }

  const removedList = $("removed-list");
  removedList.replaceChildren();
  $("removed").hidden = !report.removed_claims.length;
  for (const claim of report.removed_claims) {
    removedList.append(el("li", {}, el("s", { text: claim.text }), ` — ${claim.reason}`));
  }

  const limitList = $("limit-list");
  limitList.replaceChildren(...report.limitations.map((item) => el("li", { text: item })));

  const citationList = $("citation-list");
  citationList.replaceChildren();
  for (const citation of report.citations) {
    citationList.append(
      el(
        "li",
        { id: `citation-${citation.id}` },
        citeChip(report.citations, citation.id),
        el(
          "div",
          {},
          el("q", { text: citation.quote }),
          el("div", { class: "src", text: `${citation.source_title}${citation.page ? `, page ${citation.page}` : ""}` }),
        ),
      ),
    );
  }

  const s = report.stats;
  $("trace-summary").textContent = `— ${plural(s.tool_calls, "tool call")}, ${plural(s.llm_calls, "model call")}, ${(s.duration_ms / 1000).toFixed(1)}s, ${plural(s.passages_in_scope, "passage")} searched`;
  renderLog(report.trace, $("trace-log"), false);
}

/** @param {number} n @param {HTMLElement} opener */
function openCitation(n, opener) {
  const report = state.run && state.run.report;
  const citation = report && report.citations.find((c) => c.id === n);
  if (!citation) return;
  const drawer = $("drawer");
  $("drawer-title").textContent = `Evidence ${n}`;
  $("drawer-source").textContent = `${citation.source_title}${citation.page ? `, page ${citation.page}` : ""}`;
  const context = $("drawer-context");
  const fullText = citation.context.replace(/\n{2,}/g, "\n");
  const at = fullText.indexOf(citation.quote);
  if (at >= 0) {
    context.replaceChildren(fullText.slice(0, at), el("mark", { text: citation.quote }), fullText.slice(at + citation.quote.length));
  } else {
    context.replaceChildren(el("mark", { text: citation.quote }));
  }
  drawer.hidden = false;
  drawer.focus();
  drawerOpener = opener;
}

/** @type {HTMLElement|null} */
let drawerOpener = null;

function closeDrawer() {
  const drawer = $("drawer");
  if (drawer.hidden) return;
  drawer.hidden = true;
  if (drawerOpener) drawerOpener.focus();
  drawerOpener = null;
}

/** @param {string} runId */
async function poll(runId) {
  window.clearTimeout(state.pollTimer);
  try {
    /** @type {Run} */
    const run = await api(`/api/runs/${runId}`);
    if (!state.run || state.run.id !== runId) return; // user opened another run
    renderRun(run);
    if (run.status === "queued" || run.status === "running") {
      state.pollTimer = window.setTimeout(() => poll(runId), POLL_MS);
    } else {
      announce(run.error ? "The run stopped with an error." : `Report ready: ${(STATUS_TEXT[run.status] || run.status).split(" —")[0].toLowerCase()}.`, Boolean(run.error));
      await refreshHistory();
    }
  } catch (error) {
    announce(`Lost contact with the run: ${messageOf(error)} Retrying…`, true);
    state.pollTimer = window.setTimeout(() => poll(runId), POLL_MS * 4);
  }
}

/** @param {string} runId */
async function openRun(runId) {
  closeDrawer();
  try {
    /** @type {Run} */
    const run = await api(`/api/runs/${runId}`);
    renderRun(run);
    history.replaceState(null, "", `#run=${runId}`);
    if (run.status === "queued" || run.status === "running") poll(runId);
    $("run-question").setAttribute("tabindex", "-1");
    $("run-question").focus();
  } catch (error) {
    announce(messageOf(error), true);
    history.replaceState(null, "", "#");
  }
}

/** @param {SubmitEvent} event */
async function submitQuestion(event) {
  event.preventDefault();
  const field = /** @type {HTMLTextAreaElement} */ ($("question"));
  const errorBox = $("question-error");
  const question = field.value.trim();
  errorBox.textContent = "";
  if (question.length < 8) {
    errorBox.textContent = "Write a fuller question (at least 8 characters).";
    field.focus();
    return;
  }
  const sourceIds = selectedSourceIds();
  if (!sourceIds.length) {
    errorBox.textContent = state.sources.length ? "Select at least one source to search." : "Add a source first — upload a file, paste text, or load the sample library.";
    return;
  }
  const useLlm = /** @type {HTMLInputElement} */ ($("use-llm")).checked;
  closeDrawer();
  try {
    const { id } = await api("/api/runs", { method: "POST", json: { question, source_ids: sourceIds, use_llm: useLlm } });
    state.run = { id, question, status: "queued", mode: useLlm ? "llm" : "extractive", created_at: Date.now() / 1000, finished_at: null, events: [], report: null, error: null };
    renderRun(state.run);
    history.replaceState(null, "", `#run=${id}`);
    announce("Research started.");
    await refreshHistory();
    poll(id);
  } catch (error) {
    errorBox.textContent = messageOf(error);
  }
}

// ---------------------------------------------------------------------------
// Dialogs and settings
// ---------------------------------------------------------------------------

/** @param {string} id */
function dialog(id) {
  return /** @type {HTMLDialogElement} */ ($(id));
}

function wireDialogs() {
  for (const node of document.querySelectorAll("dialog [data-close]")) {
    node.addEventListener("click", () => {
      const parent = node.closest("dialog");
      if (parent) parent.close();
    });
  }

  $("open-paste").addEventListener("click", () => {
    $("paste-error").textContent = "";
    dialog("paste-dialog").showModal();
  });
  $("paste-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const title = /** @type {HTMLInputElement} */ ($("paste-name"));
    const text = /** @type {HTMLTextAreaElement} */ ($("paste-text"));
    if (text.value.trim().length < 20) {
      $("paste-error").textContent = "Paste at least a sentence or two (20+ characters).";
      return;
    }
    try {
      await api("/api/sources/text", { method: "POST", json: { title: title.value, text: text.value } });
      dialog("paste-dialog").close();
      title.value = "";
      text.value = "";
      await refreshSources();
      announce("Added pasted text.");
    } catch (error) {
      $("paste-error").textContent = messageOf(error);
    }
  });

  $("open-url").addEventListener("click", () => {
    $("url-error").textContent = "";
    dialog("url-dialog").showModal();
  });
  $("url-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = /** @type {HTMLInputElement} */ ($("url-input"));
    const submit = /** @type {HTMLButtonElement} */ (/** @type {HTMLFormElement} */ ($("url-form")).querySelector("button[type=submit]"));
    if (!/^https?:\/\/\S+$/i.test(input.value.trim())) {
      $("url-error").textContent = "Enter a full link starting with https://";
      return;
    }
    submit.disabled = true;
    submit.textContent = "Fetching…";
    try {
      await api("/api/sources/url", { method: "POST", json: { url: input.value.trim() } });
      dialog("url-dialog").close();
      input.value = "";
      await refreshSources();
      announce("Added web page.");
    } catch (error) {
      $("url-error").textContent = messageOf(error);
    } finally {
      submit.disabled = false;
      submit.textContent = "Add page";
    }
  });

  $("open-settings").addEventListener("click", () => {
    $("settings-error").textContent = "";
    dialog("settings-dialog").showModal();
  });
  $("delete-all").addEventListener("click", async () => {
    if (!window.confirm("Delete all your sources and reports? This cannot be undone.")) return;
    try {
      await api("/api/workspace", { method: "DELETE" });
      workspaceId = newWorkspaceId();
      try {
        localStorage.setItem(WS_KEY, workspaceId);
      } catch {
        /* storage blocked */
      }
      state.run = null;
      state.excluded.clear();
      window.clearTimeout(state.pollTimer);
      history.replaceState(null, "", "#");
      $("run").hidden = true;
      $("intro").hidden = false;
      dialog("settings-dialog").close();
      await Promise.all([refreshSources(), refreshHistory()]);
      announce("All your data was deleted.");
    } catch (error) {
      $("settings-error").textContent = messageOf(error);
    }
  });
}

/** @param {Config} config */
function applyConfig(config) {
  state.config = config;
  const pill = $("engine-pill");
  pill.textContent = config.llm_available ? `AI writing: ${config.llm_model}` : "Extractive mode (no AI key needed)";
  pill.classList.toggle("ai", config.llm_available);
  $("llm-toggle-wrap").hidden = !config.llm_available;
  $("open-url").hidden = !config.allow_url_fetch;
  $("settings-engine").textContent = config.llm_available
    ? `This server can write answers with ${config.llm_model}. Turn on “Write with AI model” to use it; otherwise answers quote your sources directly. Either way, every sentence is checked against the evidence it cites.`
    : "This server runs in extractive mode: answers are the best-matching sentences quoted from your sources, with citations. No AI provider or API key is involved. The server operator can connect any OpenAI-compatible model (including a local one via Ollama) to enable written answers.";
  $("settings-retention").textContent = config.retention_hours
    ? `This server deletes everything after ${plural(config.retention_hours, "hour")}.`
    : "This server keeps your data until you delete it.";
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

function wireReportActions() {
  $("copy-md").addEventListener("click", async () => {
    if (!state.run) return;
    try {
      const response = await fetch(`/api/runs/${state.run.id}/export.md`, { headers: { "X-Workspace-Id": workspaceId } });
      if (!response.ok) throw new Error("export failed");
      await navigator.clipboard.writeText(await response.text());
      announce("Report copied as Markdown.");
    } catch {
      announce("Could not copy to the clipboard. Use Download .md instead.", true);
    }
  });
  $("dl-md").addEventListener("click", () => {
    if (state.run) downloadExport(`/api/runs/${state.run.id}/export.md`, "sourcebound-report.md").catch((e) => announce(messageOf(e), true));
  });
  $("dl-json").addEventListener("click", () => {
    if (state.run) downloadExport(`/api/runs/${state.run.id}/export.json`, "sourcebound-report.json").catch((e) => announce(messageOf(e), true));
  });
  $("delete-run").addEventListener("click", async () => {
    if (!state.run || !window.confirm("Delete this report?")) return;
    try {
      await api(`/api/runs/${state.run.id}`, { method: "DELETE" });
      state.run = null;
      history.replaceState(null, "", "#");
      $("run").hidden = true;
      $("intro").hidden = false;
      closeDrawer();
      await refreshHistory();
      renderScope();
      announce("Report deleted.");
    } catch (error) {
      announce(messageOf(error), true);
    }
  });
  $("close-drawer").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDrawer();
  });
}

function wireLibrary() {
  const input = /** @type {HTMLInputElement} */ ($("file-input"));
  input.addEventListener("change", () => {
    if (input.files && input.files.length) uploadFiles(Array.from(input.files)).finally(() => (input.value = ""));
  });
  $("load-sample").addEventListener("click", loadSample);

  const library = $("library");
  let depth = 0;
  library.addEventListener("dragenter", (event) => {
    event.preventDefault();
    depth += 1;
    library.classList.add("dragging");
  });
  library.addEventListener("dragover", (event) => event.preventDefault());
  library.addEventListener("dragleave", () => {
    depth = Math.max(0, depth - 1);
    if (!depth) library.classList.remove("dragging");
  });
  library.addEventListener("drop", (event) => {
    event.preventDefault();
    depth = 0;
    library.classList.remove("dragging");
    const files = event.dataTransfer ? event.dataTransfer.files : null;
    if (files && files.length) uploadFiles(Array.from(files));
  });
}

async function boot() {
  wireLibrary();
  wireDialogs();
  wireReportActions();
  /** @type {HTMLFormElement} */ ($("composer")).addEventListener("submit", submitQuestion);
  const field = /** @type {HTMLTextAreaElement} */ ($("question"));
  field.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      /** @type {HTMLFormElement} */ ($("composer")).requestSubmit();
    }
  });
  try {
    applyConfig(await api("/api/config"));
    await Promise.all([refreshSources(), refreshHistory()]);
  } catch (error) {
    announce(messageOf(error), true);
    return;
  }
  const match = location.hash.match(/^#run=([a-f0-9]{32})$/);
  if (match) await openRun(match[1]);
}

boot();

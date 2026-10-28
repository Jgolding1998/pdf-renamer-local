"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const limits = JSON.parse(document.body.dataset.limits);
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const PREFIX_RE = /^[A-Za-z0-9_-]{0,32}$/;

  const form = $("rename-form");
  const input = $("files");
  const drop = $("drop");
  const prefix = $("prefix");
  const previewBtn = $("preview");
  const downloadBtn = $("download");
  const status = $("status");

  let files = [];
  let plan = null;
  let busy = false;
  let generation = 0;

  const formatSize = (bytes) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  };

  const mode = () => form.querySelector('input[name="mode"]:checked').value;

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  function setStatus(message, kind = "") {
    status.textContent = message;
    status.dataset.kind = kind;
  }

  function clientProblem() {
    if (!PREFIX_RE.test(prefix.value)) return "Fix the prefix first.";
    if (files.length === 0) return "Choose at least one PDF.";
    if (files.length > limits.maxFiles) return `Too many files. The limit is ${limits.maxFiles}.`;
    const total = files.reduce((sum, f) => sum + f.size, 0);
    if (total > limits.maxTotalBytes) return `Total size ${formatSize(total)} is over the ${formatSize(limits.maxTotalBytes)} limit.`;
    return "";
  }

  function invalidate() {
    generation += 1;
    if (plan) $("stale").hidden = false;
    plan = null;
    refresh();
  }

  function refresh() {
    const valid = PREFIX_RE.test(prefix.value);
    prefix.setAttribute("aria-invalid", String(!valid));
    $("prefix-error").hidden = valid;
    const sample = mode() === "invoice" ? "INV-20931" : "C10042";
    $("example").textContent = `${valid ? prefix.value : ""}${sample}.pdf`;

    previewBtn.disabled = busy || files.length === 0;
    downloadBtn.disabled = busy || !plan || plan.summary.renamed === 0;
    form.setAttribute("aria-busy", String(busy));
    prefix.disabled = busy;
    input.disabled = busy;
    $("clear").disabled = busy;
    form.querySelectorAll('input[name="mode"]').forEach((radio) => { radio.disabled = busy; });
    drop.setAttribute("aria-disabled", String(busy));
    renderFiles();
  }

  function renderFiles() {
    const list = $("file-list");
    list.replaceChildren();
    $("selection").hidden = files.length === 0;
    files.forEach((file, index) => {
      const item = el("li", "file-item");
      const name = el("span", "file-name", file.name);
      const size = el("span", "file-size", formatSize(file.size));
      if (file.size > limits.maxFileBytes) size.classList.add("over");
      const remove = el("button", "icon", "Remove");
      remove.type = "button";
      remove.disabled = busy;
      remove.setAttribute("aria-label", `Remove ${file.name}`);
      remove.addEventListener("click", () => {
        if (busy) return;
        files.splice(index, 1);
        invalidate();
        setStatus(`Removed ${file.name}.`);
      });
      item.append(name, size, remove);
      list.append(item);
    });
    const total = files.reduce((sum, f) => sum + f.size, 0);
    $("selection-title").textContent = `Selected files (${files.length}, ${formatSize(total)})`;
  }

  function addFiles(list) {
    if (busy) return;
    const incoming = Array.from(list);
    files = files.concat(incoming);
    invalidate();
    const problem = clientProblem();
    setStatus(problem || `${incoming.length} file(s) added. Preview to see the new names.`, problem ? "error" : "");
  }

  function payload(withPlan) {
    const data = new FormData();
    data.append("mode", mode());
    data.append("prefix", prefix.value);
    if (withPlan) data.append("plan_id", plan.plan_id);
    files.forEach((file) => data.append("files", file, file.name));
    return data;
  }

  function selectionSignature() {
    return JSON.stringify([mode(), prefix.value, files.map((file) => [file.name, file.size, file.lastModified])]);
  }

  async function send(url, body) {
    const response = await fetch(url, {
      method: "POST",
      body,
      credentials: "same-origin",
      headers: { "X-CSRF-Token": csrf },
    });
    if (!response.ok) {
      let detail = `Request failed (${response.status}).`;
      try {
        const json = await response.json();
        if (typeof json.detail === "string") detail = json.detail;
      } catch (_) { /* non-JSON error body */ }
      throw new Error(detail);
    }
    return response;
  }

  function renderPlan(result) {
    const rows = $("plan-rows");
    rows.replaceChildren();
    result.items.forEach((item) => {
      const tr = el("tr", item.status === "ok" ? "ok" : "bad");
      const num = el("td", "num", String(item.index + 1));
      num.dataset.label = "#";
      const original = el("td", "orig", item.original);
      original.dataset.label = "Original";
      const state = el("td");
      state.dataset.label = "Result";
      state.append(el("span", `badge ${item.status === "ok" ? "badge-ok" : "badge-bad"}`,
        item.status === "ok" ? (item.renamed_duplicate ? "Renamed (duplicate)" : "Renamed") : "Not renamed"));
      const detail = el("td", item.status === "ok" ? "new" : "reason");
      detail.dataset.label = item.status === "ok" ? "New name" : "Reason";
      if (item.status === "ok") detail.append(el("code", "", item.new_name));
      else detail.textContent = item.message;
      tr.append(num, original, state, detail);
      rows.append(tr);
    });
    const { renamed, failed, total } = result.summary;
    $("summary").textContent = `${renamed} of ${total} renamed${failed ? `, ${failed} need attention` : ""}`;
    $("stale").hidden = true;
    $("results").hidden = false;
  }

  async function runPreview() {
    if (busy) return;
    const problem = clientProblem();
    if (problem) {
      setStatus(problem, "error");
      return;
    }
    busy = true;
    const requestedGeneration = ++generation;
    const requestedSelection = selectionSignature();
    refresh();
    setStatus(`Reading ${files.length} file(s) locally...`);
    try {
      const response = await send("/api/preview", payload(false));
      const result = await response.json();
      if (requestedGeneration !== generation || requestedSelection !== selectionSignature()) {
        throw new Error("Files or options changed during the preview. Preview again.");
      }
      plan = result;
      renderPlan(plan);
      const { renamed, failed } = plan.summary;
      setStatus(renamed
        ? `Preview ready: ${renamed} file(s) will be renamed${failed ? `, ${failed} skipped` : ""}.`
        : "No file could be renamed. See the reasons in the table.", renamed ? "ok" : "error");
    } catch (err) {
      plan = null;
      setStatus(err.message, "error");
    } finally {
      busy = false;
      refresh();
    }
  }

  async function runDownload() {
    if (busy || !plan) return;
    const requestedPlan = plan;
    const requestedGeneration = generation;
    const requestedSelection = selectionSignature();
    busy = true;
    refresh();
    setStatus("Building ZIP...");
    try {
      const response = await send("/api/zip", payload(true));
      if (response.headers.get("x-plan-id") !== requestedPlan.plan_id) throw new Error("The ZIP did not match the preview. Preview again.");
      const blob = await response.blob();
      if (requestedGeneration !== generation || requestedSelection !== selectionSignature()) {
        throw new Error("Files or options changed during the download. Preview again.");
      }
      const url = URL.createObjectURL(blob);
      const link = el("a");
      link.href = url;
      link.download = "renamed-pdfs.zip";
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setStatus(`Downloaded renamed-pdfs.zip with ${requestedPlan.summary.renamed} file(s).`, "ok");
    } catch (err) {
      setStatus(err.message, "error");
      if (/changed|match/i.test(err.message)) invalidate();
    } finally {
      busy = false;
      refresh();
    }
  }

  input.addEventListener("change", () => {
    if (busy) { input.value = ""; return; }
    if (input.files.length) addFiles(input.files);
    input.value = "";
  });
  ["dragenter", "dragover"].forEach((type) => drop.addEventListener(type, (event) => {
    event.preventDefault();
    if (!busy) drop.classList.add("over");
  }));
  ["dragleave", "drop"].forEach((type) => drop.addEventListener(type, () => drop.classList.remove("over")));
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    if (!busy && event.dataTransfer.files.length) addFiles(event.dataTransfer.files);
  });
  prefix.addEventListener("input", invalidate);
  form.querySelectorAll('input[name="mode"]').forEach((radio) => radio.addEventListener("change", invalidate));
  $("clear").addEventListener("click", () => {
    if (busy) return;
    generation += 1;
    files = [];
    plan = null;
    $("results").hidden = true;
    refresh();
    setStatus("Selection cleared.");
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!busy) runPreview();
  });
  downloadBtn.addEventListener("click", () => { if (!busy) runDownload(); });

  refresh();
})();

/* ValidSim Dashboard v0 — SPA logic (vanilla JS, no build step).
 *
 * Talks to the existing FastAPI app:
 *   POST /api/v1/validations            run a fresh validation
 *   GET  /api/v1/validations/{id}/scorecard  reload a historical scorecard
 *   GET  /api/v1/dashboard/history      newest-first run list
 *   GET  /api/v1/dashboard/summary      gate KPI counts
 *
 * Chart.js is loaded from a CDN; if it is missing (offline) or its init
 * throws, the failure taxonomy degrades to the HTML table, which doubles
 * as the accessible alternative for the canvas.
 */
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);

  const els = {
    form: $("run-form"),
    runBtn: $("run-btn"),
    runBtnLabel: $("run-btn-label"),
    status: $("run-status"),
    runMeta: $("scorecard-run-meta"),
    composite: $("sc-composite"),
    gate: $("sc-gate"),
    verdict: $("sc-verdict"),
    success: $("m-success"),
    safety: $("m-safety"),
    robustness: $("m-robustness"),
    regression: $("m-regression"),
    ci: $("m-ci"),
    episodes: $("m-episodes"),
    duration: $("m-duration"),
    chartWrap: $("taxonomy-chart-wrap"),
    chartCanvas: $("taxonomy-chart"),
    taxonomyTable: $("taxonomy-table"),
    taxonomyBody: $("taxonomy-tbody"),
    taxonomyMeta: $("taxonomy-meta"),
    taxonomyEmpty: $("taxonomy-empty"),
    historyBody: $("history-tbody"),
    historyStats: $("history-stats"),
  };

  /* Inline SVG icons (Heroicons outline, 24x24) — no emoji anywhere. */
  const ICONS = {
    approve:
      '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true" focusable="false"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"/></svg>',
    block:
      '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true" focusable="false"><path stroke-linecap="round" stroke-linejoin="round" d="m9.75 9.75 4.5 4.5m0-4.5-4.5 4.5M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"/></svg>',
    pending:
      '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true" focusable="false"><path stroke-linecap="round" stroke-linejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"/></svg>',
  };

  let chart = null; // current Chart.js instance (destroyed before redraw)
  let currentRunId = null;

  const reducedMotion = () =>
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const pct = (x, digits = 1) => (x * 100).toFixed(digits) + "%";

  function setStatus(message, kind) {
    els.status.textContent = message;
    els.status.className = "run-status" + (kind ? " is-" + kind : "");
  }

  async function fetchJSON(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) {
      let detail = "HTTP " + response.status;
      try {
        const data = await response.json();
        if (data && data.detail) {
          detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
        }
      } catch (ignored) { /* non-JSON error body */ }
      throw new Error(detail);
    }
    return response.json();
  }

  /* ------------------------------------------------------------------ */
  /* Scorecard rendering                                                  */
  /* ------------------------------------------------------------------ */

  function renderVerdict(decision) {
    const safe = decision === "APPROVE" || decision === "BLOCK" ? decision : "PENDING";
    const cls = safe === "APPROVE" ? "verdict--approve" : safe === "BLOCK" ? "verdict--block" : "verdict--pending";
    const icon = safe === "APPROVE" ? ICONS.approve : safe === "BLOCK" ? ICONS.block : ICONS.pending;
    els.verdict.className = "verdict " + cls;
    els.verdict.innerHTML = icon + "<span>" + safe + "</span>";
    return safe;
  }

  function renderScorecard(card, durationLabel) {
    els.composite.textContent = Number(card.composite_score).toFixed(2);
    els.gate.textContent = "gate \u2265 " + Number(card.threshold).toFixed(1);
    const decision = renderVerdict(card.deploy_decision);

    els.success.textContent = pct(Number(card.success_rate));
    els.safety.textContent = Number(card.safety_score).toFixed(1) + " / 100";
    els.robustness.textContent = Number(card.robustness_score).toFixed(1) + " / 100";

    if (card.regression_delta === null || card.regression_delta === undefined) {
      els.regression.textContent = "no baseline";
      els.regression.className = "metric-value";
    } else {
      const pp = Number(card.regression_delta) * 100;
      els.regression.textContent = (pp >= 0 ? "+" : "\u2212") + Math.abs(pp).toFixed(1) + " pp";
      els.regression.className = "metric-value " + (pp >= 0 ? "positive" : "negative");
    }

    els.ci.textContent = Array.isArray(card.confidence_interval) && card.confidence_interval.length === 2
      ? pct(card.confidence_interval[0]) + " \u2013 " + pct(card.confidence_interval[1])
      : "\u2014";
    els.episodes.textContent = String(card.episode_count);
    els.duration.textContent = durationLabel || "\u2014";
    els.runMeta.textContent =
      card.run_id + " \u00b7 " + card.checkpoint_id + " \u00b7 " + card.task_id + " \u00b7 " + card.created_at;

    currentRunId = card.run_id;
    renderTaxonomy(card.failure_taxonomy || {});
    highlightHistoryRow(currentRunId);
    return decision;
  }

  /* ------------------------------------------------------------------ */
  /* Failure taxonomy: Chart.js horizontal bars + table fallback          */
  /* ------------------------------------------------------------------ */

  function chartJsAvailable() {
    // window.__vsChartJsFailed is set by the CDN <script onerror=...> tag
    // when the network blocks the download; Chart is undefined likewise.
    return typeof window.Chart !== "undefined" && !window.__vsChartJsFailed;
  }

  function useTableFallback() {
    if (chart) { chart.destroy(); chart = null; }
    els.chartWrap.hidden = true;
    els.taxonomyTable.classList.remove("is-visually-hidden"); // table fully visible
  }

  function drawChart(entries) {
    if (!chartJsAvailable()) { useTableFallback(); return; }
    if (chart) { chart.destroy(); chart = null; }
    try {
      els.chartWrap.hidden = false;
      // Keep the table in the DOM but visually hidden: it remains the
      // screen-reader/accessible alternative to the canvas.
      els.taxonomyTable.classList.add("is-visually-hidden");
      chart = new window.Chart(els.chartCanvas, {
        type: "bar",
        data: {
          labels: entries.map(([mode]) => mode.replaceAll("_", " ")),
          datasets: [{
            label: "Failed episodes",
            data: entries.map(([, count]) => count),
            backgroundColor: "#3B82F6",
            hoverBackgroundColor: "#F59E0B",
            borderRadius: 4,
            maxBarThickness: 26,
          }],
        },
        options: {
          indexAxis: "y", // horizontal bars
          responsive: true,
          maintainAspectRatio: false,
          animation: reducedMotion() ? false : { duration: 200 },
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: "#0B1220",
              borderColor: "#2B3B55",
              borderWidth: 1,
              titleColor: "#E8EEF9",
              bodyColor: "#A7B6CE",
            },
          },
          scales: {
            x: {
              beginAtZero: true,
              ticks: { color: "#A7B6CE", precision: 0 },
              grid: { color: "rgba(148, 163, 184, 0.12)" },
            },
            y: {
              ticks: { color: "#E8EEF9" },
              grid: { display: false },
            },
          },
        },
      });
    } catch (err) {
      // Chart.js loaded but failed to initialise (e.g. canvas issues):
      // degrade to the table instead of leaving a blank panel.
      useTableFallback();
    }
  }

  function renderTaxonomy(taxonomy) {
    if (chart) { chart.destroy(); chart = null; }
    els.taxonomyBody.textContent = "";
    const entries = Object.entries(taxonomy)
      .filter(([, n]) => Number(n) > 0)
      .sort((a, b) => b[1] - a[1]);

    if (entries.length === 0) {
      els.taxonomyEmpty.hidden = false;
      els.chartWrap.hidden = true;
      els.taxonomyTable.classList.add("is-visually-hidden");
      els.taxonomyMeta.textContent = "";
      return;
    }

    els.taxonomyEmpty.hidden = true;
    const total = entries.reduce((sum, [, n]) => sum + n, 0);
    els.taxonomyMeta.textContent = total + " failed episodes \u00b7 " + entries.length + " modes";

    for (const [mode, count] of entries) {
      const tr = document.createElement("tr");
      const name = document.createElement("td");
      name.textContent = mode.replaceAll("_", " ");
      const num = document.createElement("td");
      num.className = "num";
      num.textContent = String(count);
      const share = document.createElement("td");
      share.className = "num";
      share.textContent = pct(count / total);
      tr.append(name, num, share);
      els.taxonomyBody.appendChild(tr);
    }
    drawChart(entries);
  }

  /* ------------------------------------------------------------------ */
  /* History + summary                                                    */
  /* ------------------------------------------------------------------ */

  function renderStats(summary) {
    const avg = (summary.avg_composite === null || summary.avg_composite === undefined)
      ? "\u2014"
      : Number(summary.avg_composite).toFixed(2);
    els.historyStats.textContent =
      "Runs " + summary.total_runs +
      " \u00b7 Approved " + summary.approvals +
      " \u00b7 Blocked " + summary.blocks +
      " \u00b7 Avg composite " + avg;
  }

  function decisionBadge(decision) {
    const safe = decision === "APPROVE" || decision === "BLOCK" ? decision : "PENDING";
    const span = document.createElement("span");
    span.className = "badge-sm badge-sm--" + safe.toLowerCase();
    span.innerHTML = (safe === "APPROVE" ? ICONS.approve : safe === "BLOCK" ? ICONS.block : ICONS.pending) +
      "<span>" + safe + "</span>";
    return span;
  }

  function highlightHistoryRow(runId) {
    for (const tr of els.historyBody.querySelectorAll("tr[data-run-id]")) {
      tr.classList.toggle("is-active", tr.dataset.runId === runId);
    }
  }

  function renderHistory(rows) {
    els.historyBody.textContent = "";
    if (!rows.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 6;
      td.className = "row-empty";
      td.textContent = "No runs yet — submit a checkpoint above.";
      tr.appendChild(td);
      els.historyBody.appendChild(tr);
      return;
    }
    for (const row of rows) {
      const tr = document.createElement("tr");
      tr.tabIndex = 0;
      tr.dataset.runId = row.run_id;
      tr.setAttribute("aria-label",
        "Load scorecard for run " + row.run_id +
        ", checkpoint " + row.checkpoint_id +
        ", composite " + row.composite_score +
        ", decision " + row.deploy_decision);

      const run = document.createElement("td");
      run.className = "cell-run";
      run.textContent = row.run_id;

      const ckpt = document.createElement("td");
      ckpt.textContent = row.checkpoint_id;

      const task = document.createElement("td");
      task.className = "cell-task";
      task.textContent = row.task_id;

      const comp = document.createElement("td");
      comp.className = "num";
      comp.textContent = Number(row.composite_score).toFixed(2);

      const decision = document.createElement("td");
      decision.appendChild(decisionBadge(row.deploy_decision));

      const created = document.createElement("td");
      created.className = "cell-created";
      created.textContent = String(row.created_at).replace("T", " ").replace("+00:00", "");

      tr.append(run, ckpt, task, comp, decision, created);
      tr.addEventListener("click", () => loadRun(row.run_id));
      tr.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          loadRun(row.run_id);
        }
      });
      els.historyBody.appendChild(tr);
    }
    highlightHistoryRow(currentRunId);
  }

  async function refreshPanels() {
    try {
      const [rows, summary] = await Promise.all([
        fetchJSON("/api/v1/dashboard/history"),
        fetchJSON("/api/v1/dashboard/summary"),
      ]);
      renderHistory(rows);
      renderStats(summary);
    } catch (err) {
      els.historyStats.textContent = "History unavailable: " + err.message;
    }
  }

  async function loadRun(runId) {
    try {
      setStatus("Loading scorecard for " + runId + " \u2026", "busy");
      const card = await fetchJSON("/api/v1/validations/" + encodeURIComponent(runId) + "/scorecard");
      const decision = renderScorecard(card, null); // duration unknown from history
      setStatus(
        "Loaded " + runId + " \u2014 composite " + Number(card.composite_score).toFixed(2) +
        ", decision " + decision + ".",
        decision === "APPROVE" ? "ok" : "block");
    } catch (err) {
      setStatus("Failed to load run " + runId + ": " + err.message, "error");
    }
  }

  /* ------------------------------------------------------------------ */
  /* Run form                                                             */
  /* ------------------------------------------------------------------ */

  function setRunning(isRunning) {
    els.runBtn.disabled = isRunning; // loading-buttons rule: disable while running
    els.runBtn.classList.toggle("is-loading", isRunning);
    els.runBtn.setAttribute("aria-busy", String(isRunning));
    els.runBtnLabel.textContent = isRunning ? "Running\u2026" : "Run Validation";
    for (const input of els.form.querySelectorAll("input")) input.disabled = isRunning;
  }

  els.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!els.form.reportValidity()) return; // native 100–5000 / required checks

    const checkpoint = els.form.elements.checkpoint_id.value.trim();
    const task = els.form.elements.task_id.value.trim();
    const episodes = Number(els.form.elements.episodes.value);
    const threshold = Number(els.form.elements.threshold.value);

    const body = {
      checkpoint_id: checkpoint,
      // Forward-compatible gate knob; the v0 API ignores unknown fields.
      threshold,
      task: {
        task_id: task,
        robot: { name: "franka_panda" },
        environment: { name: "kitchen" },
        episodes,
        randomization: "full",
        adversarial_count: Math.min(1000, Math.max(10, Math.round(episodes * 0.1))),
      },
    };

    setRunning(true);
    setStatus("Running " + episodes + " episodes for " + checkpoint + " \u2026", "busy");
    const startedAt = performance.now();
    try {
      const card = await fetchJSON("/api/v1/validations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const seconds = (performance.now() - startedAt) / 1000;
      const decision = renderScorecard(card, seconds.toFixed(1) + " s");
      setStatus(
        "Run " + card.run_id + " complete \u2014 composite " + Number(card.composite_score).toFixed(2) +
        ", decision " + decision + ".",
        decision === "APPROVE" ? "ok" : "block");
      await refreshPanels();
    } catch (err) {
      setStatus("Validation failed: " + err.message, "error");
    } finally {
      setRunning(false);
    }
  });

  /* ------------------------------------------------------------------ */

  refreshPanels();
})();

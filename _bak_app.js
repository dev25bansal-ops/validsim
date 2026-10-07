/* ValidSim Dashboard v0 — SPA logic (vanilla JS, no build step).
 *
 * Talks to the existing FastAPI app:
 *   GET  /api/v1/health                        public probe (reports auth_enabled)
 *   POST /api/v1/validations                   run a fresh validation
 *   GET  /api/v1/validations/{id}/scorecard    reload a historical scorecard
 *   GET  /api/v1/dashboard/history             newest-first run list
 *   GET  /api/v1/dashboard/summary             gate KPI counts
 *   GET  /api/v1/jobs                          job queue (polled every 3s)
 *
 * This file is DOM wiring only. Every formatting and coercion decision lives in
 * view-core.js as a pure function over the API's JSON, so what an operator
 * actually sees is unit-testable without a browser (tests/test_web_*
 * _agent.py runs that same file under Node). If view-core.js fails to load
 * there is nothing left that can format a scorecard, so the page shows a fatal
 * panel rather than throwing.
 *
 * Chart.js is loaded from a CDN; if it is missing (offline) or its init throws,
 * the failure taxonomy degrades to the HTML table, which doubles as the
 * accessible alternative for the canvas.
 *
 * Accessibility contract (WCAG 2.1 AA):
 *   - every dynamic message goes through a polite live region, never a
 *     3-second poll (see #a11y-announcer and the note on #jobs-meta);
 *   - row actions are native <button>s, so name/role/value and Enter/Space
 *     come for free and focus is never lost;
 *   - the taxonomy table is always in the accessibility tree, and can be
 *     revealed for everyone with the "Show data table" button;
 *   - async buttons follow the APG loading-button pattern: the control is NEVER
 *     disabled while its own request is in flight. A disabled control that
 *     holds focus drops it to <body>, throwing keyboard users to the top of the
 *     page (WCAG 2.4.3). Instead the button takes aria-busy="true" and a
 *     re-entrant submit is ignored rather than queued;
 *   - no verdict, badge or metric is signalled by colour alone: each pairs its
 *     hue with an inline icon and a literal word (WCAG 1.4.1), matching the PDF
 *     exporter's colour+text verdict banner.
 */
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const V = window.ValidSimView;

  const els = {
    fatal: $("app-fatal"),
    fatalDetail: $("app-fatal-detail"),
    body: $("app-body"),
    authPanel: $("auth-panel"),
    authDetail: $("auth-detail"),
    authForm: $("auth-form"),
    authBtn: $("auth-btn"),
    authBtnLabel: $("auth-btn-label"),
    authStatus: $("auth-status"),
    apiKey: $("api-key"),
    main: $("main-content"),
    skipLink: document.querySelector(".skip-link"),
    announcer: $("a11y-announcer"),
    form: $("run-form"),
    runBtn: $("run-btn"),
    runBtnLabel: $("run-btn-label"),
    status: $("run-status"),
    runMeta: $("scorecard-run-meta"),
    blockReasons: $("block-reasons"),
    blockReasonsCount: $("block-reasons-count"),
    blockReasonsList: $("block-reasons-list"),
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
    adversarialCount: $("m-adversarial-count"),
    adversarialRate: $("m-adversarial-rate"),
    chartWrap: $("taxonomy-chart-wrap"),
    chartCanvas: $("taxonomy-chart"),
    taxonomyTable: $("taxonomy-table"),
    taxonomyCaption: $("taxonomy-caption"),
    taxonomyNote: $("taxonomy-note"),
    taxonomyToggle: $("taxonomy-table-toggle"),
    taxonomyBody: $("taxonomy-tbody"),
    taxonomyMeta: $("taxonomy-meta"),
    taxonomyEmpty: $("taxonomy-empty"),
    historyBody: $("history-tbody"),
    historyStats: $("history-stats"),
    trendsMeta: $("trends-meta"),
    trendLatest: $("t-latest"),
    trendDelta: $("t-delta"),
    trendMa3: $("t-ma3"),
    trendApproval: $("t-approval"),
    enqueueForm: $("enqueue-form"),
    enqueueBtn: $("enqueue-btn"),
    enqueueBtnLabel: $("enqueue-btn-label"),
    enqueueStatus: $("enqueue-status"),
    jobsBody: $("jobs-tbody"),
    jobsMeta: $("jobs-meta"),
    themeToggle: $("theme-toggle"),
    themeToggleText: $("theme-toggle-text"),
  };

  /* ------------------------------------------------------------------ */
  /* Fatal bootstrap failure                                            */
  /* ------------------------------------------------------------------ */
  /* view-core.js is a separate <script>. If the static mount 404s it, or it
   * is edited into a syntax error, no formatter survives and every render path
   * would throw on first use. Say so plainly and stop. */
  function fatal(message) {
    if (els.fatalDetail) els.fatalDetail.textContent = message;
    if (els.fatal) els.fatal.hidden = false;
    if (els.body) els.body.hidden = true;
    if (els.authPanel) els.authPanel.hidden = true;
  }

  if (window.__vsViewCoreFailed || !V || typeof V.scorecardView !== "function") {
    fatal(
      "The dashboard's presentation module (view-core.js) failed to load, so scorecards cannot " +
      "be rendered. Reload the page; if it persists, that asset is missing from the deployment."
    );
    return;
  }

  /* Inline SVG icons (Heroicons outline, 24x24) — no emoji anywhere.
     All are aria-hidden: the adjacent literal status word carries the meaning,
     so colour and shape are never the only signal (WCAG 1.4.1). */
  const ICONS = {
    approve:
      '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true" focusable="false"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"/></svg>',
    block:
      '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true" focusable="false"><path stroke-linecap="round" stroke-linejoin="round" d="m9.75 9.75 4.5 4.5m0-4.5-4.5 4.5M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"/></svg>',
    pending:
      '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true" focusable="false"><path stroke-linecap="round" stroke-linejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"/></svg>',
    trendUp:
      '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true" focusable="false"><path stroke-linecap="round" stroke-linejoin="round" d="M2.25 18 9 11.25l4.306 4.306a11.95 11.95 0 0 1 5.814-5.518l2.74-1.22m0 0-5.94-2.281m5.94 2.28-2.28 5.941"/></svg>',
    trendDown:
      '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true" focusable="false"><path stroke-linecap="round" stroke-linejoin="round" d="M2.25 6 9 12.75l4.286-4.286a11.948 11.948 0 0 1 4.306 6.43l.776 2.898m0 0 3.182-5.511m-3.182 5.51-5.511-3.181"/></svg>',
    trendFlat:
      '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true" focusable="false"><path stroke-linecap="round" stroke-linejoin="round" d="M5 12h14"/></svg>',
    queued:
      '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true" focusable="false"><path stroke-linecap="round" stroke-linejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"/></svg>',
    running:
      '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true" focusable="false"><path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99"/></svg>',
  };

  let chart = null; // current Chart.js instance (destroyed before redraw)
  let currentRunId = null;
  let lastTaxonomyEntries = null; // re-drawn when the theme flips

  const reducedMotion = () =>
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ------------------------------------------------------------------ */
  /* Theme control (light / dark)                                        */
  /* ------------------------------------------------------------------ */
  // The <head> inline script already set data-theme before first paint, so
  // there is no flash-of-wrong-theme. Here we only (a) sync the toggle button
  // to that state, (b) persist explicit choices, and (c) follow the OS while
  // the user has not chosen explicitly.
  const THEME_KEY = "vs-theme";

  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  }

  function readStoredTheme() {
    try {
      const v = localStorage.getItem(THEME_KEY);
      return v === "light" || v === "dark" ? v : null;
    } catch (e) {
      return null; // private mode / storage disabled: treat as "no choice"
    }
  }

  function storeTheme(theme) {
    try { localStorage.setItem(THEME_KEY, theme); } catch (e) { /* best effort */ }
  }

  // Keep the toggle's pressed state and accessible name aligned with the
  // active theme. aria-label describes the ACTION (what activating will do),
  // aria-pressed reflects the state (true == light theme is on).
  function syncThemeButton() {
    const light = currentTheme() === "light";
    if (els.themeToggle) {
      els.themeToggle.setAttribute("aria-pressed", String(light));
      els.themeToggle.setAttribute("aria-label", light ? "Switch to dark theme" : "Switch to light theme");
    }
    if (els.themeToggleText) els.themeToggleText.textContent = light ? "Light" : "Dark";
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    syncThemeButton();
    // Re-render the chart so its colours match the new surface.
    if (chartDrawn && lastTaxonomyEntries) drawChart(lastTaxonomyEntries);
  }

  if (els.themeToggle) {
    els.themeToggle.addEventListener("click", () => {
      const next = currentTheme() === "light" ? "dark" : "light";
      storeTheme(next);
      applyTheme(next);
      announce(next === "light" ? "Light theme enabled." : "Dark theme enabled.");
    });
  }

  // Follow the OS only while the user has not made an explicit choice.
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onSchemeChange = (event) => {
      if (readStoredTheme() !== null) return; // explicit choice wins
      applyTheme(event.matches ? "dark" : "light");
    };
    if (typeof mq.addEventListener === "function") mq.addEventListener("change", onSchemeChange);
    else if (typeof mq.addListener === "function") mq.addListener(onSchemeChange); // legacy Safari
  }

  // Align the button with whatever the head script resolved at load time.
  syncThemeButton();

  /* ------------------------------------------------------------------ */
  /* Live-region helpers                                                  */
  /* ------------------------------------------------------------------ */

  // Single polite announcer for events that have no dedicated status element.
  // Clearing first guarantees identical consecutive messages are re-announced.
  let announceTimer = null;
  function announce(message) {
    if (!els.announcer || !message) return;
    els.announcer.textContent = "";
    if (announceTimer !== null) clearTimeout(announceTimer);
    announceTimer = setTimeout(() => {
      els.announcer.textContent = message;
      announceTimer = null;
    }, 80);
  }

  function setStatus(message, kind) {
    els.status.textContent = message;
    els.status.className = "run-status" + (kind ? " is-" + kind : "");
  }

  /* ------------------------------------------------------------------ */
  /* API key: header injection, session storage, and 401 degradation      */
  /* ------------------------------------------------------------------ */
  /* With VALIDSIM_API_KEY configured, the API's router-level dependency covers
   * every /api/v1 route and the document route at "/" is registered on the
   * same app, so an anonymous browser receives {"detail":"Missing or invalid API
   * key"} as application/json — the dashboard never loads. That is a real
   * deployment state, so the UI absorbs it:
   *   - /api/v1/health is in PUBLIC_PATHS and reports `auth_enabled`, which is
   *     how the client tells "this deployment wants a key" apart from "the API
   *     is down";
   *   - a 401 on any dashboard request raises AuthRequired, which swaps the body
   *     for the auth panel — never for the raw JSON body;
   *   - the key lives in sessionStorage only (gone when the tab closes; never a
   *     cookie, never a URL query parameter) and rides in the X-API-Key header. */
  const API_KEY_HEADER = "X-API-Key";
  const API_KEY_STORE = "vs-api-key";

  class AuthRequired extends Error {
    constructor() {
      super("This deployment requires an API key.");
      this.name = "AuthRequired";
    }
  }

  function readApiKey() {
    try {
      return sessionStorage.getItem(API_KEY_STORE) || "";
    } catch (e) {
      return ""; // storage disabled (private mode / policy): treat as "no key"
    }
  }

  function writeApiKey(value) {
    try {
      if (value) sessionStorage.setItem(API_KEY_STORE, value);
      else sessionStorage.removeItem(API_KEY_STORE);
    } catch (e) { /* best effort — the key still works for this page load */ }
  }

  function authHeaders() {
    const key = readApiKey();
    return key ? { [API_KEY_HEADER]: key } : {};
  }

  /* Best-effort policy probe. The health route is deliberately reachable
   * without a credential (a container probe has none to present), so this
   * answers even on a fully locked-down deployment. */
  async function probeAuthPolicy() {
    try {
      const response = await fetch("/api/v1/health", { headers: authHeaders() });
      if (!response.ok) return null;
      const data = await response.json();
      return data && typeof data === "object" ? data : null;
    } catch (err) {
      return null; // offline, or not a ValidSim backend
    }
  }

  function showAuthStatus(message, kind) {
    if (!els.authStatus) return;
    els.authStatus.hidden = false;
    els.authStatus.textContent = message;
    els.authStatus.className = "run-status" + (kind ? " is-" + kind : "");
  }

  function focusQuietly(target) {
    if (!target || !target.isConnected) return;
    try { target.focus({ preventScroll: false }); } catch (e) { /* ignore */ }
  }

  /* The single state flip. Idempotent, and callable from any request that
   * discovers the key is missing or rejected. */
  function showAuthRequired(detail) {
    stopJobsPolling();
    if (els.body) els.body.hidden = true;
    if (els.authPanel) {
      els.authPanel.hidden = false;
      if (detail && els.authDetail) els.authDetail.textContent = detail;
    }
    if (els.apiKey) els.apiKey.value = "";
    showAuthStatus("The dashboard needs a valid API key before it can load any data.", "error");
    focusQuietly(els.apiKey);
  }

  function showDashboard() {
    if (els.authPanel) els.authPanel.hidden = true;
    if (els.body) els.body.hidden = false;
  }

  async function fetchJSON(url, options) {
    const opts = Object.assign({}, options);
    opts.headers = Object.assign({}, authHeaders(), opts.headers || {});
    const response = await fetch(url, opts);
    if (response.status === 401 || response.status === 403) {
      // Discard the credential: a rejected key will not start working, and
      // leaving it would fail every subsequent request with no way forward.
      writeApiKey("");
      throw new AuthRequired();
    }
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

  function renderVerdict(decision, word) {
    const safe = V.safeDecision(decision);
    const cls = safe === "APPROVE" ? "verdict--approve" : safe === "BLOCK" ? "verdict--block" : "verdict--pending";
    const icon = safe === "APPROVE" ? ICONS.approve : safe === "BLOCK" ? ICONS.block : ICONS.pending;
    els.verdict.className = "verdict " + cls;
    // The hidden prefix turns a bare "APPROVE" into a meaningful statement.
    els.verdict.innerHTML = icon +
      '<span class="is-visually-hidden">Deploy decision:\u00a0</span>' +
      "<span>" + safe + "</span>";
    // aria-label restates the verdict as a sentence so it is intelligible out
    // of context and carries the word as well as the hue (WCAG 1.4.1).
    els.verdict.setAttribute("aria-label", "Deploy decision: " + safe + ", " + word + ".");
    return safe;
  }

  /* Renders the engine's block_reasons verbatim as <li> text nodes.
   *
   * textContent, never innerHTML: a reason may quote a caller-supplied
   * checkpoint or task id, so markup arriving in the payload has to render as
   * characters rather than become an element. */
  function renderBlockReasons(view) {
    if (!els.blockReasons) return;
    const reasons = view.blockReasons;
    if (reasons.length === 0) {
      els.blockReasons.hidden = true;
      els.blockReasonsList.textContent = "";
      els.blockReasonsCount.textContent = "";
      return;
    }
    els.blockReasonsList.textContent = "";
    for (const reason of reasons) {
      const li = document.createElement("li");
      li.className = "block-reason";
      li.textContent = reason;
      els.blockReasonsList.appendChild(li);
    }
    // Counted in words in the visible header, so it is not read as a bare digit.
    els.blockReasonsCount.textContent = reasons.length === 1
      ? "Blocked for 1 reason"
      : "Blocked for " + reasons.length + " reasons";
    els.blockReasons.hidden = false;
  }

  function renderScorecard(card, durationLabel) {
    const view = V.scorecardView(card, durationLabel);
    const decision = renderVerdict(view.decision, view.decisionWord);

    els.composite.textContent = view.composite;
    els.gate.textContent = view.gate;
    // The gate line is a terse visual label; the accessible name states the
    // comparison in words, so "gate \u2265 85.00" is not the only way to read it.
    els.gate.setAttribute("aria-label", view.gateSentence);
    els.gate.title = view.gateSentence;

    els.success.textContent = view.success;
    els.safety.textContent = view.safety;
    els.robustness.textContent = view.robustness;

    els.regression.textContent = view.regression;
    if (view.regressionHasBaseline) {
      // Signed colour only where there is a signed delta to colour; the
      // U+2212 MINUS SIGN prefix is what marks a fall.
      els.regression.className = "metric-value " +
        (view.regression.indexOf("\u2212") === 0 ? "negative" : "positive");
    } else {
      els.regression.className = "metric-value";
    }

    els.ci.textContent = view.ci;
    els.episodes.textContent = view.episodes;
    els.duration.textContent = view.duration;

    // Previously fetched by the API and silently discarded: the adversarial
    // segment is the one part of the run the engine gates separately from the
    // weighted composite, so its count and rate belong beside the verdict.
    els.adversarialCount.textContent = view.adversarialCount;
    els.adversarialRate.textContent = view.adversarialRate === null
      ? (view.adversarialText === "none" ? "no segment" : V.UNKNOWN_TEXT)
      : view.adversarialRate;
    // An absent rate is stated in words rather than left blank, keeping it
    // distinct from a segment that genuinely scored 0%.
    els.adversarialRate.className = "metric-value" +
      (view.adversarialRate === null ? " is-unmeasured" : "");

    renderBlockReasons(view);
    els.runMeta.textContent = view.meta;

    currentRunId = V.text(card && card.run_id);
    renderTaxonomy(view.taxonomyRows);
    highlightHistoryRow(currentRunId);
    return view;
  }

  /* ------------------------------------------------------------------ */
  /* Failure taxonomy: Chart.js horizontal bars + accessible table        */
  /* ------------------------------------------------------------------ */

  let chartDrawn = false;      // canvas currently on screen
  let tableRevealed = false;   // user's choice while the chart is on screen
  let fallbackAnnounced = false;

  // window.__vsChartJsFailed is set by the CDN <script onerror=...> tag
  // when the network blocks the download; Chart is undefined likewise.
  function chartJsAvailable() {
    return typeof window.Chart !== "undefined" && !window.__vsChartJsFailed;
  }

  // Toggle the table's *visual* presentation only. It is never display:none,
  // so it stays reachable to screen readers and to find-in-page while hidden.
  function setTaxonomyTableVisible(visible) {
    els.taxonomyTable.classList.toggle("is-visually-hidden", !visible);
    els.taxonomyCaption.classList.toggle("is-visually-hidden", !visible);
    if (els.taxonomyToggle) {
      els.taxonomyToggle.setAttribute("aria-expanded", String(visible));
      els.taxonomyToggle.textContent = visible ? "Hide data table" : "Show data table";
    }
  }

  function useTableFallback() {
    if (chart) { chart.destroy(); chart = null; }
    chartDrawn = false;
    els.chartWrap.hidden = true;
    setTaxonomyTableVisible(true);
    if (els.taxonomyToggle) els.taxonomyToggle.hidden = true; // table already shown
    els.taxonomyNote.textContent =
      "Chart library unavailable, so the failure taxonomy is presented as a data table.";
    if (!fallbackAnnounced) {
      fallbackAnnounced = true;
      announce("Chart unavailable. Failure taxonomy shown as a data table.");
    }
  }

  function chartDescription(entries) {
    const parts = entries.map(([mode, count]) => mode.replaceAll("_", " ") + ": " + count);
    return "Horizontal bar chart of failure modes by episode count. " + parts.join("; ") + ".";
  }

  // Chart colours are hard-coded in JS (Chart.js cannot read CSS variables),
  // so they are chosen per theme. Text/tick colours reuse the same accessible
  // tokens as the rest of the UI: light ticks #334155 on #FFFFFF = 10.3:1,
  // dark ticks #A7B6CE on #0B1220 = 9.1:1.
  function chartPalette() {
    return currentTheme() === "light"
      ? {
          bar: "#2563EB",
          barHover: "#B45309",
          tooltipBg: "#FFFFFF",
          tooltipBorder: "#A9B7CC",
          title: "#0B1220",
          body: "#334155",
          tick: "#334155",
          tickStrong: "#0B1220",
          grid: "rgba(71, 85, 105, 0.18)",
        }
      : {
          bar: "#3B82F6",
          barHover: "#F59E0B",
          tooltipBg: "#0B1220",
          tooltipBorder: "#2B3B55",
          title: "#E8EEF9",
          body: "#A7B6CE",
          tick: "#A7B6CE",
          tickStrong: "#E8EEF9",
          grid: "rgba(148, 163, 184, 0.12)",
        };
  }

  function drawChart(entries) {
    lastTaxonomyEntries = entries;
    if (!chartJsAvailable()) { useTableFallback(); return; }
    if (chart) { chart.destroy(); chart = null; }
    const pal = chartPalette();
    try {
      // Refresh the canvas text alternative with the real figures before init.
      els.chartCanvas.setAttribute("aria-label", chartDescription(entries));
      els.chartWrap.hidden = false;
      // Keep the table in the DOM but visually hidden: it remains the
      // screen-reader/accessible alternative to the canvas.
      setTaxonomyTableVisible(tableRevealed);
      if (els.taxonomyToggle) els.taxonomyToggle.hidden = false;
      els.taxonomyNote.textContent =
        "The chart duplicates the failure taxonomy data table; use the button above to show the table.";
      chart = new window.Chart(els.chartCanvas, {
        type: "bar",
        data: {
          labels: entries.map(([mode]) => mode.replaceAll("_", " ")),
          datasets: [{
            label: "Failed episodes",
            data: entries.map(([, count]) => count),
            backgroundColor: pal.bar,
            hoverBackgroundColor: pal.barHover,
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
              backgroundColor: pal.tooltipBg,
              borderColor: pal.tooltipBorder,
              borderWidth: 1,
              titleColor: pal.title,
              bodyColor: pal.body,
            },
          },
          scales: {
            x: {
              beginAtZero: true,
              ticks: { color: pal.tick, precision: 0 },
              grid: { color: pal.grid },
            },
            y: {
              ticks: { color: pal.tickStrong },
              grid: { display: false },
            },
          },
        },
      });
      chartDrawn = true;
    } catch (err) {
      // Chart.js loaded but failed to initialise (e.g. canvas issues):
      // degrade to the table instead of leaving a blank panel.
      useTableFallback();
    }
  }

  /* `entries` arrive already normalised by view-core's taxonomyEntries():
   * every count is a finite positive number, the list is sorted descending, and
   * a malformed payload yields []. That is why the chart and the table can never
   * disagree, and why neither can receive a NaN bar. */
  function renderTaxonomy(entries) {
    if (chart) { chart.destroy(); chart = null; }
    chartDrawn = false;
    els.taxonomyBody.textContent = "";

    if (entries.length === 0) {
      els.taxonomyEmpty.hidden = false;
      els.chartWrap.hidden = true;
      setTaxonomyTableVisible(false);
      if (els.taxonomyToggle) els.taxonomyToggle.hidden = true;
      els.taxonomyNote.textContent = "";
      els.taxonomyMeta.textContent = "";
      return;
    }

    els.taxonomyEmpty.hidden = true;
    const total = entries.reduce((sum, pair) => sum + pair[1], 0);
    els.taxonomyMeta.textContent = total + " failed episodes \u00b7 " + entries.length + " modes";

    for (const pair of entries) {
      const tr = document.createElement("tr");
      const name = document.createElement("td");
      name.textContent = pair[0].replaceAll("_", " ");
      const num = document.createElement("td");
      num.className = "num";
      num.textContent = String(pair[1]);
      const share = document.createElement("td");
      share.className = "num";
      share.textContent = V.pct(pair[1] / total);
      tr.append(name, num, share);
      els.taxonomyBody.appendChild(tr);
    }
    drawChart(entries);
    if (chartDrawn) fallbackAnnounced = false; // re-arm for a later degradation
  }

  if (els.taxonomyToggle) {
    els.taxonomyToggle.addEventListener("click", () => {
      tableRevealed = els.taxonomyTable.classList.contains("is-visually-hidden");
      setTaxonomyTableVisible(tableRevealed);
      announce(tableRevealed
        ? "Failure taxonomy data table shown."
        : "Failure taxonomy data table hidden; values remain available to screen readers.");
    });
  }

  /* ------------------------------------------------------------------ */
  /* History + summary                                                    */
  /* ------------------------------------------------------------------ */

  function renderStats(summary) {
    els.historyStats.textContent = V.summaryText(summary);
  }

  /* ------------------------------------------------------------------ */
  /* Trends (client-side, mirroring validsim/engine/trends.py)               */
  /* ------------------------------------------------------------------ */

  function renderTrendsEmpty(meta) {
    els.trendsMeta.textContent = meta;
    els.trendLatest.textContent = "--";
    els.trendDelta.className = "metric-value trend-delta";
    els.trendDelta.textContent = "--";
    els.trendMa3.textContent = "--";
    els.trendApproval.textContent = "--";
  }

  function renderTrends(rows) {
    // The math lives in view-core so it can be asserted against the engine's
    // trends.py in a test rather than trusted by inspection.
    const t = V.computeTrends(rows);
    if (!t) { renderTrendsEmpty("No runs yet"); return; }

    els.trendLatest.textContent = t.latestScore.toFixed(2);
    els.trendMa3.textContent = t.ma3.toFixed(2);
    els.trendApproval.textContent = V.pct(t.approvalRate);

    const dir = t.delta > 0 ? "up" : t.delta < 0 ? "down" : "flat";
    const icon = dir === "up" ? ICONS.trendUp : dir === "down" ? ICONS.trendDown : ICONS.trendFlat;
    const sign = t.delta > 0 ? "+" : t.delta < 0 ? "\u2212" : "";
    const word = dir === "up" ? "increase" : dir === "down" ? "decrease" : "no change";
    els.trendDelta.className = "metric-value trend-delta trend-delta--" + dir;
    // Direction is stated in words, not only by hue/arrow (WCAG 1.4.1).
    els.trendDelta.innerHTML = icon +
      '<span class="is-visually-hidden">' + word + " of </span>" +
      "<span>" + sign + Math.abs(t.delta).toFixed(2) + "</span>";

    els.trendsMeta.textContent =
      "Runs " + t.totalRuns +
      " \u00b7 newest " + t.newestCheckpoint +
      " \u00b7 MA over last " + t.window3Size +
      " \u00b7 approval over last " + t.window10Size;
  }

  function renderTrendsError(message) {
    renderTrendsEmpty("Trends unavailable: " + message);
  }

  function decisionBadge(decision) {
    const safe = V.safeDecision(decision);
    const span = document.createElement("span");
    span.className = "badge-sm badge-sm--" + safe.toLowerCase();
    // Icon + literal word, so the badge never relies on hue alone (WCAG 1.4.1).
    span.innerHTML = (safe === "APPROVE" ? ICONS.approve : safe === "BLOCK" ? ICONS.block : ICONS.pending) +
      "<span>" + safe + "</span>";
    span.setAttribute("aria-label", "Decision " + safe + ", " + V.decisionWord(decision) + ".");
    return span;
  }

  function highlightHistoryRow(runId) {
    for (const tr of els.historyBody.querySelectorAll("tr[data-run-id]")) {
      const active = tr.dataset.runId === runId;
      tr.classList.toggle("is-active", active);
      if (active) tr.setAttribute("aria-current", "true"); // announced as the loaded run
      else tr.removeAttribute("aria-current");
    }
  }

  function renderHistory(rows) {
    // Malformed entries are dropped by view-core rather than throwing mid-render
    // and leaving a half-built table.
    rows = V.normaliseHistory(rows);
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
      tr.dataset.runId = row.run_id;

      // Native <button> = focusable, correct role, Enter/Space handling and a
      // full accessible name (the row's own data is hidden behind it otherwise).
      const activate = document.createElement("button");
      activate.type = "button";
      activate.className = "row-activate";
      activate.textContent = row.run_id;
      activate.setAttribute("aria-label",
        "Load scorecard for run " + row.run_id +
        ", checkpoint " + row.checkpoint_id +
        ", task " + row.task_id +
        ", composite " + V.score2(row.composite_score) +
        ", decision " + V.safeDecision(row.deploy_decision) + ".");
      activate.addEventListener("click", () => loadRun(row.run_id));

      const run = document.createElement("td");
      run.className = "cell-run";
      run.appendChild(activate);

      const ckpt = document.createElement("td");
      ckpt.textContent = row.checkpoint_id;

      const task = document.createElement("td");
      task.className = "cell-task";
      task.textContent = row.task_id;

      const comp = document.createElement("td");
      comp.className = "num";
      comp.textContent = V.score2(row.composite_score);

      const decision = document.createElement("td");
      decision.appendChild(decisionBadge(row.deploy_decision));

      const created = document.createElement("td");
      created.className = "cell-created";
      const stamp = document.createElement("time");
      stamp.dateTime = row.created_at;
      stamp.textContent = row.created_at.replace("T", " ").replace("+00:00", "");
      created.appendChild(stamp);

      tr.append(run, ckpt, task, comp, decision, created);
      // Mouse convenience: clicking anywhere on the row performs the same
      // action; keyboard users drive the native button above (no double fire).
      tr.addEventListener("click", (event) => {
        if (event.target.closest("button")) return;
        activate.click();
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
      renderTrends(rows);
    } catch (err) {
      if (err instanceof AuthRequired) { showAuthRequired(); return; }
      els.historyStats.textContent = "History unavailable: " + err.message;
      renderTrendsError(err.message);
    }
  }

  async function loadRun(runId) {
    try {
      setStatus("Loading scorecard for " + runId + " \u2026", "busy");
      const card = await fetchJSON("/api/v1/validations/" + encodeURIComponent(runId) + "/scorecard");
      const view = renderScorecard(card, null); // duration unknown from history
      setStatus(
        "Loaded " + runId + " — composite " + view.composite +
        ", decision " + view.decision + " (" + view.decisionWord + ").",
        view.decision === "APPROVE" ? "ok" : "block");
    } catch (err) {
      if (err instanceof AuthRequired) { showAuthRequired(); return; }
      setStatus("Failed to load run " + runId + ": " + err.message, "error");
    }
  }

  /* ------------------------------------------------------------------ */
  /* Run form                                                             */
  /* ------------------------------------------------------------------ */

  /* APG loading-button pattern.
   *
   * The previous implementation set `button.disabled = true` and disabled the
   * inputs while a run was in flight. That is the standard "prevent double
   * submit" advice and it is wrong for accessibility: disabling the control
   * that currently holds focus drops focus to <body>, so a keyboard user who
   * activates Run has their focus thrown to the top of the page when the run
   * finishes and they try to tab onward (WCAG 2.4.3 Focus Order), and the
   * disabled state is announced but the reason is not.
   *
   * Instead: the button keeps focus and stays operable, carries
   * aria-busy="true" for the duration, and a re-entrant submit is ignored by
   * the `running` guard rather than by the browser. The inputs stay editable
   * so the form is never locked out mid-run; their values are already captured
   * in `body` before the request goes out. */
  let runInFlight = false;

  function setRunning(isRunning) {
    els.runBtn.classList.toggle("is-loading", isRunning);
    els.runBtn.setAttribute("aria-busy", String(isRunning));
    els.runBtnLabel.textContent = isRunning ? "Running\u2026" : "Run Validation";
  }

  els.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (runInFlight) return; // ignore a re-entrant activation, keep focus put
    if (!els.form.reportValidity()) return; // native 100\u20135000 / required checks

    const checkpoint = els.form.elements.checkpoint_id.value.trim();
    const task = els.form.elements.task_id.value.trim();
    const episodes = Number(els.form.elements.episodes.value);
    const threshold = Number(els.form.elements.threshold.value);

    const body = {
      checkpoint_id: checkpoint,
      // Gate knob. Honoured end-to-end: POST /api/v1/validations accepts a
      // `threshold` and scores against it, so the verdict shown here reflects
      // the value entered rather than a fixed 85.0.
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

    runInFlight = true;
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
      const view = renderScorecard(card, seconds.toFixed(1) + " s");
      // The live-region sentence names the block reasons, so the answer to "why
      // was this blocked?" reaches a screen reader without hunting the panel.
      setStatus(V.runSummarySentence(view), view.decision === "APPROVE" ? "ok" : "block");
      await refreshPanels();
    } catch (err) {
      if (err instanceof AuthRequired) { showAuthRequired(); return; }
      setStatus("Validation failed: " + err.message, "error");
    } finally {
      runInFlight = false;
      setRunning(false);
    }
  });

  /* ------------------------------------------------------------------ */
  /* Job Queue: enqueue form + 3s polling of /api/v1/jobs                */
  /* ------------------------------------------------------------------ */

  const JOBS_POLL_MS = 3000;
  let jobsTimer = null;
  let jobsEverLoaded = false;
  let jobsFirstLoad = true;
  let jobsErrored = false;
  const jobStatusSeen = new Map();

  // Colour is never the sole signal: every badge pairs a distinct hue with an
  // inline icon and the literal status word.
  const JOB_STATUS = {
    queued: { cls: "queued", icon: "queued" },
    running: { cls: "running", icon: "running" },
    done: { cls: "done", icon: "approve" },
    failed: { cls: "failed", icon: "block" },
  };

  function jobStatusBadge(status) {
    const key = Object.prototype.hasOwnProperty.call(JOB_STATUS, status) ? status : "queued";
    const meta = JOB_STATUS[key];
    const label = key;
    const span = document.createElement("span");
    span.className = "badge-sm badge-sm--" + meta.cls;
    span.innerHTML = ICONS[meta.icon] + "<span>" + label + "</span>";
    return span;
  }

  function jobsEmptyRow(message) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 6;
    td.className = "row-empty";
    td.textContent = message;
    tr.appendChild(td);
    els.jobsBody.appendChild(tr);
  }

  function renderJobs(jobs) {
    els.jobsBody.textContent = "";
    if (!Array.isArray(jobs) || jobs.length === 0) {
      jobsEmptyRow("No jobs in the queue \u2014 enqueue one above.");
      return;
    }
    // The API returns FIFO (oldest first); show newest first so a freshly
    // enqueued job appears at the top immediately.
    for (const job of [...jobs].reverse()) {
      const spec = job.spec || {};
      const tr = document.createElement("tr");

      const id = document.createElement("td");
      id.className = "cell-run";
      id.textContent = job.job_id || "\u2014";

      const status = document.createElement("td");
      status.appendChild(jobStatusBadge(job.status));

      const ckpt = document.createElement("td");
      ckpt.textContent = spec.checkpoint_id || "\u2014";

      const task = document.createElement("td");
      task.className = "cell-task";
      task.textContent = spec.task_id || "\u2014";

      const ep = document.createElement("td");
      ep.className = "num";
      ep.textContent = spec.episodes == null ? "\u2014" : String(spec.episodes);

      const adv = document.createElement("td");
      adv.className = "num";
      adv.textContent = spec.adversarial == null ? "\u2014" : String(spec.adversarial);

      tr.append(id, status, ckpt, task, ep, adv);
      els.jobsBody.appendChild(tr);
    }
  }

  // The 3-second poll rewrites #jobs-meta, so that element is deliberately NOT
  // a live region. Instead we announce only real status *transitions*.
  function announceJobTransitions(jobs) {
    const changes = [];
    for (const job of jobs) {
      const id = job.job_id || "job";
      const next = job.status || "queued";
      const prev = jobStatusSeen.get(id);
      if (prev !== undefined && prev !== next) {
        changes.push("Job " + id + " moved from " + prev + " to " + next + ".");
      }
      jobStatusSeen.set(id, next);
    }
    if (!changes.length) return;
    const tail = changes.length > 3 ? " " + (changes.length - 3) + " further changes." : "";
    announce(changes.slice(0, 3).join(" ") + tail);
  }

  function nowLabel() {
    return new Date().toISOString().replace("T", " ").replace("Z", "");
  }

  async function refreshJobs() {
    try {
      const jobs = await fetchJSON("/api/v1/jobs");
      jobsEverLoaded = true;
      renderJobs(jobs);
      const list = Array.isArray(jobs) ? jobs : [];
      const n = list.length;
      els.jobsMeta.textContent =
        n + (n === 1 ? " job" : " jobs") + " \u00b7 refreshed " + nowLabel();
      if (jobsErrored) {
        jobsErrored = false;
        announce("Job queue reachable again. " + n + (n === 1 ? " job" : " jobs") + " listed.");
      }
      if (jobsFirstLoad) {
        for (const job of list) jobStatusSeen.set(job.job_id || "job", job.status || "queued");
        jobsFirstLoad = false;
      } else {
        announceJobTransitions(list);
      }
    } catch (err) {
      // Offline-graceful: report the failure without throwing. Keep the last
      // good rows on screen if we ever loaded data; otherwise show a message.
      els.jobsMeta.textContent = "Queue unavailable: " + err.message;
      if (!jobsErrored) {
        jobsErrored = true;
        announce("Job queue unreachable: " + err.message);
      }
      if (!jobsEverLoaded) {
        els.jobsBody.textContent = "";
        jobsEmptyRow("Job queue unreachable \u2014 " + err.message);
      }
    }
  }

  function startJobsPolling() {
    refreshJobs();
    if (jobsTimer === null) jobsTimer = setInterval(refreshJobs, JOBS_POLL_MS);
  }

  function stopJobsPolling() {
    if (jobsTimer !== null) {
      clearInterval(jobsTimer);
      jobsTimer = null;
    }
  }

  // Be a good citizen: pause the 3s poll while the tab is hidden.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopJobsPolling();
    else startJobsPolling();
  });

  /* Same APG loading-button contract as the run form: never disabled, so focus
   * is never dropped mid-flight; aria-busy carries the state. */
  let enqueueInFlight = false;

  function setEnqueueBusy(isBusy) {
    els.enqueueBtn.classList.toggle("is-loading", isBusy);
    els.enqueueBtn.setAttribute("aria-busy", String(isBusy));
    els.enqueueBtnLabel.textContent = isBusy ? "Enqueuing\u2026" : "Enqueue Job";
  }

  function showEnqueueStatus(message, kind) {
    els.enqueueStatus.hidden = false;
    els.enqueueStatus.textContent = message;
    els.enqueueStatus.className = "run-status enqueue-status" + (kind ? " is-" + kind : "");
  }

  els.enqueueForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (enqueueInFlight) return;
    if (!els.enqueueForm.reportValidity()) return; // native required / min / max checks

    const body = {
      checkpoint_id: els.enqueueForm.elements.checkpoint_id.value.trim(),
      task_id: els.enqueueForm.elements.task_id.value.trim(),
      episodes: Number(els.enqueueForm.elements.episodes.value),
      adversarial: Number(els.enqueueForm.elements.adversarial.value),
    };

    enqueueInFlight = true;
    setEnqueueBusy(true);
    showEnqueueStatus("Enqueuing job for " + body.checkpoint_id + " \u2026", "busy");
    try {
      const res = await fetchJSON("/api/v1/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      showEnqueueStatus(
        "Enqueued " + (res && res.job_id ? res.job_id : "job") +
        " — status " + (res && res.status ? res.status : "queued") + ".",
        "ok");
      await refreshJobs(); // reflect the new job without waiting for the next tick
    } catch (err) {
      if (err instanceof AuthRequired) { showAuthRequired(); return; }
      showEnqueueStatus("Failed to enqueue: " + err.message, "error");
    } finally {
      enqueueInFlight = false;
      setEnqueueBusy(false);
    }
  });

  /* ------------------------------------------------------------------ */
  /* API-key form                                                         */
  /* ------------------------------------------------------------------ */

  let authInFlight = false;

  function setAuthBusy(isBusy) {
    els.authBtn.classList.toggle("is-loading", isBusy);
    els.authBtn.setAttribute("aria-busy", String(isBusy));
    els.authBtnLabel.textContent = isBusy ? "Connecting\u2026" : "Connect";
  }

  if (els.authForm) {
    els.authForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (authInFlight) return;
      if (!els.authForm.reportValidity()) return;
      const key = els.apiKey.value.trim();
      if (!key) {
        showAuthStatus("Enter the deployment's API key to continue.", "error");
        focusQuietly(els.apiKey);
        return;
      }
      authInFlight = true;
      setAuthBusy(true);
      showAuthStatus("Checking the API key\u2026", "busy");
      writeApiKey(key);
      try {
        // /api/v1/dashboard/summary is the cheapest authenticated endpoint, so
        // it is used as the credential check: a 401 here still raises
        // AuthRequired, which is exactly the "wrong key" signal we want.
        const summary = await fetchJSON("/api/v1/dashboard/summary");
        showDashboard();
        showAuthStatus("Connected. API key accepted.", "ok");
        renderStats(summary);
        await refreshPanels();
        startJobsPolling();
        announce("API key accepted. Dashboard loaded.");
        focusQuietly(els.runBtn);
      } catch (err) {
        if (err instanceof AuthRequired) {
          // Wrong key: clear the field and say so, staying on the auth panel.
          showAuthStatus("That API key was rejected. Check the deployment's key and try again.", "error");
          focusQuietly(els.apiKey);
        } else {
          showAuthStatus("Could not reach the API: " + err.message, "error");
        }
      } finally {
        authInFlight = false;
        setAuthBusy(false);
      }
    });
  }

  /* ------------------------------------------------------------------ */
  /* Skip link: make sure focus actually lands on <main>                 */
  /* ------------------------------------------------------------------ */

  if (els.skipLink && els.main) {
    els.skipLink.addEventListener("click", () => {
      // The anchor already moves the viewport; set focus explicitly so the next
      // Tab continues from the main content in every browser.
      els.main.focus({ preventScroll: true });
    });
  }

  /* ------------------------------------------------------------------ */
  /* Bootstrap                                                            */
  /* ------------------------------------------------------------------ */

  /* The dashboard body starts hidden so a protected deployment never flashes a
   * half-populated panel before the first 401. /api/v1/health is public and
   * reports `auth_enabled`, so the very first thing the page does is ask the
   * server whether a key is expected:
   *   - auth_enabled === true and no key is stored -> straight to the auth
   *     panel, before any /api/v1 request is attempted;
   *   - otherwise -> reveal the dashboard and load its panels;
   *   - health unreachable or unparseable -> reveal the dashboard anyway and let
   *     the normal error paths report the failure, rather than stranding the
   *     user behind a gate screen that may not be needed. */
  (async function boot() {
    const storedKey = readApiKey();
    let policy = null;
    try {
      policy = await probeAuthPolicy();
    } catch (err) {
      policy = null; // probeAuthPolicy already swallows; belt and braces
    }

    const serverWantsKey = policy !== null && policy.auth_enabled === true;
    if (serverWantsKey && !storedKey) {
      showAuthRequired(
        "This ValidSim instance requires an X-API-Key header on every /api/v1 route, so the " +
        "dashboard could not load its data. Paste the deployment's API key to continue."
      );
      announce("This ValidSim deployment requires an API key.");
      return;
    }

    showDashboard();
    await refreshPanels();
    startJobsPolling();

    if (serverWantsKey && storedKey) {
      // A stored key on a key-protected deployment: the panels above either
      // rendered (good) or already swapped us to the auth panel via 401.
      showAuthStatus("Connected with the stored API key.", "ok");
    }
  })();
})();

/* ValidSim Dashboard — pure presentation logic (no DOM, no build step).
 *
 * Everything in this file is a *pure function* over plain data: given the JSON
 * the API returns, it computes the exact strings the dashboard renders. It
 * touches no DOM, no window and no network, which is what makes it testable
 * without a browser — the test suite executes this same file under Node and
 * asserts on its output (tests/test_web_presentation_agent.py).
 *
 * Why a separate file? app.js used to be one IIFE whose renderers could only be
 * exercised in a real browser. Those renderers decide what an operator sees
 * about a deploy verdict, and they were the least testable part of the app.
 * Splitting the decisions out pins the behaviour with tests while app.js stays
 * purely the DOM wiring.
 *
 * Loaded as a classic <script> before app.js; attaches `window.ValidSimView`.
 * No modules, no bundler, no framework — the project ships as plain files.
 */
"use strict";

(function (root, factory) {
  /* Attaches to the browser global, and supports module.exports so the Node
   * test harness can require() the very same file the browser downloads. */
  var api = factory();
  if (typeof module === "object" && module && module.exports) module.exports = api;
  if (root) root.ValidSimView = api;
})(typeof window !== "undefined" ? window : this, function () {
  /* Shown wherever a value is genuinely unknown. U+2014 EM DASH, matching the
   * PDF exporter's default dash so both artifacts read identically. */
  var EM_DASH = "\u2014";

  /* The engine's default gate. Mirrors Scorecard.threshold's default in
   * validsim/engine/scorecard.py, so a scorecard that omits the field is still
   * rendered at the threshold the gate actually applied. */
  var DEFAULT_THRESHOLD = 85;

  /* Displayed when the API returns something the dashboard cannot honestly
   * describe (a non-numeric composite, a taxonomy that is not an object, ...).
   * Deliberately a word rather than "NaN": it is never mistaken for a
   * measurement, and a screen reader announces it as a word. */
  var UNKNOWN_TEXT = "unavailable";

  /* Digits used for percentages.
   *
   * These are the 0-100 component scores, and the engine rounds them to 2dp
   * (`_robustness_score` -> round(..., 2), `safety.safety_score` as stored), so
   * two decimals is the finest precision the underlying data supports. The PDF
   * exporter prints the same figures the same way (`f"{pct:.1f}%"` for the
   * success rate, `{:.2f}` for the component scores), so the dashboard and the
   * exported artifact read identically.
   *
   * Note the deliberate asymmetry: the *fraction* `success_rate` is stored at
   * 4dp (see `round(evaluation.success_rate, 4)`), so a percentage of it is
   * meaningful to one decimal, while `robustness_score` is already a 0-100
   * value rounded to 2dp. Both are rendered at 1dp as a percentage and 2dp as
   * a score respectively; see `pct` and `score2`. */
  var RATE_DIGITS = 1;

  /* The two values the engine's DeployDecision can take. */
  var DECISIONS = { APPROVE: true, BLOCK: true };

  /* ---------------------------------------------------------------- */
  /* Coercion helpers                                                 */
  /* ---------------------------------------------------------------- */

  /* Coerce to a finite number, or null when that is impossible.
   *
   * Deliberately stricter than Number(x): a numeric string like "85" is
   * accepted, but "" and null must NOT become 0. Number("") is 0, hence the
   * explicit non-empty-string guard — rendering "0.00" for an absent score
   * would be a lie about a deploy gate. */
  function num(value) {
    if (typeof value === "number") return Number.isFinite(value) ? value : null;
    if (typeof value === "string" && value.trim() !== "") {
      var parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : null;
    }
    return null;
  }

  /* Coerce to a trimmed non-empty string, or null. Numbers are stringified so
   * an opaque label (a run id) or a reason that arrived as a number still
   * renders; booleans, objects and arrays become null rather than "[object
   * Object]". */
  function str(value) {
    if (typeof value === "string") {
      var trimmed = value.trim();
      return trimmed === "" ? null : trimmed;
    }
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
    return null;
  }

  /* Display helper: a string, or the em dash when absent. */
  function text(value, fallback) {
    var s = str(value);
    return s === null ? (fallback === undefined ? EM_DASH : fallback) : s;
  }

  /* Render a 0-1 fraction as a percentage, or the unknown marker. */
  function pct(value) {
    var n = num(value);
    return n === null ? UNKNOWN_TEXT : (n * 100).toFixed(RATE_DIGITS) + "%";
  }

  /* Render to two decimals, or the unknown marker. Used for the composite, the
   * threshold and the 0-100 component scores: these are the numbers the gate
   * itself compares, so the dashboard prints them at the gate's precision. */
  function score2(value) {
    var n = num(value);
    return n === null ? UNKNOWN_TEXT : n.toFixed(2);
  }

  /* Signed percentage-point delta, using U+2212 MINUS SIGN so a negative reads
   * as a real minus rather than a hyphen. */
  function signedPoints(value) {
    var n = num(value);
    if (n === null) return UNKNOWN_TEXT;
    var pp = n * 100;
    return (pp >= 0 ? "+" : "\u2212") + Math.abs(pp).toFixed(RATE_DIGITS) + " pp";
  }

  /* An integer count, or the unknown marker. Clamped at 0: a negative episode
   * count is nonsense and must not read as a real figure. */
  function count(value) {
    var n = num(value);
    return n === null ? UNKNOWN_TEXT : String(Math.max(0, Math.round(n)));
  }

  /* ---------------------------------------------------------------- */
  /* Scorecard normalisation                                           */
  /* ---------------------------------------------------------------- */

  /* Normalise the failure taxonomy to a sorted [mode, count] list.
   *
   * Accepts an object, a Map-like array of pairs, or nothing at all. Non-numeric
   * and negative counts are dropped rather than coerced, so a malformed payload
   * produces an empty chart rather than a chart of zeroes. */
  function taxonomyEntries(raw) {
    var pairs = [];
    if (Array.isArray(raw)) {
      for (var i = 0; i < raw.length; i++) {
        var pair = raw[i];
        if (Array.isArray(pair) && pair.length >= 2) pairs.push([pair[0], pair[1]]);
      }
    } else if (raw && typeof raw === "object") {
      var keys = Object.keys(raw);
      for (var k = 0; k < keys.length; k++) pairs.push([keys[k], raw[keys[k]]]);
    }
    var entries = [];
    for (var p = 0; p < pairs.length; p++) {
      var mode = str(pairs[p][0]);
      var n = num(pairs[p][1]);
      if (mode === null || n === null || n <= 0) continue;
      entries.push([mode, n]);
    }
    entries.sort(function (a, b) { return b[1] - a[1]; });
    return entries;
  }

  /* The provenance flags gate on, plus their documented semantics:
   *  - robustness_measured: false means the score is a structural constant
   *    (one randomization group), not a measurement.
   *  - regression_baseline_available: false means the regression component
   *    scored 100 because nothing was compared, not because it came back clean.
   * Both default to true so a pre-flag scorecard is not retroactively relabelled
   * as unmeasured — "we cannot tell" must never render as a positive claim. */
  function provenance(raw, key) {
    return raw[key] !== false;
  }

  /* Coerce an arbitrary JSON value into a fully-populated, known-safe shape.
   *
   * This is the single robustness chokepoint for the scorecard panel: once
   * normalised, every field is the correct JS type and is either a usable value
   * or an explicit null sentinel. NO downstream renderer can throw, so a
   * scorecard from a proxy that adds a field, strips one, or nulls a field
   * still renders instead of blanking the panel the operator is reading a
   * deploy verdict from. */
  function normaliseScorecard(raw) {
    var s = raw && typeof raw === "object" ? raw : {};

    var ci = s.confidence_interval;
    var hasCi = Array.isArray(ci) && ci.length === 2 && num(ci[0]) !== null && num(ci[1]) !== null;

    /* block_reasons is a list of engine-authored sentences. A JSON array of
     * strings is the contract; a bare string is tolerated (older payloads) and
     * anything else degrades to "no reasons" rather than throwing. */
    var reasons = [];
    if (Array.isArray(s.block_reasons)) {
      for (var i = 0; i < s.block_reasons.length; i++) {
        var reason = str(s.block_reasons[i]);
        if (reason !== null) reasons.push(reason);
      }
    } else {
      var single = str(s.block_reasons);
      if (single !== null) reasons.push(single);
    }

    var threshold = num(s.threshold);

    return {
      run_id: text(s.run_id),
      checkpoint_id: text(s.checkpoint_id),
      task_id: text(s.task_id),
      created_at: text(s.created_at),
      composite_score: num(s.composite_score),
      threshold: threshold === null ? DEFAULT_THRESHOLD : threshold,
      success_rate: num(s.success_rate),
      safety_score: num(s.safety_score),
      robustness_score: num(s.robustness_score),
      regression_delta: num(s.regression_delta),
      confidence_interval: hasCi ? [num(ci[0]), num(ci[1])] : null,
      episode_count: num(s.episode_count),
      deploy_decision: text(s.deploy_decision, "PENDING"),
      failure_taxonomy: taxonomyEntries(s.failure_taxonomy),
      adversarial_episode_count: num(s.adversarial_episode_count),
      adversarial_success_rate: num(s.adversarial_success_rate),
      block_reasons: reasons,
      robustness_measured: provenance(s, "robustness_measured"),
      regression_baseline_available: provenance(s, "regression_baseline_available"),
    };
  }

  /* ---------------------------------------------------------------- */
  /* Scorecard view model                                              */
  /* ---------------------------------------------------------------- */

  /* ---------------------------------------------------------------- */
  /* Model comparison view model                                       */
  /* ---------------------------------------------------------------- */

  /* The regression engine classifies each metric comparison with one of
   * these three severities (see validsim/engine/regression.py). The rank
   * lets us fold a list of items into a single "worst severity" without
   * string comparisons, so the dashboard never claims "warning" when the
   * engine actually said "critical".
   *
   * The engine's vocabulary is "info" / "warning" / "critical". The
   * notification layer uses a different word for the middle band
   * ("warn", see validsim/notify/dispatcher.py); both are accepted here
   * and normalised to the engine's spelling, so a payload from either
   * producer ranks identically. */
  var COMPARISON_SEVERITY_RANK = { info: 0, warning: 1, warn: 1, critical: 2 };

  /* Canonical severity spelling used everywhere the dashboard renders one. */
  var COMPARISON_SEVERITY_CANONICAL = { info: "info", warning: "warning", warn: "warning", critical: "critical" };

  /* Human labels for the metrics the engine knows how to compare. An
   * unknown metric falls back to its raw key rather than being dropped —
   * the operator still needs to see which metric moved. */
  var COMPARISON_METRIC_LABELS = {
    success_rate: "Success rate",
    mean_duration_s: "Mean duration",
    composite_score: "Composite score",
  };

  function comparisonMetricLabel(metric) {
    if (typeof metric === "string" && COMPARISON_METRIC_LABELS[metric]) {
      return COMPARISON_METRIC_LABELS[metric];
    }
    return typeof metric === "string" && metric ? metric : "metric";
  }

  /* Render a metric's absolute value at the precision the engine stores it
   * at: success_rate is a 0-1 fraction (4dp in the store), mean_duration_s
   * is seconds, composite_score is a 0-100 score. */
  function comparisonFormatValue(metric, value) {
    var v = num(value);
    if (v === null) return UNKNOWN_TEXT;
    if (metric === "success_rate") return pct(v);
    if (metric === "mean_duration_s") return v.toFixed(3) + " s";
    if (metric === "composite_score") return score2(v);
    return v.toFixed(2);
  }

  /* Signed delta between baseline and run. A positive success-rate delta is
   * an improvement; the same sign convention applies to duration and score,
   * where positive means the run was slower / scored higher. The regression
   * engine's own severity call is what decides whether that is *bad*, so the
   * dashboard never second-guesses the sign itself. */
  function comparisonFormatDelta(metric, delta) {
    var v = num(delta);
    if (v === null) return EM_DASH;
    if (metric === "success_rate") return signedPoints(v);
    var sign = v > 0 ? "+" : "";
    if (metric === "mean_duration_s") return sign + v.toFixed(3) + " s";
    return sign + v.toFixed(2);
  }

  /* p-values below the conventional 0.001 threshold are reported as an
   * inequality rather than "0.000", because "0.000" reads like an exact
   * zero and would overstate what the test established. */
  function comparisonFormatPValue(p) {
    var v = num(p);
    if (v === null) return "n/a";
    if (v < 0.001) return "<0.001";
    return v.toFixed(3);
  }

  /* Normalise one comparison item into the exact strings the table renders.
   * Every field is coerced through the helpers above so a malformed API
   * response degrades to "unavailable" instead of "NaN" on screen. */
  function normaliseComparisonItem(raw) {
    var item = raw && typeof raw === "object" ? raw : {};
    var metric = typeof item.metric === "string" && item.metric ? item.metric : "metric";
    var rawSeverity = item.severity;
    var severity = COMPARISON_SEVERITY_RANK[rawSeverity] !== undefined
      ? COMPARISON_SEVERITY_CANONICAL[rawSeverity]
      : "info";
    return {
      metric: metric,
      label: comparisonMetricLabel(metric),
      before: comparisonFormatValue(metric, item.before),
      after: comparisonFormatValue(metric, item.after),
      deltaText: comparisonFormatDelta(metric, item.delta),
      pText: comparisonFormatPValue(item.p_value),
      significant: item.significant === true,
      severity: severity,
      severityRank: COMPARISON_SEVERITY_RANK[rawSeverity] !== undefined
        ? COMPARISON_SEVERITY_RANK[rawSeverity]
        : 0,
    };
  }

  /* Build the whole comparison view model from an API response. Tolerates a
   * missing/!Array items field so a partial response still renders an
   * empty table rather than throwing inside the renderer. */
  function comparisonView(raw) {
    var source = raw && typeof raw === "object" ? raw : {};
    var rawItems = Array.isArray(source.items) ? source.items : [];
    var items = rawItems.map(normaliseComparisonItem);
    var worst = "info";
    var worstRank = 0;
    var significantCount = 0;
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      /* The engine counts a *regression* as an item that is both
       * statistically significant and worse than "info"; a significant
       * "info" item (e.g. an improvement) is not a regression. Mirror
       * that rule so the count on screen matches the server's. */
      if (it.significant && it.severity !== "info") significantCount += 1;
      if (it.severityRank > worstRank) {
        worstRank = it.severityRank;
        worst = it.severity;
      }
    }
    /* The server may declare a worst severity that outranks any single
     * item's (it can aggregate context the item list does not carry).
     * Honour it when it does. */
    var declared = source.worst_severity;
    if (
      COMPARISON_SEVERITY_RANK[declared] !== undefined
      && COMPARISON_SEVERITY_RANK[declared] > worstRank
    ) {
      worst = COMPARISON_SEVERITY_CANONICAL[declared];
    }
    /* Prefer the server's own count when present: it is authoritative and
     * counts regressions, not merely significant items. */
    var serverCount = num(source.significant_count);
    if (serverCount !== null) significantCount = serverCount;
    return {
      runId: text(source.run_id, "unknown run"),
      baselineId: text(source.baseline_id, "unknown baseline"),
      items: items,
      significantCount: significantCount,
      worstSeverity: worst,
      hasItems: items.length > 0,
    };
  }

  /* One-sentence summary of a comparison, for the panel's status line. */
  function comparisonSummarySentence(view) {
    if (!view || typeof view !== "object") return "Comparison unavailable.";
    if (!view.hasItems) return "No comparable metrics were returned for this pair of runs.";
    /* Run ids are long opaque strings; the tail is the part an operator
     * can correlate with the run list. */
    function shortId(id) {
      var s = text(id, "unknown");
      return s.length > 12 ? s.slice(-8) : s;
    }
    var a = shortId(view.baselineId);
    var b = shortId(view.runId);
    if (!view.significantCount) {
      return "No significant regressions between " + a + " and " + b
        + "; worst severity " + view.worstSeverity + ".";
    }
    var noun = view.significantCount === 1 ? "regression" : "regressions";
    return view.significantCount + " statistically significant " + noun
      + " between " + a + " and " + b
      + "; worst severity " + view.worstSeverity + ".";
  }

  /* Whitelist the decision. The API's own type is Literal["APPROVE","BLOCK"];
   * anything else must not be able to inject markup into the verdict banner,
   * so an unrecognised value degrades to PENDING (neutral, and visibly not a
   * verdict). */
  function safeDecision(decision) {
    return DECISIONS[decision] === true ? decision : "PENDING";
  }

  /* Word describing a decision, for the status line and the badge's accessible
   * name. Colour is never the only signal (WCAG 1.4.1), so every verdict is
   * carried by a literal word as well as its hue. */
  function decisionWord(decision) {
    var d = safeDecision(decision);
    if (d === "APPROVE") return "approved";
    if (d === "BLOCK") return "blocked";
    return "pending";
  }

  /* The whole scorecard panel as a flat bag of already-formatted strings.
   * app.js does nothing but assign these into the DOM, so every formatting
   * decision is testable without a browser. */
  function scorecardView(card, durationLabel) {
    var c = normaliseScorecard(card);
    var decision = safeDecision(c.deploy_decision);

    /* The engine's decision is authoritative: the dashboard never re-derives
     * APPROVE/BLOCK from the composite, because the gate has conditions the
     * composite alone does not express (evidence sufficiency, the adversarial
     * binomial floor). The two are only ever shown side by side. */
    var compositeText = score2(c.composite_score);
    var thresholdText = score2(c.threshold);

    var taxonomyTotal = 0;
    for (var i = 0; i < c.failure_taxonomy.length; i++) taxonomyTotal += c.failure_taxonomy[i][1];

    var taxonomyRows = [];
    for (var t = 0; t < c.failure_taxonomy.length; t++) {
      var mode = c.failure_taxonomy[t][0];
      var n = c.failure_taxonomy[t][1];
      taxonomyRows.push({
        mode: mode.replace(/_/g, " "),
        rawMode: mode,
        count: String(n),
        share: taxonomyTotal > 0 ? pct(n / taxonomyTotal) : UNKNOWN_TEXT,
      });
    }

    return {
      decision: decision,
      decisionWord: decisionWord(decision),
      /* The run id on its own, so callers never re-parse it out of `meta`.
       * `meta` is a display string; splitting it back apart is a coupling that
       * silently breaks the moment a field is added to the middle of it. */
      runId: c.run_id,
      composite: compositeText,
      /* The gate line states the comparison in words as well as symbols, so
       * the verdict is legible without reading "≥". */
      gate: "gate \u2265 " + thresholdText,
      gateSentence:
        "Composite " + compositeText + " against a gate of " + thresholdText + ".",
      success: pct(c.success_rate),
      safety: score2(c.safety_score),
      robustness: c.robustness_measured
        ? score2(c.robustness_score)
        /* A structural 100.0 here would read as proof of a robust model, which
         * is the opposite of what the data supports. Same wording as the PDF
         * exporter and the Markdown export. */
        : score2(c.robustness_score) + " (not measured)",
      regression: c.regression_delta === null
        ? /* Distinguish "no baseline was supplied" from "compared and flat",
           * mirroring the regression_baseline_available provenance flag. */
          (c.regression_baseline_available ? "0.0 pp" : "no baseline")
        : signedPoints(c.regression_delta),
      regressionHasBaseline: c.regression_delta !== null || c.regression_baseline_available,
      ci: c.confidence_interval === null
        ? EM_DASH
        : pct(c.confidence_interval[0]) + " \u2013 " + pct(c.confidence_interval[1]),
      episodes: count(c.episode_count),
      duration: durationLabel || EM_DASH,
      meta: c.run_id + " \u00b7 " + c.checkpoint_id + " \u00b7 " + c.task_id + " \u00b7 " + c.created_at,

      /* --- the fields the dashboard previously dropped entirely --- */
      adversarialCount: count(c.adversarial_episode_count),
      /* A null adversarial rate means no adversarial segment ran at all, which
       * is different from a segment that ran and scored 0%. */
      adversarialRate: c.adversarial_success_rate === null ? null : pct(c.adversarial_success_rate),
      adversarialText: c.adversarial_episode_count === null || c.adversarial_episode_count <= 0
        ? "none"
        : count(c.adversarial_episode_count) + " \u00b7 " +
          (c.adversarial_success_rate === null ? UNKNOWN_TEXT : pct(c.adversarial_success_rate)),
      blockReasons: c.block_reasons,
      hasBlockReasons: c.block_reasons.length > 0,

      taxonomyRows: taxonomyRows,
      taxonomyTotal: taxonomyTotal,
      taxonomyModeCount: taxonomyRows.length,
      taxonomyEmpty: taxonomyRows.length === 0,
    };
  }

  /* One polite sentence describing a finished run, for the live region. */
  function runSummarySentence(view) {
    return (
      "Run " + view.runId +
      " complete \u2014 composite " + view.composite +
      ", decision " + view.decision + " (" + view.decisionWord + ")" +
      (view.hasBlockReasons
        ? ", blocked for " + view.blockReasons.length +
          (view.blockReasons.length === 1 ? " reason: " : " reasons: ") +
          view.blockReasons.join("; ")
        : "") +
      "."
    );
  }

  /* ---------------------------------------------------------------- */
  /* History + summary                                                 */
  /* ---------------------------------------------------------------- */

  /* Normalise one run-summary row. Same contract as normaliseScorecard but
   * tolerant of a row that is not an object at all. */
  function normaliseRunSummary(raw) {
    var r = raw && typeof raw === "object" ? raw : {};
    return {
      run_id: text(r.run_id),
      checkpoint_id: text(r.checkpoint_id),
      task_id: text(r.task_id),
      created_at: text(r.created_at),
      composite_score: num(r.composite_score),
      deploy_decision: text(r.deploy_decision, "PENDING"),
    };
  }

  /* Newest-first history rows, defensively filtered: a malformed entry is
   * dropped rather than throwing mid-render and leaving a half-built table. */
  function normaliseHistory(raw) {
    if (!Array.isArray(raw)) return [];
    var rows = [];
    for (var i = 0; i < raw.length; i++) {
      if (raw[i] && typeof raw[i] === "object") rows.push(normaliseRunSummary(raw[i]));
    }
    return rows;
  }

  /* The KPI strip line. A missing/NaN average is an em dash, not "NaN". */
  function summaryText(summary) {
    var s = summary && typeof summary === "object" ? summary : {};
    var avg = num(s.avg_composite);
    return (
      "Runs " + count(s.total_runs) +
      " \u00b7 Approved " + count(s.approvals) +
      " \u00b7 Blocked " + count(s.blocks) +
      " \u00b7 Avg composite " + (avg === null ? EM_DASH : avg.toFixed(2))
    );
  }

  /* ---------------------------------------------------------------- */
  /* Trends (client-side, mirroring validsim/engine/trends.py)          */
  /* ---------------------------------------------------------------- */

  function round4(x) {
    return Math.round(x * 10000) / 10000;
  }

  /* Order run summaries oldest-first.
   *
   * /api/v1/dashboard/history documents itself as newest-first, so the list is
   * reversed first and created_at is then used only to *stabilise* that order.
   * That direction matters and a plain timestamp sort gets it wrong:
   * created_at is second-precision (_utc_now_iso uses timespec="seconds"), so
   * every run started inside the same second ties, a stable sort returns 0 for
   * those ties, and the original newest-first order survives — making the
   * OLDEST run look like the latest one. Reversing first and breaking ties by
   * position fixes that while still correcting a backend that ever hands back
   * rows out of order. */
  function oldestFirst(rows) {
    var decorated = [];
    var reversed = rows.slice().reverse();
    for (var i = 0; i < reversed.length; i++) {
      decorated.push({ row: reversed[i], pos: i });
    }
    decorated.sort(function (a, b) {
      var at = Date.parse(a.row.created_at);
      var bt = Date.parse(b.row.created_at);
      if (!Number.isNaN(at) && !Number.isNaN(bt) && at !== bt) return at - bt;
      // Equal or unparseable timestamps: keep the reversed (oldest-first) order.
      return a.pos - b.pos;
    });
    var out = [];
    for (var j = 0; j < decorated.length; j++) out.push(decorated[j].row);
    return out;
  }

  /* Mirrors validsim/engine/trends.py: latest composite, delta vs the previous
   * run (0 for <2 runs), a 3-run moving average, and the 10-run approval rate.
   *
   * An unreadable composite degrades to 0.0 rather than dropping the row, which
   * is exactly what trends.py's as_float(default=0.0) does — the row still
   * counts toward the window sizes, so the panel reports the same n the server
   * would. */
  function computeTrends(rows) {
    if (!Array.isArray(rows) || rows.length === 0) return null;
    var sorted = oldestFirst(rows);

    var scores = [];
    for (var i = 0; i < sorted.length; i++) {
      var s = num(sorted[i].composite_score);
      scores.push(s === null ? 0 : s);
    }

    var n = scores.length;
    var latest = scores[n - 1];
    var delta = n >= 2 ? latest - scores[n - 2] : 0;

    var maWindow = scores.slice(Math.max(0, n - 3));
    var maSum = 0;
    for (var m = 0; m < maWindow.length; m++) maSum += maWindow[m];
    var ma3 = round4(maSum / maWindow.length);

    var approvalWindow = sorted.slice(Math.max(0, sorted.length - 10));
    var approvals = 0;
    for (var a = 0; a < approvalWindow.length; a++) {
      if (safeDecision(approvalWindow[a].deploy_decision) === "APPROVE") approvals++;
    }
    var approvalRate = round4(approvals / approvalWindow.length);

    return {
      latestScore: latest,
      delta: round4(delta),
      ma3: ma3,
      approvalRate: approvalRate,
      totalRuns: n,
      window3Size: maWindow.length,
      window10Size: approvalWindow.length,
      newestCheckpoint: sorted[sorted.length - 1].checkpoint_id,
    };
  }

  return {
    EM_DASH: EM_DASH,
    UNKNOWN_TEXT: UNKNOWN_TEXT,
    DEFAULT_THRESHOLD: DEFAULT_THRESHOLD,
    num: num,
    str: str,
    text: text,
    pct: pct,
    score2: score2,
    signedPoints: signedPoints,
    count: count,
    taxonomyEntries: taxonomyEntries,
    normaliseScorecard: normaliseScorecard,
    safeDecision: safeDecision,
    decisionWord: decisionWord,
    scorecardView: scorecardView,
    runSummarySentence: runSummarySentence,
    comparisonView: comparisonView,
    normaliseComparisonItem: normaliseComparisonItem,
    comparisonSummarySentence: comparisonSummarySentence,
    comparisonFormatPValue: comparisonFormatPValue,
    comparisonMetricLabel: comparisonMetricLabel,
    normaliseHistory: normaliseHistory,
    summaryText: summaryText,
    computeTrends: computeTrends,
  };
});

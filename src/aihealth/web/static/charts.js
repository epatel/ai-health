// Dashboard charts (ECharts). Colours come from CSS custom properties so
// light/dark mode stay in one place; charts re-render when the scheme changes.
(function () {
  const charts = [];

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function theme() {
    return {
      text: css("--text-primary"),
      secondary: css("--text-secondary"),
      muted: css("--text-muted"),
      grid: css("--grid"),
      surface: css("--surface-1"),
      series: css("--series-1"),
      band: {
        healthy: css("--status-healthy"),
        warning: css("--status-warning"),
        alert: css("--status-alert"),
      },
    };
  }

  function band(score) {
    return score >= 9 ? "healthy" : score >= 4 ? "warning" : "alert";
  }

  function tooltipBase(t) {
    return {
      backgroundColor: t.surface,
      borderColor: t.grid,
      textStyle: { color: t.text, fontSize: 12 },
      extraCssText: "box-shadow: 0 2px 8px rgba(0,0,0,.15); border-radius: 6px;",
    };
  }

  function mount(id, build) {
    const el = document.getElementById(id);
    if (!el || !window.echarts) return;
    const chart = echarts.init(el, null, { renderer: "svg" });
    const render = () => chart.setOption(build(theme()), true);
    render();
    charts.push({ chart, render });
    return chart;
  }

  // Labels of scans whose aihealth or parser/grammar versions differ from
  // the scan before (both known): scores there may move without any code
  // change.
  function versionChanges(points) {
    const differs = (a, b) => Boolean(a) && Boolean(b) && a !== b;
    const changed = (p, i) => i > 0 &&
      (differs(p.tool, points[i - 1].tool) || differs(p.parsers, points[i - 1].parsers));
    return new Set(points.filter(changed).map((p) => p.label));
  }

  function versionsText(p) {
    return `aihealth ${p.tool || "?"}${p.parsers ? ", " + p.parsers : ""}`;
  }

  function trendTooltip(points, changed) {
    return (items) => {
      const p = points[items[0].dataIndex];
      const when = (p.date || "").slice(0, 16).replace("T", " ");
      const score = p.value == null ? "n/a" : p.value.toFixed(2) + " (" + band(p.value) + ")";
      const commit = p.commit ? p.commit.slice(0, 7) + "<br>" : "";
      const versions = changed.has(p.label) ? `<br>versions changed: ${versionsText(p)}` : "";
      return `<b>${p.label}</b> · ${when}<br>${commit}score ${score}${versions}`;
    };
  }

  // band thresholds (dashed) and version changes (dotted, vertical)
  function trendMarks(t, changed) {
    const versionLine = (label) => ({
      xAxis: label, name: "versions", lineStyle: { type: "dotted", color: t.secondary },
    });
    return [
      { yAxis: 9, name: "healthy ≥ 9" },
      { yAxis: 4, name: "alert < 4" },
      ...[...changed].map(versionLine),
    ];
  }

  // Score per scan: one series, fixed 1-10 axis, dashed band thresholds,
  // a dotted vertical line where the aihealth or parser versions changed.
  function trend(id, points, opts) {
    const changed = versionChanges(points);
    const chart = mount(id, (t) => ({
      grid: { left: 36, right: 64, top: 16, bottom: 28 },
      tooltip: {
        ...tooltipBase(t),
        trigger: "axis",
        axisPointer: { type: "line", lineStyle: { color: t.muted } },
        formatter: trendTooltip(points, changed),
      },
      xAxis: {
        type: "category",
        data: points.map((p) => p.label),
        axisLine: { lineStyle: { color: t.grid } },
        axisTick: { show: false },
        axisLabel: { color: t.secondary, fontSize: 11 },
      },
      yAxis: {
        type: "value", min: 1, max: 10, interval: 1,
        splitLine: { lineStyle: { color: t.grid } },
        axisLabel: { color: t.secondary, fontSize: 11 },
      },
      series: [{
        type: "line",
        data: points.map((p) => p.value),
        lineStyle: { width: 2, color: t.series },
        itemStyle: { color: t.series, borderColor: t.surface, borderWidth: 2 },
        symbolSize: (v, params) => (points[params.dataIndex].label === opts.current ? 10 : 8),
        markLine: {
          silent: true, symbol: "none",
          lineStyle: { type: "dashed", width: 1, color: t.muted },
          label: { color: t.secondary, fontSize: 11, formatter: "{b}" },
          data: trendMarks(t, changed),
        },
      }],
    }));
    if (chart) chart.on("click", (e) => { const p = points[e.dataIndex]; if (p && p.href) location.href = p.href; });
  }

  // Total penalty per smell: single hue, horizontal, largest first.
  function smells(id, rows) {
    const data = rows.slice().reverse(); // ECharts draws category axis bottom-up
    mount(id, (t) => ({
      grid: { left: 8, right: 40, top: 8, bottom: 8, containLabel: true },
      tooltip: {
        ...tooltipBase(t),
        trigger: "item",
        formatter: (p) => {
          const r = data[p.dataIndex];
          return `<b>${r.label}</b><br>penalty ${r.penalty}<br>${r.count} finding(s) in ${r.files} file(s)`;
        },
      },
      xAxis: { type: "value", splitLine: { lineStyle: { color: t.grid } }, axisLabel: { color: t.secondary, fontSize: 11 } },
      yAxis: {
        type: "category", data: data.map((r) => r.label),
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: t.text, fontSize: 12 },
      },
      series: [{
        type: "bar",
        data: data.map((r) => r.penalty),
        barMaxWidth: 16,
        itemStyle: { color: t.series, borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: "right", color: t.secondary, fontSize: 11 },
      }],
    }));
  }

  // Files by directory: area = LoC, colour = score band.
  function treemap(id, tree, fileHref) {
    function colour(node, t) {
      if (node.children) return { ...node, children: node.children.map((c) => colour(c, t)) };
      return { ...node, itemStyle: { color: t.band[node.band] } };
    }
    const chart = mount(id, (t) => ({
      tooltip: {
        ...tooltipBase(t),
        formatter: (p) => {
          const d = p.data || {};
          if (!d.path) return `<b>${p.name}</b><br>${p.value} LoC`;
          return `<b>${d.path}</b><br>score ${d.score.toFixed(2)} (${d.band})<br>${d.value} LoC · ${d.findings} finding(s)`;
        },
      },
      series: [{
        type: "treemap",
        data: tree.map((n) => colour(n, t)),
        left: 0, right: 0, top: 0, bottom: 36, // default is 80% centred

        roam: false,
        leafDepth: null,
        nodeClick: "zoomToNode",
        breadcrumb: {
          itemStyle: { color: t.surface, borderColor: t.grid, textStyle: { color: t.text } },
        },
        label: {
          show: true, fontSize: 11, color: "#0b0b0b",
          formatter: (p) => (p.data && p.data.path ? `${p.name}\n${p.data.score.toFixed(1)}` : p.name),
        },
        upperLabel: { show: true, height: 20, color: t.text, backgroundColor: "transparent" },
        itemStyle: { borderColor: t.surface, borderWidth: 2, gapWidth: 2 },
        levels: [
          { itemStyle: { borderWidth: 0, gapWidth: 4 } },
          { itemStyle: { borderColor: t.grid, borderWidth: 4, gapWidth: 2 } },
        ],
      }],
    }));
    if (chart) chart.on("click", (e) => { if (e.data && e.data.path) location.href = fileHref(e.data.path); });
  }

  const rerender = () => charts.forEach((c) => c.render());
  window.addEventListener("resize", () => charts.forEach((c) => c.chart.resize()));
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", rerender);
  // a theme set on <html data-theme="..."> must win both ways, like the CSS
  new MutationObserver(rerender).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });

  window.aihealthCharts = { trend, smells, treemap };
})();

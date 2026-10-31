/* Polymarket session chart — a LINE chart (not candlesticks: Polymarket's own price
   history IS a line series, {t,p} probability points, see app/polymarket/clob.py),
   dual price scale (share price 0-1 left, underlying spot price right), a price line
   at the strike, and trade entry/exit markers. Theme constants copied from gp.js's
   chartOpts() for visual consistency with the rest of the terminal. Standalone (not
   an extension of gp.js, which is deeply OHLCV/indicator-specific — see the plan). */
(function () {
  "use strict";
  const LW = () => window.LightweightCharts;

  function chartOpts(w, h) {
    return {
      width: w, height: h,
      layout: { background: { type: "solid", color: "#050505" }, textColor: "#8b8b8b", fontFamily: "JetBrains Mono, Consolas, monospace", fontSize: 11 },
      grid: { vertLines: { color: "#121212" }, horzLines: { color: "#121212" } },
      localization: { locale: "en-US" },
      crosshair: { mode: LW().CrosshairMode.Normal, vertLine: { color: "#ffa028", labelBackgroundColor: "#ffa028" }, horzLine: { color: "#ffa028", labelBackgroundColor: "#ffa028" } },
      rightPriceScale: { borderColor: "#262626", minimumWidth: 70 },
      timeScale: { borderColor: "#262626", timeVisible: true, secondsVisible: true },
    };
  }

  /** Renders one session's chart into `host` (a DOM element). data: the
   * /api/polymarket/sessions/{slug}/chart response. Returns the chart instance
   * (caller should .remove() it on teardown/resize-away). */
  // lightweight-charts wants strictly ascending unique times
  const series = (rows) => {
    const seen = new Map();
    rows.forEach((p) => seen.set(Math.floor(p.t), p.p));
    return [...seen.entries()].sort((a, b) => a[0] - b[0]).map(([time, value]) => ({ time, value }));
  };

  function renderSessionChart(host, data) {
    host.innerHTML = "";
    const chart = LW().createChart(host, chartOpts(host.clientWidth || 600, host.clientHeight || 320));

    const upSeries = chart.addLineSeries({ color: "#ffa028", lineWidth: 2, priceScaleId: "left", title: "UP price" });
    chart.priceScale("left").applyOptions({ visible: true, borderColor: "#262626" });
    upSeries.setData(series(data.up_price || []));

    if (data.spot && data.spot.length) {
      const spotSeries = chart.addLineSeries({ color: "#5aa9ff", lineWidth: 1.4, priceScaleId: "right", title: data.session.asset.toUpperCase() + " spot" });
      spotSeries.setData(series(data.spot));
      if (data.session.strike_price) {
        spotSeries.createPriceLine({
          price: data.session.strike_price, color: "#8b8b8b", lineWidth: 1, lineStyle: 2,
          axisLabelVisible: true, title: "strike",
        });
      }
    }

    const times = series(data.up_price || []).map((p) => p.time);
    const snap = (t) => { // markers must sit on an existing point: take the nearest one
      if (!times.length) return t;
      let best = times[0];
      for (const x of times) if (Math.abs(x - t) < Math.abs(best - t)) best = x;
      return best;
    };
    const markers = (data.trades || []).flatMap((t) => {
      const entryTs = snap(Math.floor(new Date(t.entry_time).getTime() / 1000));
      const exitTs = snap(Math.floor(new Date(t.exit_time).getTime() / 1000));
      const up = t.pnl >= 0;
      return [
        { time: entryTs, position: "belowBar", color: "#5aa9ff", shape: "arrowUp", text: `${t.side.toUpperCase()} @${Number(t.entry_price).toFixed(2)}` },
        { time: exitTs, position: "aboveBar", color: up ? "#2be07e" : "#ff4a4a", shape: up ? "arrowUp" : "arrowDown", text: `${t.exit_reason} ${up ? "+" : ""}${Number(t.pnl).toFixed(2)}` },
      ];
    });
    if (markers.length) upSeries.setMarkers(markers.sort((a, b) => a.time - b.time));

    chart.timeScale().fitContent();
    return chart;
  }

  window.NX = window.NX || {};
  window.NX.pmChart = { renderSessionChart };
})();

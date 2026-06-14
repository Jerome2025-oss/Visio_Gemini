(function () {
  "use strict";

  var priceChart = null;
  var volumeChart = null;
  var selectedKey = null;
  var simCache = {};

  function byId(id) {
    return document.getElementById(id);
  }

  function rowKey(token, flashTs) {
    return token + "|" + flashTs;
  }

  function getSimParams() {
    return {
      leverage: Number(byId("bt-leverage").value) || 20,
      tp: Number(byId("bt-tp").value) || 2.5,
      sl: Number(byId("bt-sl").value) || 1.5,
    };
  }

  function outcomeLabel(row) {
    var outcome = (row.outcome || "").toUpperCase();
    if (outcome === "TP") return { text: "🟢 TP" };
    if (outcome === "SL") return { text: "🔴 SL" };
    if (outcome === "LIQUIDATION") return { text: "💀 LIQ" };
    if (outcome === "TIMEOUT") return { text: "✅ CLÔTURÉ" };
    if (outcome === "OPEN" || row.status === "in_progress") return { text: "⏳ EN COURS" };
    var code = row.resultat || "";
    if (code === "TP") return { text: "🟢 TP" };
    if (code === "SL") return { text: "🔴 SL" };
    if (code === "EN_COURS") return { text: "⏳ EN COURS" };
    if (code === "CLO_24H") return { text: "✅ CLÔTURÉ" };
    return { text: "—" };
  }

  function tradeOutcomeCode(row) {
    var outcome = (row.outcome || "").toUpperCase();
    if (outcome) return outcome;
    var code = row.resultat || "";
    if (code === "TP") return "TP";
    if (code === "SL") return "SL";
    if (code === "EN_COURS") return "OPEN";
    if (code === "CLO_24H") return "TIMEOUT";
    return "";
  }

  function fmtPnlDisplay(row) {
    var raw = row.pnl_pct_raw != null ? row.pnl_pct_raw : row.pnl_pct;
    if (raw == null || raw === "" || raw === "—") return "—";
    if (typeof raw === "number") {
      var sign = raw > 0 ? "+" : "";
      var txt = sign + raw.toFixed(2) + "%";
      if (row.provisional || row.pnl_provisional) return "~" + txt;
      return txt;
    }
    return String(raw);
  }

  function fmtExitMinutes(mins, outcome) {
    if (mins == null) return "—";
    if (outcome === "TIMEOUT" && mins >= 1380) return "24h";
    if (mins < 60) return mins + " min";
    var h = Math.floor(mins / 60);
    var m = mins % 60;
    return m ? h + "h " + m + "m" : h + "h";
  }

  function destroyCharts() {
    if (priceChart) {
      priceChart.destroy();
      priceChart = null;
    }
    if (volumeChart) {
      volumeChart.destroy();
      volumeChart = null;
    }
  }

  function chartPriceBounds(candles, levels) {
    var prices = [];
    candles.forEach(function (c) {
      if (c.c != null) prices.push(Number(c.c));
      if (c.h != null) prices.push(Number(c.h));
      if (c.l != null) prices.push(Number(c.l));
    });
    [levels.tp, levels.sl, levels.liq, levels.exit].forEach(function (p) {
      if (p != null && Number.isFinite(Number(p))) prices.push(Number(p));
    });
    if (!prices.length) return null;
    var min = Math.min.apply(null, prices);
    var max = Math.max.apply(null, prices);
    var pad = (max - min) * 0.04 || max * 0.02 || 0.0001;
    return { min: min - pad, max: max + pad };
  }

  function drawHLine(chart, yScale, price, color, label, dash) {
    if (price == null || !Number.isFinite(price)) return;
    var y = yScale.getPixelForValue(price);
    if (y < chart.chartArea.top || y > chart.chartArea.bottom) return;
    var ctx = chart.ctx;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.setLineDash(dash || [5, 4]);
    ctx.beginPath();
    ctx.moveTo(chart.chartArea.left, y);
    ctx.lineTo(chart.chartArea.right, y);
    ctx.stroke();
    ctx.fillStyle = color;
    ctx.font = "11px system-ui";
    ctx.setLineDash([]);
    ctx.fillText(label, chart.chartArea.left + 6, y - 4);
    ctx.restore();
  }

  function renderChart(klinesData, simRow, simParams) {
    var section = byId("bt-chart-section");
    var symEl = byId("bt-chart-symbol");
    var metaEl = byId("bt-chart-meta");
    if (section) section.hidden = false;

    if (symEl) symEl.textContent = klinesData.symbol || simRow.token || "—";

    var oc = outcomeLabel(simRow);
    var ocCode = tradeOutcomeCode(simRow);
    var wickNote = "";
    if (ocCode === "TP" || ocCode === "SL") {
      wickNote =
        " · Simulation intra-bougie : TP/SL si le high/low de la minute 1m touche le niveau (mèche), pas seulement le close.";
    }
    if (metaEl) {
      metaEl.textContent =
        "FLASH " +
        (klinesData.flash_at_utc || simRow.flash_ts || "") +
        " UTC · " +
        oc.text +
        " · " +
        fmtExitMinutes(simRow.exit_minutes, ocCode) +
        " · PnL " +
        fmtPnlDisplay(simRow) +
        " · Levier " +
        simParams.leverage +
        "× TP +" +
        simParams.tp +
        "% SL -" +
        simParams.sl +
        "%" +
        wickNote;
    }

    destroyCharts();
    var candles = klinesData.candles || [];
    if (!candles.length) {
      if (metaEl) metaEl.textContent = "Aucune bougie disponible pour ce flash.";
      return;
    }

    var labels = candles.map(function (c) {
      return new Date(c.t).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
    });
    var closes = candles.map(function (c) {
      return c.c;
    });
    var highs = candles.map(function (c) {
      return c.h;
    });
    var lows = candles.map(function (c) {
      return c.l;
    });
    var volumes = candles.map(function (c) {
      return c.v;
    });
    var levels = {
      tp: simRow.tp_price,
      sl: simRow.sl_price,
      liq: simRow.liq_price,
      exit: simRow.exit_price,
      exitMs: simRow.exit_at_ms,
      outcome: ocCode,
      exitMin: simRow.exit_minutes,
    };
    var yBounds = chartPriceBounds(candles, levels);

    var priceCtx = byId("bt-price-chart").getContext("2d");
    priceChart = new Chart(priceCtx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Plus haut (mèche)",
            data: highs,
            borderColor: "rgba(63, 185, 80, 0.45)",
            backgroundColor: "transparent",
            fill: false,
            tension: 0.1,
            pointRadius: 0,
            borderWidth: 1,
            borderDash: [3, 3],
            order: 2,
          },
          {
            label: "Plus bas (mèche)",
            data: lows,
            borderColor: "rgba(248, 81, 73, 0.3)",
            backgroundColor: "transparent",
            fill: false,
            tension: 0.1,
            pointRadius: 0,
            borderWidth: 1,
            borderDash: [3, 3],
            order: 2,
          },
          {
            label: "Close",
            data: closes,
            borderColor: "#58a6ff",
            backgroundColor: "rgba(88, 166, 255, 0.08)",
            fill: true,
            tension: 0.15,
            pointRadius: 0,
            borderWidth: 2,
            order: 1,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: {
            ticks: { color: "#8b949e", maxTicksLimit: 14 },
            grid: { color: "rgba(48, 54, 61, 0.5)" },
          },
          y: {
            min: yBounds ? yBounds.min : undefined,
            max: yBounds ? yBounds.max : undefined,
            ticks: { color: "#8b949e" },
            grid: { color: "rgba(48, 54, 61, 0.5)" },
          },
        },
      },
      plugins: [
        {
          id: "btOverlays",
          afterDraw: function (chart) {
            var yScale = chart.scales.y;
            drawHLine(chart, yScale, levels.tp, "#3fb950", "TP", [6, 4]);
            drawHLine(chart, yScale, levels.sl, "#d29922", "SL", [6, 4]);
            drawHLine(chart, yScale, levels.liq, "#f85149", "LIQ", [4, 2]);

            var flashMs = klinesData.flash_at_ms;
            var xs = candles.map(function (c) {
              return c.t;
            });
            var flashIdx = xs.findIndex(function (t) {
              return t >= flashMs;
            });
            if (flashIdx >= 0) {
              var xFlash = chart.scales.x.getPixelForValue(flashIdx);
              var ctx = chart.ctx;
              ctx.save();
              ctx.strokeStyle = "#ef4444";
              ctx.lineWidth = 2;
              ctx.setLineDash([6, 4]);
              ctx.beginPath();
              ctx.moveTo(xFlash, chart.chartArea.top);
              ctx.lineTo(xFlash, chart.chartArea.bottom);
              ctx.stroke();
              ctx.fillStyle = "#ef4444";
              ctx.font = "11px system-ui";
              ctx.setLineDash([]);
              ctx.fillText("FLASH", xFlash + 4, chart.chartArea.top + 14);
              ctx.restore();
            }

            if (levels.exitMs) {
              var exitIdx = xs.findIndex(function (t) {
                return t >= levels.exitMs;
              });
              if (exitIdx >= 0) {
                var xExit = chart.scales.x.getPixelForValue(exitIdx);
                var exitCandle = candles[exitIdx];
                var ocLocal = levels.outcome || "";
                var ctx2 = chart.ctx;

                if (exitCandle && (ocLocal === "TP" || ocLocal === "SL")) {
                  var yWickTop = yScale.getPixelForValue(exitCandle.h);
                  var yWickBot = yScale.getPixelForValue(exitCandle.l);
                  ctx2.save();
                  ctx2.strokeStyle = ocLocal === "TP" ? "#3fb950" : "#d29922";
                  ctx2.lineWidth = 3;
                  ctx2.setLineDash([]);
                  ctx2.beginPath();
                  ctx2.moveTo(xExit, yWickBot);
                  ctx2.lineTo(xExit, yWickTop);
                  ctx2.stroke();
                  ctx2.restore();
                }

                var exitY =
                  levels.exit != null && Number.isFinite(levels.exit)
                    ? levels.exit
                    : closes[exitIdx];
                var yExit = yScale.getPixelForValue(exitY);
                yExit = Math.max(
                  chart.chartArea.top + 8,
                  Math.min(chart.chartArea.bottom - 8, yExit)
                );
                var dotColor =
                  ocLocal === "TP"
                    ? "#3fb950"
                    : ocLocal === "SL"
                      ? "#d29922"
                      : ocLocal === "LIQUIDATION"
                        ? "#f85149"
                        : "#8b949e";
                ctx2.save();
                ctx2.fillStyle = dotColor;
                ctx2.beginPath();
                ctx2.arc(xExit, yExit, 8, 0, Math.PI * 2);
                ctx2.fill();
                ctx2.strokeStyle = "#fff";
                ctx2.lineWidth = 2;
                ctx2.stroke();
                var lbl =
                  (ocLocal === "TIMEOUT" ? "CLÔTURÉ" : ocLocal === "TP" ? "TP (mèche)" : ocLocal || "SORTIE") +
                  (levels.exitMin != null ? " T+" + levels.exitMin + "min" : "");
                ctx2.fillStyle = dotColor;
                ctx2.font = "bold 11px system-ui";
                var labelY = Math.max(chart.chartArea.top + 12, yExit - 10);
                ctx2.fillText(lbl, Math.min(xExit + 10, chart.chartArea.right - 100), labelY);
                ctx2.restore();
              }
            }
          },
        },
      ],
    });

    var volCtx = byId("bt-volume-chart").getContext("2d");
    volumeChart = new Chart(volCtx, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Volume",
            data: volumes,
            backgroundColor: "rgba(136, 146, 164, 0.45)",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { display: false },
          y: {
            ticks: { color: "#8b949e", maxTicksLimit: 4 },
            grid: { color: "rgba(48, 54, 61, 0.3)" },
          },
        },
      },
    });

    section.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function mergeSimRow(cached, sim) {
    var row = Object.assign({}, cached || {}, sim || {});
    if (cached && cached.resultat && !row.resultat) row.resultat = cached.resultat;
    if (cached && cached.pnl_pct_raw != null) row.pnl_pct_raw = cached.pnl_pct_raw;
    if (cached && cached.provisional != null) row.provisional = cached.provisional;
    return row;
  }

  function setSelectedRow(key) {
    selectedKey = key;
    document.querySelectorAll("#bt-tbody tr[data-token]").forEach(function (tr) {
      var k = rowKey(tr.dataset.token, tr.dataset.flashTs);
      tr.classList.toggle("is-selected", k === key);
    });
  }

  function loadChart(token, flashTs) {
    var key = rowKey(token, flashTs);
    var metaEl = byId("bt-chart-meta");
    var section = byId("bt-chart-section");
    if (section) section.hidden = false;
    if (metaEl) metaEl.textContent = "Chargement du graphique…";
    setSelectedRow(key);

    var params = getSimParams();
    var cached = simCache[key];
    var q =
      "symbol=" +
      encodeURIComponent(token) +
      "&flash_ts=" +
      encodeURIComponent(flashTs) +
      "&leverage=" +
      params.leverage +
      "&tp=" +
      params.tp +
      "&sl=" +
      params.sl;

    fetch("/backtest/chart?" + q, { cache: "no-store" })
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        if (!data.ok) throw new Error(data.error || "Erreur graphique");
        var simRow = mergeSimRow(cached, data.sim);
        simRow.token = token;
        simRow.flash_ts = flashTs;
        renderChart(data.klines, simRow, params);
      })
      .catch(function (err) {
        if (metaEl) metaEl.textContent = "Erreur : " + (err.message || err);
      });
  }

  function wireTable() {
    document.querySelectorAll("#bt-tbody tr[data-token]").forEach(function (tr) {
      var token = tr.dataset.token;
      var flashTs = tr.dataset.flashTs;
      if (!token || !flashTs) return;

      tr.style.cursor = "pointer";
      tr.setAttribute("title", "Cliquer pour afficher le graphique");

      var tokenCell = tr.querySelector(".bt-token-link");
      if (tokenCell) {
        tokenCell.addEventListener("click", function (e) {
          e.stopPropagation();
          loadChart(token, flashTs);
        });
      }

      tr.addEventListener("click", function () {
        loadChart(token, flashTs);
      });
    });
  }

  window.BacktestChart = {
    updateCache: function (resultats) {
      simCache = {};
      (resultats || []).forEach(function (r) {
        if (r.token && r.flash_ts) {
          simCache[rowKey(r.token, r.flash_ts)] = r;
        }
      });
    },
    reloadSelected: function () {
      if (!selectedKey) return;
      var parts = selectedKey.split("|");
      if (parts.length >= 2) loadChart(parts[0], parts.slice(1).join("|"));
    },
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wireTable);
  } else {
    wireTable();
  }
})();

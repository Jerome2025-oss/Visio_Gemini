(function () {
  "use strict";

  const MONTHS = [
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
  ];
  const DOW = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"];
  const MIN_DATE = document.body.dataset.calendarMin || "2026-06-14";

  let dayMap = {};
  let ctxRef = null;
  let modalBound = false;

  function byId(id) {
    return document.getElementById(id);
  }

  function fmtPct(v) {
    if (v == null || !Number.isFinite(Number(v))) return "—";
    const n = Number(v);
    return (n > 0 ? "+" : "") + n.toFixed(2) + "%";
  }

  function pnlClass(v) {
    return Number(v) >= 0 ? "rp-pos" : "rp-neg";
  }

  function todayISO() {
    return new Date().toISOString().slice(0, 10);
  }

  function shiftDays(days) {
    const d = new Date();
    d.setUTCDate(d.getUTCDate() - days);
    return d.toISOString().slice(0, 10);
  }

  function esc(v) {
    const n = document.createElement("div");
    n.textContent = String(v == null ? "" : v);
    return n.innerHTML;
  }

  function fmtCellPct(v) {
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    if (Math.abs(n) >= 10000) return (n > 0 ? "+" : "") + (n / 1000).toFixed(0) + "k%";
    if (Math.abs(n) >= 1000) return (n > 0 ? "+" : "") + (n / 1000).toFixed(1) + "k%";
    return fmtPct(n);
  }

  function formatDateFr(iso) {
    if (!iso) return "—";
    const parts = String(iso).slice(0, 10).split("-");
    if (parts.length !== 3) return iso;
    const d = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
    const wd = ["Dimanche", "Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi"];
    return wd[d.getDay()] + " " + Number(parts[2]) + " " + MONTHS[Number(parts[1]) - 1] + " " + parts[0];
  }

  function fmtModalTime(iso, dayKey) {
    if (!iso) return "—";
    const s = String(iso).replace("T", " ");
    const day = s.slice(0, 10);
    const hm = s.slice(11, 16);
    if (day === dayKey) return hm;
    return day.slice(8, 10) + "/" + day.slice(5, 7) + " " + hm;
  }

  function fmtModalRange(entry, exit, dayKey) {
    return fmtModalTime(entry, dayKey) + " → " + fmtModalTime(exit, dayKey);
  }

  function fmtDurationShort(min) {
    if (min == null) return "—";
    const m = Number(min);
    if (m < 60) return m + "m";
    const h = Math.floor(m / 60);
    const r = m % 60;
    return r ? h + "h" + r : h + "h";
  }

  function dayToneClass(dayTotal) {
    return Number(dayTotal) >= 0 ? " rp-cal-cell--pos" : " rp-cal-cell--neg";
  }

  function isInRange(dateKey, rangeStart, rangeEnd) {
    return dateKey >= rangeStart && dateKey <= rangeEnd;
  }

  function fmtPctProv(v, provisional) {
    const base = fmtPct(v);
    return provisional && base !== "—" ? "~" + base : base;
  }

  function getActiveFiltres() {
    if (byId("rp-filtre-tous").checked) return [];
    const out = [];
    if (byId("rp-filtre-ichimoku").checked) out.push("ichimoku");
    if (byId("rp-filtre-btc").checked) out.push("btc");
    if (byId("rp-filtre-btc10").checked) out.push("btc10");
    return out;
  }

  function getActiveEtats() {
    return {
      btc_ok: byId("rp-filtre-btc-ok").checked,
      btc_reprise: byId("rp-filtre-btc-reprise").checked,
      btc_faible: byId("rp-filtre-btc-faible").checked,
      regime_oui: byId("rp-filtre-regime-oui").checked,
      regime_non: byId("rp-filtre-regime-non").checked,
    };
  }

  function onFiltreChange(source) {
    const tousEl = byId("rp-filtre-tous");
    const ichEl = byId("rp-filtre-ichimoku");
    const btcEl = byId("rp-filtre-btc");
    const btc10El = byId("rp-filtre-btc10");
    if (source === "tous" && tousEl.checked) {
      ichEl.checked = false;
      btcEl.checked = false;
      btc10El.checked = false;
    } else if (ichEl.checked || btcEl.checked || btc10El.checked) {
      tousEl.checked = false;
    } else {
      tousEl.checked = true;
    }
  }

  function formatDateShort(iso) {
    if (!iso || iso.length < 10) return iso || "";
    return iso.slice(8, 10) + "/" + iso.slice(5, 7) + "/" + iso.slice(0, 4);
  }

  function ensureDefaultDates() {
    const fromEl = byId("rp-from");
    const toEl = byId("rp-to");
    if (!fromEl.value) fromEl.value = MIN_DATE;
    if (!toEl.value) toEl.value = todayISO();
    if (toEl.value < fromEl.value) toEl.value = fromEl.value;
  }

  function applyPreset(kind) {
    const to = todayISO();
    let from = MIN_DATE;
    if (kind === "7") from = clampMinDate(shiftDays(6));
    else if (kind === "30") from = clampMinDate(shiftDays(29));
    else if (kind === "90") from = clampMinDate(shiftDays(89));
    byId("rp-from").value = from;
    byId("rp-to").value = to;
  }

  function clampMinDate(iso) {
    if (!iso || iso < MIN_DATE) return MIN_DATE;
    return iso;
  }

  function getParams() {
    ensureDefaultDates();
    let dateFrom = clampMinDate(byId("rp-from").value || MIN_DATE);
    let dateTo = byId("rp-to").value || todayISO();
    if (dateTo < dateFrom) dateTo = dateFrom;
    return {
      leverage: parseFloat(byId("rp-lev").value),
      tp: parseFloat(byId("rp-tp").value),
      sl: parseFloat(byId("rp-sl").value),
      date_from: dateFrom,
      date_to: dateTo,
      filtres: getActiveFiltres(),
      ...getActiveEtats(),
    };
  }

  function buildQuery(params) {
    const q = new URLSearchParams();
    q.set("leverage", String(params.leverage));
    q.set("tp", String(params.tp));
    q.set("sl", String(params.sl));
    if (params.date_from) q.set("date_from", params.date_from);
    if (params.date_to) q.set("date_to", params.date_to);
    q.set("btc_ok", params.btc_ok ? "true" : "false");
    q.set("btc_reprise", params.btc_reprise ? "true" : "false");
    q.set("btc_faible", params.btc_faible ? "true" : "false");
    q.set("regime_oui", params.regime_oui ? "true" : "false");
    q.set("regime_non", params.regime_non ? "true" : "false");
    (params.filtres || []).forEach((f) => q.append("filtres", f));
    return q.toString();
  }

  function setStatus(msg, isError) {
    const el = byId("rp-status");
    el.innerHTML = msg;
    el.style.color = isError ? "var(--red)" : "";
  }

  function fmtPeriodLabel(from, to) {
    return formatDateShort(from) + " → " + formatDateShort(to);
  }

  function statPill(label, value, cls) {
    return (
      '<div class="bt-stat-pill' + (cls ? " " + cls : "") + '">' +
      '<span class="bt-stat-pill__label">' + esc(label) + "</span>" +
      '<span class="bt-stat-pill__value">' + value + "</span></div>"
    );
  }

  function renderStats(stats) {
    const panel = byId("rp-calendar-stats");
    if (!panel || !stats) {
      if (panel) panel.hidden = true;
      return;
    }
    panel.hidden = false;
    const trades = stats.trades != null ? stats.trades : stats.total || 0;
    const tpPct = trades ? Math.round((stats.tp || 0) / trades * 100) + "%" : "0%";
    const slPct = trades ? Math.round((stats.sl || 0) / trades * 100) + "%" : "0%";
    const errPill = stats.err > 0
      ? statPill("⚠️ ERR", String(stats.err))
      : "";
    panel.innerHTML =
      '<div class="backtest-stats">' +
      '<div class="backtest-stats__group backtest-stats__group--counts">' +
      statPill("📊 Trades", String(trades)) +
      statPill("🟢 TP", stats.tp + ' <span class="bt-stat-pill__sub">(' + tpPct + ")</span>") +
      statPill("🔴 SL", stats.sl + ' <span class="bt-stat-pill__sub">(' + slPct + ")</span>") +
      statPill("⏳ EN COURS", String(stats.en_cours || 0)) +
      statPill("🕐 CLÔ 24H", String(stats.clo_24h || 0)) +
      errPill +
      "</div>" +
      '<div class="backtest-stats__sep" aria-hidden="true"></div>' +
      '<div class="backtest-stats__group backtest-stats__group--pnl">' +
      statPill("PnL réalisé", fmtPct(stats.pnl_realise), pnlClass(stats.pnl_realise)) +
      statPill(
        "PnL total",
        fmtPctProv(stats.pnl_total, stats.pnl_total_provisional),
        pnlClass(stats.pnl_total)
      ) +
      "</div></div>";
  }

  function formatDayShort(iso) {
    if (!iso || iso.length < 10) return "";
    return iso.slice(8, 10) + "/" + iso.slice(5, 7);
  }

  function kpi(label, value, signed) {
    let cls = "value";
    if (signed != null) cls += Number(signed) >= 0 ? " rp-pos" : " rp-neg";
    return (
      '<div class="rp-kpi"><span class="label">' + esc(label) +
      '</span><span class="' + cls + '">' + esc(value) + "</span></div>"
    );
  }

  function kpiDay(label, pnlText, dateIso, signed) {
    let cls = "value";
    if (signed != null) cls += Number(signed) >= 0 ? " rp-pos" : " rp-neg";
    const dayLabel = formatDayShort(dateIso);
    return (
      '<div class="rp-kpi rp-kpi--day"><span class="label">' + esc(label) +
      '</span><span class="' + cls + '">' + esc(pnlText) + "</span>" +
      (dayLabel ? '<span class="rp-kpi-date">' + esc(dayLabel) + "</span>" : "") +
      "</div>"
    );
  }

  function renderMonth(year, month, map, ctx, rangeStart, rangeEnd) {
    const daysInMonth = new Date(year, month, 0).getDate();
    const firstDow = (new Date(year, month - 1, 1).getDay() + 6) % 7;
    let cells = "";
    DOW.forEach((d) => { cells += '<div class="rp-cal-dow">' + d + "</div>"; });
    for (let b = 0; b < firstDow; b++) {
      cells += '<div class="rp-cal-cell rp-cal-cell--empty" aria-hidden="true"></div>';
    }
    for (let day = 1; day <= daysInMonth; day++) {
      const key = year + "-" + String(month).padStart(2, "0") + "-" + String(day).padStart(2, "0");
      const d = map[key];
      const today = todayISO();
      if (!d && isInRange(key, rangeStart, rangeEnd) && key >= today) {
        cells +=
          '<div class="rp-cal-cell rp-cal-cell--pending"><span class="rp-cal-daynum">' + day +
          '</span><span class="rp-cal-meta"><span class="rp-cal-pending-label">en attente</span></span></div>';
      } else if (!d && isInRange(key, rangeStart, rangeEnd)) {
        cells +=
          '<div class="rp-cal-cell rp-cal-cell--zero"><span class="rp-cal-daynum">' + day +
          '</span><span class="rp-cal-meta"><span class="rp-cal-zero-label">0 trade</span></span></div>';
      } else if (!d) {
        cells += '<div class="rp-cal-cell rp-cal-cell--none"><span class="rp-cal-daynum">' + day + "</span></div>";
      } else {
        const dayTotal = d.pnl_total != null ? d.pnl_total : d.pnl;
        const pnlCls = dayTotal >= 0 ? "rp-pos" : "rp-neg";
        const openN = Number(d.open_count) || 0;
        const dayPnl = fmtCellPct(dayTotal);
        const dayPnlDisplay = d.pnl_total_provisional && dayPnl !== "—" ? "~" + dayPnl : dayPnl;
        let countLine;
        if (openN >= d.n) {
          countLine = d.n + " trade" + (d.n > 1 ? "s" : "") + " · en cours";
        } else if (openN > 0) {
          countLine = d.n + " trades · WR " + d.win_rate + "% · " + openN + " en cours";
        } else {
          countLine = d.n + " trade" + (d.n > 1 ? "s" : "") + " · WR " + d.win_rate + "%";
        }
        const activeCls = openN > 0 ? " rp-cal-cell--live" : "";
        cells +=
          '<button type="button" class="rp-cal-cell rp-cal-cell--active' + dayToneClass(dayTotal) + activeCls +
          '" data-date="' + esc(key) +
          '"><span class="rp-cal-daynum">' + day +
          '</span><span class="rp-cal-meta"><span class="rp-cal-pnl ' + pnlCls + '">' +
          dayPnlDisplay +
          '</span><span class="rp-cal-count">' + esc(countLine) + "</span></span></button>";
      }
    }
    return (
      '<div class="rp-cal-month"><h3 class="rp-cal-month-title">' +
      MONTHS[month - 1] + " " + year +
      '</h3><div class="rp-cal-grid">' + cells + "</div></div>"
    );
  }

  function renderCalendar(data, ctx) {
    ctxRef = ctx;
    const days = data.days || [];
    const stats = ctx.stats || null;
    const container = byId("rp-calendar");
    const kpis = byId("rp-calendar-kpis");
    dayMap = {};

    renderStats(stats);

    if (!days.length) {
      container.innerHTML = '<p class="rp-empty">Aucun trade sur cette période.</p>';
      if (kpis) kpis.innerHTML = "";
      return;
    }

    let totalTrades = 0;
    let totalPnlRealise = 0;
    let totalPnl = 0;
    let hasOpenPnl = false;
    let best = days[0];
    let worst = days[0];
    days.forEach((d) => {
      dayMap[d.date] = d;
      const dayTotal = d.pnl_total != null ? d.pnl_total : d.pnl;
      totalTrades += d.n;
      totalPnlRealise += d.pnl_realise != null ? d.pnl_realise : 0;
      totalPnl += dayTotal;
      if (d.pnl_total_provisional) hasOpenPnl = true;
      if (dayTotal > (best.pnl_total != null ? best.pnl_total : best.pnl)) best = d;
      if (dayTotal < (worst.pnl_total != null ? worst.pnl_total : worst.pnl)) worst = d;
    });

    if (kpis) {
      const bestPnl = best.pnl_total != null ? best.pnl_total : best.pnl;
      const worstPnl = worst.pnl_total != null ? worst.pnl_total : worst.pnl;
      kpis.innerHTML =
        kpi("Jours tradés", days.length) +
        kpi("Trades", totalTrades) +
        kpi("PnL réalisé", fmtPct(totalPnlRealise), totalPnlRealise) +
        kpi("PnL total", fmtPctProv(totalPnl, hasOpenPnl), totalPnl) +
        kpiDay("Meilleur jour", fmtPctProv(bestPnl, best.pnl_total_provisional), best.date, bestPnl) +
        kpiDay("Pire jour", fmtPct(worstPnl), worst.date, worstPnl);
    }

    const first = days[0].date;
    const last = days[days.length - 1].date;
    const params = ctx.params || {};
    let rangeStart = params.date_from || first;
    let rangeEnd = params.date_to || todayISO();
    if (rangeEnd < last) rangeEnd = last;

    let html = "";
    let y = Number(rangeStart.slice(0, 4));
    let m = Number(rangeStart.slice(5, 7));
    const endY = Number(rangeEnd.slice(0, 4));
    const endM = Number(rangeEnd.slice(5, 7));
    while (y < endY || (y === endY && m <= endM)) {
      html += renderMonth(y, m, dayMap, ctx, rangeStart, rangeEnd);
      m += 1;
      if (m > 12) { m = 1; y += 1; }
    }
    container.innerHTML = html;
    container.onclick = function (ev) {
      const btn = ev.target.closest("[data-date]");
      if (btn) openDayModal(btn.getAttribute("data-date"));
    };
    bindModalOnce();
  }

  function bindModalOnce() {
    if (modalBound) return;
    modalBound = true;
    const modal = byId("rp-cal-modal");
    modal.querySelectorAll("[data-rp-close]").forEach((el) => {
      el.addEventListener("click", closeDayModal);
    });
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && modal && !modal.hidden) closeDayModal();
    });
  }

  function openDayModal(dateKey) {
    const d = dayMap[dateKey];
    const modal = byId("rp-cal-modal");
    if (!d || !modal || !ctxRef) return;

    byId("rp-cal-modal-title").textContent = formatDateFr(dateKey);
    const dayTotal = d.pnl_total != null ? d.pnl_total : d.pnl;
    byId("rp-cal-modal-summary").innerHTML =
      kpi("Trades", d.n) +
      kpi("PnL réalisé", fmtPct(d.pnl_realise), d.pnl_realise) +
      kpi("PnL total", fmtPctProv(dayTotal, d.pnl_total_provisional), dayTotal) +
      kpi("Win rate", d.win_rate + "%");

    const grid = byId("rp-cal-modal-trades");
    const trades = d.trades || [];
    if (!trades.length) {
      grid.innerHTML = '<p class="rp-empty rp-day-grid__empty">Aucun trade.</p>';
    } else {
      let cells =
        '<span class="rp-day-grid__head">Token</span>' +
        '<span class="rp-day-grid__head">Horaires</span>' +
        '<span class="rp-day-grid__head">Durée</span>' +
        '<span class="rp-day-grid__head">PnL</span>' +
        '<span class="rp-day-grid__head">Exit</span>';
      trades.forEach((t) => {
        const range = fmtModalRange(t.entry_ts, t.exit_ts, dateKey);
        cells +=
          '<span class="rp-day-grid__cell rp-day-grid__cell--token">' + esc(t.symbol) + "</span>" +
          '<span class="rp-day-grid__cell rp-day-grid__cell--range">' + esc(range) + "</span>" +
          '<span class="rp-day-grid__cell rp-day-grid__cell--dur">' + esc(fmtDurationShort(t.duration_min)) + "</span>" +
          '<span class="rp-day-grid__cell rp-day-grid__cell--pnl ' + pnlClass(t.pnl_pct) + '">' + fmtPct(t.pnl_pct) + "</span>" +
          '<span class="rp-day-grid__cell rp-day-grid__cell--exit">' +
          esc(t.exit_reason === "OPEN" && t.pnl_provisional ? "OPEN (prov.)" : t.exit_reason) +
          "</span>";
      });
      grid.innerHTML = cells;
    }

    modal.hidden = false;
    document.body.style.overflow = "hidden";
  }

  function closeDayModal() {
    const modal = byId("rp-cal-modal");
    if (!modal) return;
    modal.hidden = true;
    document.body.style.overflow = "";
  }

  async function runCalendar() {
    const params = getParams();
    const q = buildQuery(params);
    const btn = byId("rp-apply");
    btn.disabled = true;
    const t0 = Date.now();
    setStatus("Calcul simulation Visio Gemini…");
    try {
      const dataResp = await fetch("/reports/calendar/data?" + q);
      const payload = await dataResp.json();
      if (!dataResp.ok || !payload.ok) {
        throw new Error(payload.detail || payload.error || "Calendrier indisponible");
      }
      const secs = ((Date.now() - t0) / 1000).toFixed(1);
      const rangeFrom = payload.date_from || params.date_from;
      const rangeTo = payload.date_to || params.date_to;
      setStatus(
        fmtPeriodLabel(rangeFrom, rangeTo) + " · " +
        params.leverage + "× · TP " + params.tp + "% · SL " + params.sl + "% · " +
        (payload.n_trades || 0) + " trades · " + secs + "s"
      );
      renderCalendar(payload.data || payload, {
        params,
        stats: payload.stats || null,
        fmtPct,
        pnlClass,
      });
    } catch (err) {
      setStatus(err.message || String(err), true);
    } finally {
      btn.disabled = false;
    }
  }

  byId("rp-apply").addEventListener("click", runCalendar);
  document.querySelectorAll(".rp-preset").forEach((btn) => {
    btn.addEventListener("click", () => {
      applyPreset(btn.getAttribute("data-preset"));
    });
  });

  byId("rp-filtre-tous").addEventListener("change", () => onFiltreChange("tous"));
  byId("rp-filtre-ichimoku").addEventListener("change", () => onFiltreChange("ichimoku"));
  byId("rp-filtre-btc").addEventListener("change", () => onFiltreChange("btc"));
  byId("rp-filtre-btc10").addEventListener("change", () => onFiltreChange("btc10"));

  ensureDefaultDates();
})();

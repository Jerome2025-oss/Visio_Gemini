/**
 * Mode simulation backtest — pastilles OUI/NON modifiables (localStorage, jamais en DB).
 */
(function (global) {
  const STORAGE_KEY = "visio_regime_sim_overrides";
  const MODE_KEY = "visio_regime_sim_enabled";

  function slotKey(day, heure) {
    return String(day || "").trim() + "|" + String(heure || "").trim();
  }

  function etatColor(etat) {
    if (etat === "OUI") return "green";
    if (etat === "NON") return "red";
    return "yellow";
  }

  function getOverrides() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (e) {
      return {};
    }
  }

  function saveOverrides(map) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map || {}));
  }

  function isEnabled() {
    return localStorage.getItem(MODE_KEY) === "1";
  }

  function setEnabled(on) {
    localStorage.setItem(MODE_KEY, on ? "1" : "0");
  }

  function countOverrides() {
    return Object.keys(getOverrides()).length;
  }

  function hasActiveOverrides() {
    return countOverrides() > 0;
  }

  function isOverridden(day, heure) {
    return Object.prototype.hasOwnProperty.call(getOverrides(), slotKey(day, heure));
  }

  function getEffectiveEtat(row) {
    const k = slotKey(row.date, row.heure);
    const overrides = getOverrides();
    if (overrides[k]) return overrides[k];
    return row.etat;
  }

  function applyToRow(row) {
    const etat = getEffectiveEtat(row);
    const overridden = isOverridden(row.date, row.heure);
    return {
      ...row,
      etat: etat,
      etat_color: etatColor(etat),
      sim_override: overridden,
    };
  }

  function applyToRows(rows) {
    return (rows || []).map(applyToRow);
  }

  function toggleSlot(day, heure, currentEtat) {
    const k = slotKey(day, heure);
    const overrides = getOverrides();
    const base = currentEtat === "OUI" ? "OUI" : currentEtat === "NON" ? "NON" : "OUI";
    const next = base === "OUI" ? "NON" : "OUI";
    overrides[k] = next;
    saveOverrides(overrides);
    return next;
  }

  function clearOverrides() {
    localStorage.removeItem(STORAGE_KEY);
  }

  function overridesForApi() {
    const map = getOverrides();
    return Object.keys(map).length ? map : null;
  }

  function appendToUrlSearchParams(params) {
    const map = overridesForApi();
    if (map) {
      params.set("regime_overrides", JSON.stringify(map));
    }
  }

  function appendToBody(body) {
    const map = overridesForApi();
    if (map) {
      body.regime_overrides = map;
    }
    return body;
  }

  function refreshBanner() {
    const el = document.getElementById("regime-sim-banner");
    if (!el) return;
    const n = countOverrides();
    if (n > 0) {
      el.hidden = false;
      el.innerHTML =
        "🧪 Simulation ON/OFF active : <strong>" + n + "</strong> pastille(s) modifiée(s) " +
        "(non enregistrées en base). Modifier sur <a href=\"/btc-dates-onoff\">Date ON/OFF</a>.";
    } else {
      el.hidden = true;
    }
  }

  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", refreshBanner);
  }

  global.RegimeSim = {
    slotKey,
    etatColor,
    getOverrides,
    isEnabled,
    setEnabled,
    countOverrides,
    hasActiveOverrides,
    isOverridden,
    applyToRow,
    applyToRows,
    toggleSlot,
    clearOverrides,
    overridesForApi,
    appendToUrlSearchParams,
    appendToBody,
    refreshBanner,
  };
})(typeof window !== "undefined" ? window : globalThis);

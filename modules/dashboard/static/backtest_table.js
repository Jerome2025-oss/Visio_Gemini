(function () {
  "use strict";

  function applyOptimalTpCell(tr, row) {
    var cell = tr.querySelector(".bt-tp-optimal");
    if (!cell) return;
    var display = row.tp_optimal_display || "—";
    var cls = row.tp_optimal_class || "";
    cell.textContent = display;
    cell.className = "bt-tp-optimal" + (cls ? " " + cls : "");
  }

  window.BacktestTable = {
    applyOptimalTpCell: applyOptimalTpCell,
  };
})();

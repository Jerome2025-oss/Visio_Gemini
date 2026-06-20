(function () {
  async function refreshMarketSpot() {
    var root = document.getElementById("market-spot");
    if (!root) return;
    try {
      var resp = await fetch("/api/market-spot", {
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) return;
      var data = await resp.json();
      ["btc", "eth"].forEach(function (key) {
        var pair = root.querySelector('[data-symbol="' + key + '"]');
        if (!pair || !data[key]) return;
        var priceEl = pair.querySelector(".market-spot__price");
        if (priceEl && data[key].price_display) {
          priceEl.textContent = data[key].price_display;
        }
      });
    } catch (_err) {
      /* ignore */
    }
  }

  refreshMarketSpot();
  window.setInterval(refreshMarketSpot, 30000);
})();

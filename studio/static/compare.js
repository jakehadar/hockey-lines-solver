(function () {
  "use strict";

  const scenarios = window.SCENARIOS || [];
  const grid = document.getElementById("compare-grid");

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str || "";
    return div.innerHTML;
  }

  function statusClass(status) {
    if (status === "primary") return "badge status-primary";
    if (status === "secondary") return "badge status-secondary";
    return "badge status-oop";
  }

  function renderSlot(a) {
    if (!a) return '<div class="slot empty">&mdash;</div>';
    return (
      '<div class="slot">' +
      '<span class="slot-pos">' + a.position + "</span>" +
      '<span class="slot-name">' + escapeHtml(a.player_name) + "</span>" +
      '<span class="' + statusClass(a.status) + '">' + a.status + "</span>" +
      "</div>"
    );
  }

  function renderResult(result) {
    if (!result || result.status === "NO_SOLUTION") {
      return '<p class="empty">No feasible solution.</p>';
    }
    let html = '<div class="lines-grid">';
    for (const fl of result.forward_lines) {
      html +=
        '<div class="line-card"><h3>Forward ' + fl.line_number + ' <span class="exp">exp ' + fl.exp_sum + '</span></h3>' +
        fl.slots.map(renderSlot).join("") +
        '<div class="line-counts">primary ' + fl.primary_count + ' · secondary ' + fl.secondary_count + ' · oop ' + fl.oop_count + '</div>' +
        '</div>';
    }
    for (const dp of result.defense_pairs) {
      html +=
        '<div class="line-card"><h3>Defense ' + dp.pair_number + (dp.partial ? ' <span class="exp">partial</span>' : '') + '</h3>' +
        dp.slots.map(renderSlot).join("") +
        '<div class="line-counts">primary ' + dp.primary_count + ' · secondary ' + dp.secondary_count + ' · oop ' + dp.oop_count + '</div>' +
        '</div>';
    }
    html += "</div>";

    const s = result.summary;
    html +=
      '<div class="summary-stats">' +
      '<div><span class="stat-label">Available</span><span class="stat-val">' + s.available_players + '</span></div>' +
      '<div><span class="stat-label">Forwards</span><span class="stat-val">' + s.forwards_used + '/' + s.forwards_requested + '</span></div>' +
      '<div><span class="stat-label">Defense</span><span class="stat-val">' + s.defense_pairs_used + '/' + s.defense_requested + '</span></div>' +
      '<div><span class="stat-label">Assigned</span><span class="stat-val">' + s.total_assigned + '</span></div>' +
      '<div><span class="stat-label">Primary</span><span class="stat-val">' + s.total_primary + '</span></div>' +
      '<div><span class="stat-label">Secondary</span><span class="stat-val">' + s.total_secondary + '</span></div>' +
      '<div><span class="stat-label">OOP</span><span class="stat-val">' + s.total_oop + '</span></div>' +
      '</div>';
    return html;
  }

  grid.innerHTML = scenarios
    .map(function (sc) {
      return (
        '<div class="compare-column">' +
        '<div class="compare-column-header">' +
        '<h2>' + escapeHtml(sc.title) + (sc.is_baseline ? ' <span class="badge scenario">Baseline</span>' : '') + '</h2>' +
        (sc.description ? '<p class="compare-description">' + escapeHtml(sc.description) + '</p>' : '') +
        '<p class="compare-settings">Forwards ' + sc.forwards + ' &middot; Defense ' + sc.defense + '</p>' +
        (sc.load_url ? '<a class="compare-load-link" href="' + sc.load_url + '">Load into editor&hellip;</a>' : '') +
        '</div>' +
        renderResult(sc.result) +
        '</div>'
      );
    })
    .join("");
})();

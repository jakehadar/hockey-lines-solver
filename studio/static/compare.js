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

  function renderDof(dof) {
    // Cached at save time (see dof.py) - never recomputed here, since it's
    // many times more expensive than a single solve and this is a read-only
    // comparison view. Absent for scenarios saved before this existed, or
    // saved before the analysis had finished computing client-side.
    if (!dof || dof.status === "NO_SOLUTION") return "";
    const tiles = dof.by_position
      .map(function (pf) {
        const cls = pf.extra_options === 0 ? "stat-rigid" : "";
        return (
          '<div class="' + cls + '">' +
          '<span class="stat-label">' + pf.position + "</span>" +
          '<span class="stat-val">' + pf.extra_options + "/" + pf.candidates_checked + "</span>" +
          "</div>"
        );
      })
      .join("");
    return (
      '<div class="dof-panel">' +
      "<h2>DOF Analysis</h2>" +
      '<div class="dof-score-row"><span class="stat-label">Net flexibility</span><span class="stat-val">' +
      dof.score_per_slot.toFixed(2) +
      " / slot</span></div>" +
      '<div class="summary-stats dof-breakdown">' + tiles + "</div>" +
      "</div>"
    );
  }

  grid.innerHTML = scenarios
    .map(function (sc) {
      return (
        '<div class="compare-column">' +
        '<div class="compare-column-header">' +
        '<h2>' + escapeHtml(sc.title) + '</h2>' +
        (sc.description ? '<p class="compare-description">' + escapeHtml(sc.description) + '</p>' : '') +
        '<p class="compare-settings">Forwards ' + sc.forwards + ' &middot; Defense ' + sc.defense +
        ' &middot; Allow OOP ' + (sc.allow_oop === false ? 'No' : 'Yes') +
        ' &middot; Allow Unwilling ' + (sc.allow_unwilling === true ? 'Yes' : 'No') + '</p>' +
        '<a class="compare-load-link" href="' + sc.load_url + '">Load into editor&hellip;</a>' +
        '</div>' +
        renderResult(sc.result) +
        renderDof(sc.dof) +
        '</div>'
      );
    })
    .join("");
})();

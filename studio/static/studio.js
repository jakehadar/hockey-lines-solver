(function () {
  "use strict";

  const ROSTER_ID = window.ROSTER_ID;
  const POSITIONS = window.POSITIONS;

  let players = JSON.parse(JSON.stringify(window.INITIAL_ROSTER));
  let baseline = JSON.parse(JSON.stringify(window.INITIAL_ROSTER));
  let lastResult = null;
  let debounceTimer = null;

  const tbody = document.getElementById("roster-tbody");
  const gridEl = document.getElementById("grid");
  const summaryEl = document.getElementById("summary");
  const bannerEl = document.getElementById("status-banner");
  const autoToggle = document.getElementById("auto-solve-toggle");
  const titleInput = document.getElementById("roster-title");

  function nextId(prefix) {
    let max = 0;
    for (const p of players) {
      const m = new RegExp("^" + prefix + "(\\d+)$").exec(p.id);
      if (m) max = Math.max(max, parseInt(m[1], 10));
    }
    return prefix + String(max + 1).padStart(2, "0");
  }

  function isAlt(player) {
    return /^A\d+/.test(player.id);
  }

  function positionChips(player, field) {
    return POSITIONS.map((pos) => {
      const checked = player[field].includes(pos) ? "checked" : "";
      return (
        '<label class="chip"><input type="checkbox" data-field="' +
        field +
        '" data-pos="' +
        pos +
        '" ' +
        checked +
        "> " +
        pos +
        "</label>"
      );
    }).join("");
  }

  function selectOptions(options, selected) {
    return options
      .map(
        ([value, label]) =>
          '<option value="' +
          value +
          '"' +
          (value === (selected || "") ? " selected" : "") +
          ">" +
          label +
          "</option>"
      )
      .join("");
  }

  function renderRoster() {
    tbody.innerHTML = players
      .map((p, idx) => {
        const alt = isAlt(p);
        const overrideOptions = [["", "none"]].concat(POSITIONS.map((pos) => [pos, pos]));
        const linkOptions = [["", "none"]].concat(
          players.filter((o) => o.id !== p.id).map((o) => [o.id, o.name || o.id])
        );
        return (
          '<tr data-idx="' +
          idx +
          '" class="' +
          (alt ? "alt-row" : "") +
          '">' +
          '<td>' + (alt ? '<span class="badge alt">ALT</span>' : '<span class="badge">P</span>') + "</td>" +
          '<td><input type="text" data-field="name" value="' + (p.name || "") + '"></td>' +
          '<td><input type="number" min="1" max="5" data-field="experience" value="' + p.experience + '" style="width:3.5em"></td>' +
          '<td><input type="checkbox" data-field="available" ' + (p.available ? "checked" : "") + "></td>" +
          '<td class="chips">' + positionChips(p, "preferred_positions") + "</td>" +
          '<td class="chips">' + positionChips(p, "secondary_positions") + "</td>" +
          '<td class="chips">' + positionChips(p, "unwilling_positions") + "</td>" +
          '<td><select data-field="optional_position_override">' + selectOptions(overrideOptions, p.optional_position_override) + "</select></td>" +
          '<td><select data-field="optional_player_link">' + selectOptions(linkOptions, p.optional_player_link) + "</select></td>" +
          '<td><button type="button" data-action="delete" title="Remove">&times;</button></td>' +
          "</tr>"
        );
      })
      .join("");
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
      '<span class="slot-name">' + a.player_name + "</span>" +
      '<span class="' + statusClass(a.status) + '">' + a.status + "</span>" +
      "</div>"
    );
  }

  function renderGrid() {
    if (!lastResult || lastResult.status === "NO_SOLUTION") {
      gridEl.innerHTML = '<p class="empty">No lines to show.</p>';
      summaryEl.innerHTML = "";
      return;
    }
    let html = '<div class="lines-grid">';
    for (const fl of lastResult.forward_lines) {
      html +=
        '<div class="line-card"><h3>Forward ' + fl.line_number + " <span class=\"exp\">exp " + fl.exp_sum + "</span></h3>" +
        fl.slots.map(renderSlot).join("") +
        '<div class="line-counts">primary ' + fl.primary_count + " · secondary " + fl.secondary_count + " · oop " + fl.oop_count + "</div>" +
        "</div>";
    }
    for (const dp of lastResult.defense_pairs) {
      html +=
        '<div class="line-card"><h3>Defense ' + dp.pair_number + (dp.partial ? ' <span class="exp">partial</span>' : "") + "</h3>" +
        dp.slots.map(renderSlot).join("") +
        '<div class="line-counts">primary ' + dp.primary_count + " · secondary " + dp.secondary_count + " · oop " + dp.oop_count + "</div>" +
        "</div>";
    }
    html += "</div>";
    gridEl.innerHTML = html;

    const s = lastResult.summary;
    summaryEl.innerHTML =
      '<div class="summary-stats">' +
      '<div><span class="stat-label">Available</span><span class="stat-val">' + s.available_players + "</span></div>" +
      '<div><span class="stat-label">Forwards</span><span class="stat-val">' + s.forwards_used + "/" + s.forwards_requested + "</span></div>" +
      '<div><span class="stat-label">Defense</span><span class="stat-val">' + s.defense_pairs_used + "/" + s.defense_requested + "</span></div>" +
      '<div><span class="stat-label">Assigned</span><span class="stat-val">' + s.total_assigned + "</span></div>" +
      '<div><span class="stat-label">Primary</span><span class="stat-val">' + s.total_primary + "</span></div>" +
      '<div><span class="stat-label">Secondary</span><span class="stat-val">' + s.total_secondary + "</span></div>" +
      '<div><span class="stat-label">OOP</span><span class="stat-val">' + s.total_oop + "</span></div>" +
      "</div>";
  }

  function renderBanner() {
    if (!lastResult) {
      bannerEl.hidden = true;
      return;
    }
    bannerEl.hidden = false;
    if (lastResult.status === "NO_SOLUTION") {
      bannerEl.className = "status-banner infeasible";
      bannerEl.textContent = "No feasible solution for the current roster/constraints. Adjust and retry, or hit Reset.";
    } else {
      bannerEl.className = "status-banner ok";
      bannerEl.textContent = "Status: " + lastResult.status;
    }
  }

  function render() {
    renderRoster();
    renderGrid();
    renderBanner();
  }

  function scheduleSolve() {
    if (!autoToggle.checked) return;
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(doSolve, 400);
  }

  async function doSolve() {
    const forwards = parseInt(document.getElementById("setting-forwards").value, 10) || 0;
    const defense = parseInt(document.getElementById("setting-defense").value, 10) || 0;
    const timeLimit = parseInt(document.getElementById("setting-time-limit").value, 10) || 5;
    const resp = await fetch("/studio/" + ROSTER_ID + "/solve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ players: players, forwards: forwards, defense: defense, time_limit: timeLimit }),
    });
    if (!resp.ok) {
      lastResult = { status: "NO_SOLUTION" };
      renderGrid();
      renderBanner();
      return;
    }
    lastResult = await resp.json();
    renderGrid();
    renderBanner();
  }

  tbody.addEventListener("change", (e) => {
    const tr = e.target.closest("tr[data-idx]");
    if (!tr) return;
    const idx = parseInt(tr.dataset.idx, 10);
    const field = e.target.dataset.field;
    if (!field) return;
    const player = players[idx];
    if (field === "name") {
      player.name = e.target.value;
    } else if (field === "experience") {
      player.experience = parseInt(e.target.value, 10) || 1;
    } else if (field === "available") {
      player.available = e.target.checked ? 1 : 0;
    } else if (field === "optional_position_override") {
      player.optional_position_override = e.target.value || null;
    } else if (field === "optional_player_link") {
      player.optional_player_link = e.target.value || null;
    } else if (["preferred_positions", "secondary_positions", "unwilling_positions"].includes(field)) {
      const pos = e.target.dataset.pos;
      const list = player[field];
      const at = list.indexOf(pos);
      if (e.target.checked && at === -1) list.push(pos);
      if (!e.target.checked && at !== -1) list.splice(at, 1);
    }
    scheduleSolve();
  });

  tbody.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action='delete']");
    if (!btn) return;
    const tr = btn.closest("tr[data-idx]");
    const idx = parseInt(tr.dataset.idx, 10);
    players.splice(idx, 1);
    render();
    scheduleSolve();
  });

  for (const id of ["setting-forwards", "setting-defense", "setting-time-limit"]) {
    document.getElementById(id).addEventListener("change", scheduleSolve);
  }

  document.getElementById("add-player-btn").addEventListener("click", () => {
    players.push({
      id: nextId("P"),
      name: "",
      available: 1,
      experience: 1,
      preferred_positions: [],
      secondary_positions: [],
      unwilling_positions: [],
      optional_position_override: null,
      optional_player_link: null,
    });
    render();
  });

  document.getElementById("add-alt-btn").addEventListener("click", () => {
    players.push({
      id: nextId("A"),
      name: "",
      available: 1,
      experience: 3,
      preferred_positions: POSITIONS.slice(),
      secondary_positions: [],
      unwilling_positions: [],
      optional_position_override: null,
      optional_player_link: null,
    });
    render();
  });

  document.getElementById("reset-btn").addEventListener("click", () => {
    players = JSON.parse(JSON.stringify(baseline));
    render();
    scheduleAutoOrManualReset();
  });

  function scheduleAutoOrManualReset() {
    // Always re-solve after a reset so the grid reflects the restored baseline,
    // regardless of the auto-solve toggle.
    doSolve();
  }

  document.getElementById("solve-now-btn").addEventListener("click", doSolve);

  autoToggle.addEventListener("change", () => {
    if (autoToggle.checked) scheduleSolve();
  });

  document.getElementById("save-btn").addEventListener("click", async () => {
    const resp = await fetch("/studio/" + ROSTER_ID + "/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ players: players }),
    });
    if (resp.ok) {
      baseline = JSON.parse(JSON.stringify(players));
    } else {
      alert("Save failed.");
    }
  });

  document.getElementById("save-as-btn").addEventListener("click", async () => {
    const title = prompt("Title for the new roster:", titleInput.value + " (copy)");
    if (!title) return;
    const resp = await fetch("/studio/" + ROSTER_ID + "/save-as", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: title, players: players }),
    });
    if (resp.ok) {
      const data = await resp.json();
      window.location = "/studio/" + data.roster_id;
    } else {
      alert("Save As failed.");
    }
  });

  render();
  doSolve();
})();

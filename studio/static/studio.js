(function () {
  "use strict";

  const ROSTER_ID = window.ROSTER_ID;
  const POSITIONS = window.POSITIONS;
  const STUDIO_BASE = "/w/" + window.WORKSPACE_TOKEN + "/studio/" + ROSTER_ID;

  const root = document.getElementById("studio-root");
  const serverLoadedScenario = window.LOADED_SCENARIO || null;

  // A given position can only ever be claimed by one of these three columns
  // at once - this order is also the priority used to resolve any ambiguous
  // data found on load (e.g. from an older save, or a CSV upload where the
  // three source columns weren't cross-checked against each other).
  const POSITION_FIELDS = ["preferred_positions", "secondary_positions", "unwilling_positions"];

  function dedupePositions(player) {
    for (const pos of POSITIONS) {
      let claimed = false;
      for (const field of POSITION_FIELDS) {
        const at = player[field].indexOf(pos);
        if (at === -1) continue;
        if (claimed) player[field].splice(at, 1);
        else claimed = true;
      }
    }
  }

  // Editor mode and auto-solve are per-browser preferences, not roster data -
  // localStorage, not the server, so they never clash across different
  // browsers sharing the same workspace link. Wrapped in try/catch since
  // storage access can throw (private browsing, blocked site data, etc.).
  const MODE_STORAGE_KEY = "studio.lastMode." + ROSTER_ID;
  const AUTO_SOLVE_STORAGE_KEY = "studio.autoSolve";

  function loadStoredMode() {
    try {
      const stored = localStorage.getItem(MODE_STORAGE_KEY);
      return stored === "roster" || stored === "scenario" ? stored : null;
    } catch (e) {
      return null;
    }
  }

  function storeMode(newMode) {
    try {
      localStorage.setItem(MODE_STORAGE_KEY, newMode);
    } catch (e) {
      // Best-effort - losing the remembered mode just means next visit
      // falls back to the default, which is a fine outcome.
    }
  }

  function loadStoredAutoSolve() {
    try {
      const stored = localStorage.getItem(AUTO_SOLVE_STORAGE_KEY);
      return stored === null ? true : stored === "1";
    } catch (e) {
      return true;
    }
  }

  function storeAutoSolve(enabled) {
    try {
      localStorage.setItem(AUTO_SOLVE_STORAGE_KEY, enabled ? "1" : "0");
    } catch (e) {
      // Best-effort, same as storeMode.
    }
  }

  // rosterBaseline is the roster's own saved players - the only baseline
  // Roster mode ever compares against, and what Reset always falls back to.
  let rosterBaseline = JSON.parse(JSON.stringify(window.INITIAL_ROSTER));
  rosterBaseline.forEach(dedupePositions);
  // Same idea for the roster's title - only meaningful/editable in Roster
  // mode, so scenario mode never reads or resets it. Set once titleInput
  // exists, just below.
  let rosterTitleBaseline;

  // scenarioOrigin is what "clean" means in Scenario mode: the loaded
  // scenario's snapshot, or the roster baseline if nothing's loaded (a
  // fresh scenario session starts from the current roster). Switching INTO
  // Scenario mode always resets `players` to this, never carries over
  // unsaved edits from a previous visit to the mode.
  let loadedScenario = serverLoadedScenario ? { id: serverLoadedScenario.id, title: serverLoadedScenario.title } : null;
  let scenarioOrigin = serverLoadedScenario
    ? JSON.parse(JSON.stringify(serverLoadedScenario.players))
    : JSON.parse(JSON.stringify(rosterBaseline));
  scenarioOrigin.forEach(dedupePositions);
  // The settings/result that go with scenarioOrigin, so re-entering Scenario
  // mode with the same scenario still loaded can restore them instead of
  // re-solving something unchanged. Null whenever there's nothing cached for
  // the current origin (nothing loaded, or unloaded via Reset).
  let scenarioOriginMeta = serverLoadedScenario
    ? {
        forwards: serverLoadedScenario.forwards,
        defense: serverLoadedScenario.defense,
        time_limit: serverLoadedScenario.time_limit,
        result: serverLoadedScenario.result,
        dof: serverLoadedScenario.dof,
      }
    : null;

  // Loading a scenario only ever makes sense in Scenario mode, full stop -
  // otherwise fall back to whatever this browser last had this roster in,
  // or Roster mode if nothing's stored (a plain roster link's natural
  // starting point, since there's nothing to solve yet on a brand-new one).
  let mode = serverLoadedScenario ? "scenario" : loadStoredMode() || "roster";
  let players = JSON.parse(JSON.stringify(mode === "roster" ? rosterBaseline : scenarioOrigin));
  let lastResult = serverLoadedScenario ? serverLoadedScenario.result : null;
  // Cached alongside lastResult when a scenario snapshot already has a dof
  // analysis saved with it - sent back along with the next save so it isn't
  // silently dropped, and lets re-entering an unchanged scenario show it
  // instantly instead of waiting on a fresh (expensive) recomputation.
  let lastDofResult = serverLoadedScenario ? serverLoadedScenario.dof : null;
  let debounceTimer = null;
  // True whenever lastResult may not reflect the players/settings currently
  // on screen - from the moment an edit schedules a solve (even before the
  // debounce timer fires) until that solve's response comes back.
  let resultPending = !serverLoadedScenario;
  let scenarioTitles = (window.EXISTING_SCENARIO_TITLES || []).slice();
  let pendingModeSwitch = null;

  const tbody = document.getElementById("roster-tbody");
  const gridEl = document.getElementById("grid");
  const summaryEl = document.getElementById("summary");
  const resultsPanelEl = document.getElementById("results-panel");
  const pendingIndicatorEl = document.getElementById("solve-pending-indicator");
  const dofPanelEl = document.getElementById("dof-panel");
  const dofPendingIndicatorEl = document.getElementById("dof-pending-indicator");
  const dofScoreEl = document.getElementById("dof-score-val");
  const dofBreakdownBodyEl = document.getElementById("dof-breakdown-body");
  // Bumped on every new solve; a dof fetch discards its response if this has
  // moved on by the time it lands, so a superseded (but not necessarily
  // network-cancelled) request can't clobber a newer result.
  let dofGeneration = 0;
  let dofAbortController = null;
  // True from the moment a new solve is scheduled until dof's own (slower)
  // response lands - independent of resultPending, since dof keeps computing
  // after the main solve has already resolved and rendered. Drives dimming
  // only; last-known numbers stay on screen underneath rather than being
  // wiped, same treatment as the rest of the stale results panel.
  let dofPending = false;
  const bannerEl = document.getElementById("status-banner");
  const autoToggle = document.getElementById("auto-solve-toggle");
  const titleInput = document.getElementById("roster-title");
  rosterTitleBaseline = titleInput.value;

  // Grows/shrinks the title input to fit its content instead of clipping
  // long roster names at a fixed width.
  function autosizeTitleInput() {
    titleInput.size = Math.max(titleInput.value.length, 8) + 1;
  }
  autosizeTitleInput();
  titleInput.addEventListener("input", autosizeTitleInput);
  titleInput.addEventListener("input", () => updateDirtyState());
  const resetBtn = document.getElementById("reset-btn");
  const panelHeadingEl = document.getElementById("roster-panel-heading");
  const modeToggle = document.getElementById("mode-toggle");
  const solveNowBtn = document.getElementById("solve-now-btn");
  const saveBtn = document.getElementById("save-btn");
  const saveMenu = document.getElementById("save-menu");
  const saveMenuToggle = document.getElementById("save-menu-toggle");
  const saveAltBtn = document.getElementById("save-alt-btn");
  const scenarioDialog = document.getElementById("scenario-dialog");
  const scenarioDialogTitle = document.getElementById("scenario-dialog-title");
  const scenarioForm = document.getElementById("scenario-form");
  const scenarioTitleInput = document.getElementById("scenario-title");
  const scenarioDescriptionInput = document.getElementById("scenario-description");
  const scenarioSubmitBtn = document.getElementById("scenario-submit-btn");
  const unsavedDialog = document.getElementById("unsaved-dialog");
  const unsavedDialogBody = document.getElementById("unsaved-dialog-body");

  function currentOrigin() {
    return mode === "roster" ? rosterBaseline : scenarioOrigin;
  }

  // Row order (sortable in the UI) isn't meaningful data - only compare by
  // content, never by position, so sorting alone never counts as "dirty".
  function canonicalPlayersJSON(list) {
    return JSON.stringify([...list].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)));
  }

  function isDirty() {
    const playersDirty = canonicalPlayersJSON(players) !== canonicalPlayersJSON(currentOrigin());
    if (mode === "roster") {
      return playersDirty || titleInput.value !== rosterTitleBaseline;
    }
    return playersDirty;
  }

  function isRosterPlayer(player) {
    return rosterBaseline.some((p) => p.id === player.id);
  }

  function updateDirtyState() {
    resetBtn.disabled = !isDirty();
  }

  function updateSaveButtonState() {
    const disabled = mode === "scenario" && resultPending;
    saveBtn.disabled = disabled;
    saveAltBtn.disabled = disabled;
    saveMenuToggle.disabled = disabled;
    // Also covers "Solve now": a solve already in flight (or about to be,
    // once the debounce timer fires) shouldn't be kickable again on top of
    // itself.
    solveNowBtn.disabled = disabled;
  }

  function updateSaveButtonLabels() {
    if (mode === "roster") {
      saveBtn.textContent = "Save roster";
      saveAltBtn.textContent = "Save roster as…";
    } else {
      saveBtn.textContent = "Save scenario";
      saveAltBtn.textContent = "Branch scenario…";
    }
  }

  function updatePanelHeading() {
    if (mode === "roster") {
      panelHeadingEl.textContent = "Roster";
    } else if (loadedScenario) {
      panelHeadingEl.textContent = "Scenario: " + loadedScenario.title;
    } else {
      panelHeadingEl.textContent = "Scenario";
    }
  }

  function currentSettings() {
    return {
      forwards: parseInt(document.getElementById("setting-forwards").value, 10) || 0,
      defense: parseInt(document.getElementById("setting-defense").value, 10) || 0,
      time_limit: parseInt(document.getElementById("setting-time-limit").value, 10) || 5,
    };
  }

  function nextScenarioTitle() {
    let max = 0;
    const re = /^Scenario (\d+)$/;
    for (const t of scenarioTitles) {
      const m = re.exec(t);
      if (m) max = Math.max(max, parseInt(m[1], 10));
    }
    return "Scenario " + (max + 1);
  }

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

  function nextPlayerName() {
    const count = players.filter((p) => !isAlt(p)).length;
    return "Player " + (count + 1);
  }

  function nextAltName() {
    const count = players.filter(isAlt).length;
    return "Alt " + (count + 1);
  }

  // Sorting is a one-time reorder triggered by clicking a column header, not
  // something continuously reapplied on every render - otherwise a row would
  // jump around under the user's cursor while they're still editing it.
  let sortField = null;
  let sortDir = 1;

  function sortPlayers(field) {
    sortDir = sortField === field ? -sortDir : 1;
    sortField = field;
    players.sort((a, b) => {
      if (field === "name") {
        return sortDir * (a.name || "").toLowerCase().localeCompare((b.name || "").toLowerCase());
      }
      return sortDir * ((a[field] || 0) - (b[field] || 0));
    });
    render();
  }

  function updateSortIndicators() {
    document.querySelectorAll("th[data-sort]").forEach((th) => {
      const active = th.dataset.sort === sortField;
      th.innerHTML = th.dataset.label + '<span class="sort-arrow">' + (active ? (sortDir === 1 ? "▲" : "▼") : "") + "</span>";
    });
  }

  document.querySelectorAll("th.sortable").forEach((th) => {
    th.addEventListener("click", () => sortPlayers(th.dataset.sort));
  });

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
        const locked = mode === "scenario" && isRosterPlayer(p);
        const overrideOptions = [["", "none"]].concat(POSITIONS.map((pos) => [pos, pos]));
        const linkOptions = [["", "none"]].concat(
          players.filter((o) => o.id !== p.id).map((o) => [o.id, o.name || o.id])
        );
        return (
          '<tr data-idx="' +
          idx +
          '" class="' +
          (alt ? "alt-row " : "") +
          (locked ? "locked-row" : "") +
          '">' +
          '<td>' + (alt ? '<span class="badge alt">ALT</span>' : '<span class="badge">P</span>') + "</td>" +
          '<td data-col="name"><input type="text" data-field="name" value="' + (p.name || "") + '"' + (locked ? " readonly" : "") + "></td>" +
          '<td><input type="number" min="1" max="5" data-field="experience" value="' + p.experience + '" style="width:3.5em"></td>' +
          '<td class="col-ephemeral"><input type="checkbox" data-field="available" ' + (p.available ? "checked" : "") + "></td>" +
          '<td class="chips">' + positionChips(p, "preferred_positions") + "</td>" +
          '<td class="chips">' + positionChips(p, "secondary_positions") + "</td>" +
          '<td class="chips">' + positionChips(p, "unwilling_positions") + "</td>" +
          '<td class="col-ephemeral"><select data-field="optional_position_override">' + selectOptions(overrideOptions, p.optional_position_override) + "</select></td>" +
          '<td class="col-ephemeral"><select data-field="optional_player_link">' + selectOptions(linkOptions, p.optional_player_link) + "</select></td>" +
          '<td data-col="remove">' + (locked ? "" : '<button type="button" data-action="delete" title="Remove">&times;</button>') + "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  function statusClass(status) {
    if (status === "primary") return "badge status-primary";
    if (status === "secondary") return "badge status-secondary";
    if (status === "unwilling") return "badge status-unwilling";
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
    function lineCounts(item) {
      let text = "primary " + item.primary_count + " · secondary " + item.secondary_count + " · oop " + item.oop_count;
      if (item.unwilling_count > 0) text += ' · <span class="unwilling-flag">unwilling ' + item.unwilling_count + "</span>";
      return text;
    }

    let html = '<div class="lines-grid">';
    for (const fl of lastResult.forward_lines) {
      html +=
        '<div class="line-card"><h3>Forward ' + fl.line_number + " <span class=\"exp\">exp " + fl.exp_sum + "</span></h3>" +
        fl.slots.map(renderSlot).join("") +
        '<div class="line-counts">' + lineCounts(fl) + "</div>" +
        "</div>";
    }
    for (const dp of lastResult.defense_pairs) {
      html +=
        '<div class="line-card"><h3>Defense ' + dp.pair_number + (dp.partial ? ' <span class="exp">partial</span>' : "") + "</h3>" +
        dp.slots.map(renderSlot).join("") +
        '<div class="line-counts">' + lineCounts(dp) + "</div>" +
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
      (s.total_unwilling > 0
        ? '<div class="stat-unwilling"><span class="stat-label">Unwilling</span><span class="stat-val">' + s.total_unwilling + "</span></div>"
        : "") +
      "</div>";
  }

  function renderBanner() {
    if (mode === "roster" || !lastResult) {
      bannerEl.hidden = true;
      return;
    }
    bannerEl.hidden = false;
    if (lastResult.status === "NO_SOLUTION") {
      bannerEl.className = "status-banner infeasible";
      bannerEl.textContent = "No feasible solution for the current roster/constraints. Adjust and retry, or hit Reset.";
      return;
    }
    const s = lastResult.summary;
    const compromises = [];
    if (s.total_secondary > 0) compromises.push(s.total_secondary + " secondary");
    if (s.total_oop > 0) compromises.push(s.total_oop + " out-of-position");
    if (compromises.length) {
      // A solved status here (OPTIMAL/FEASIBLE) only means the solver met the
      // hard constraints - it says nothing about whether players actually
      // got their preferred position. Surface that distinctly so a technically-
      // solved but heavily-compromised roster doesn't read as an all-clear.
      const total = s.total_secondary + s.total_oop;
      bannerEl.className = "status-banner suboptimal";
      bannerEl.textContent =
        "Status: SUBOPTIMAL — " +
        compromises.join(", ") +
        " assignment" + (total > 1 ? "s" : "") +
        ". Feasible, but the roster is too constrained for everyone to play their preferred position.";
    } else {
      bannerEl.className = "status-banner ok";
      bannerEl.textContent = "Status: " + lastResult.status;
    }
  }

  function renderStaleness() {
    // Grid/summary are wholesale-replaced by renderGrid(), so this lives as
    // a sibling badge + a class on the panel rather than inside either of them.
    const stale = mode === "scenario" && resultPending;
    resultsPanelEl.classList.toggle("stale", stale);
    pendingIndicatorEl.hidden = !stale;
    // dof has its own, longer-running pending window (it keeps computing
    // after the quick main solve above has already resolved), so it dims
    // independently rather than piggybacking on `stale` above.
    const dofStale = mode === "scenario" && dofPending;
    dofPanelEl.classList.toggle("dof-stale", dofStale);
    dofPendingIndicatorEl.hidden = !dofStale;
  }

  function hideDof() {
    cancelDof();
    dofPending = false;
    lastDofResult = null;
    dofPanelEl.hidden = true;
    renderStaleness();
  }

  function render() {
    renderRoster();
    renderGrid();
    renderBanner();
    updateDirtyState();
    updateSaveButtonState();
    renderStaleness();
    updateSortIndicators();
  }

  function scheduleSolve() {
    if (mode !== "scenario" || !autoToggle.checked) return;
    // The result becomes stale the instant an edit happens, not just once
    // doSolve() actually starts - marking it pending only there left a ~400ms
    // debounce window (plus solve time) where the save button stayed
    // wrongly enabled, then flashed disabled for the brief tail end.
    resultPending = true;
    updateSaveButtonState();
    renderStaleness();
    cancelDof();
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(doSolve, 400);
  }

  async function doSolve() {
    resultPending = true;
    updateSaveButtonState();
    renderStaleness();
    cancelDof();
    const settings = currentSettings();
    const resp = await fetch(STUDIO_BASE + "/solve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ players: players, forwards: settings.forwards, defense: settings.defense, time_limit: settings.time_limit }),
    });
    lastResult = resp.ok ? await resp.json() : { status: "NO_SOLUTION" };
    resultPending = false;
    updateSaveButtonState();
    renderStaleness();
    renderGrid();
    renderBanner();
    if (lastResult.status === "NO_SOLUTION") {
      dofPanelEl.hidden = true;
    } else {
      // Fire-and-forget: the main solve above already landed and rendered,
      // so this expensive follow-up (many re-solves) must not block or slow
      // down the instant-feeling path. It gets superseded/cancelled by
      // cancelDof() the moment another solve is scheduled.
      runDof();
    }
  }

  function cancelDof() {
    if (dofAbortController) dofAbortController.abort();
    dofGeneration++;
    dofPending = true;
    renderStaleness();
  }

  async function runDof() {
    cancelDof();
    const myGeneration = dofGeneration;
    const controller = new AbortController();
    dofAbortController = controller;

    // Deliberately not touching dofScoreEl/dofBreakdownBodyEl here: the
    // last-known numbers stay on screen (just dimmed, via dofPending above)
    // while this computes, rather than being wiped to a loading state.
    dofPanelEl.hidden = false;

    const settings = currentSettings();
    let resp;
    try {
      resp = await fetch(STUDIO_BASE + "/degrees-of-freedom", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ players: players, forwards: settings.forwards, defense: settings.defense, time_limit: settings.time_limit }),
        signal: controller.signal,
      });
    } catch (err) {
      return; // aborted (superseded) or network error - next solve retriggers this
    }
    if (myGeneration !== dofGeneration) return; // superseded while the request was in flight
    if (!resp.ok) {
      dofPending = false;
      dofPanelEl.hidden = true;
      renderStaleness();
      return;
    }
    const data = await resp.json();
    if (myGeneration !== dofGeneration) return;
    renderDof(data);
  }

  function renderDof(data) {
    dofPending = false;
    if (data.status === "NO_SOLUTION") {
      lastDofResult = null;
      dofPanelEl.hidden = true;
      renderStaleness();
      return;
    }
    lastDofResult = data;
    dofPanelEl.hidden = false;
    dofScoreEl.textContent = data.score_per_slot.toFixed(2) + " / slot";
    dofBreakdownBodyEl.innerHTML = data.by_position
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
    renderStaleness();
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
    } else if (POSITION_FIELDS.includes(field)) {
      const pos = e.target.dataset.pos;
      const list = player[field];
      const at = list.indexOf(pos);
      if (e.target.checked) {
        if (at === -1) list.push(pos);
        // A position can only be claimed by one column at a time - clear it
        // from the other two so this acts like a radio group per position,
        // except "none checked" (implicitly OOP) is still a valid state.
        let clearedElsewhere = false;
        for (const otherField of POSITION_FIELDS) {
          if (otherField === field) continue;
          const otherList = player[otherField];
          const otherAt = otherList.indexOf(pos);
          if (otherAt !== -1) {
            otherList.splice(otherAt, 1);
            clearedElsewhere = true;
          }
        }
        if (clearedElsewhere) renderRoster();
      } else if (at !== -1) {
        list.splice(at, 1);
      }
    }
    updateDirtyState();
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
    players.unshift({
      id: nextId("P"),
      name: nextPlayerName(),
      available: 1,
      experience: 1,
      preferred_positions: [],
      secondary_positions: [],
      unwilling_positions: [],
      optional_position_override: null,
      optional_player_link: null,
    });
    render();
    scheduleSolve();
  });

  document.getElementById("add-alt-btn").addEventListener("click", () => {
    players.unshift({
      id: nextId("A"),
      name: nextAltName(),
      available: 1,
      experience: 3,
      preferred_positions: POSITIONS.slice(),
      secondary_positions: [],
      unwilling_positions: [],
      optional_position_override: null,
      optional_player_link: null,
    });
    render();
    scheduleSolve();
  });

  function doReset() {
    if (mode === "scenario") {
      loadedScenario = null;
      scenarioOrigin = JSON.parse(JSON.stringify(rosterBaseline));
      scenarioOriginMeta = null;
      updatePanelHeading();
      updateSaveButtonLabels();
    }
    players = JSON.parse(JSON.stringify(rosterBaseline));
    if (mode === "roster") {
      titleInput.value = rosterTitleBaseline;
      autosizeTitleInput();
      lastResult = null;
      hideDof();
      render();
    } else {
      render();
      doSolve(); // always re-solve after a reset, regardless of the auto-solve toggle
    }
  }

  resetBtn.addEventListener("click", doReset);
  solveNowBtn.addEventListener("click", doSolve);

  autoToggle.checked = loadStoredAutoSolve();

  autoToggle.addEventListener("change", () => {
    storeAutoSolve(autoToggle.checked);
    if (autoToggle.checked) scheduleSolve();
  });

  // --- Roster mode saves ---------------------------------------------

  async function saveRoster() {
    if (isDirty() && !confirm("Save will replace the saved roster with the version shown here. Continue?")) {
      return false;
    }
    const resp = await fetch(STUDIO_BASE + "/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ players: players, title: titleInput.value }),
    });
    if (!resp.ok) {
      alert("Save failed.");
      return false;
    }
    rosterBaseline = JSON.parse(JSON.stringify(players));
    rosterTitleBaseline = titleInput.value;
    if (!loadedScenario) scenarioOrigin = JSON.parse(JSON.stringify(rosterBaseline));
    updateDirtyState();
    return true;
  }

  async function saveRosterAs() {
    const title = prompt("Title for the new roster:", titleInput.value + " (copy)");
    if (!title) return;
    const resp = await fetch(STUDIO_BASE + "/save-as", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: title, players: players }),
    });
    if (resp.ok) {
      const data = await resp.json();
      window.location = "/w/" + window.WORKSPACE_TOKEN + "/studio/" + data.roster_id;
    } else {
      alert("Save As failed.");
    }
  }

  // --- Scenario mode saves ---------------------------------------------

  async function overwriteLoadedScenario() {
    if (isDirty() && !confirm('Save will replace scenario "' + loadedScenario.title + '" with the version shown here. Continue?')) {
      return false;
    }
    const settings = currentSettings();
    const resp = await fetch(STUDIO_BASE + "/scenarios/" + loadedScenario.id + "/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        players: players,
        forwards: settings.forwards,
        defense: settings.defense,
        time_limit: settings.time_limit,
        result: lastResult,
        dof: dofPending ? null : lastDofResult,
      }),
    });
    if (!resp.ok) {
      alert("Save scenario failed.");
      return false;
    }
    scenarioOrigin = JSON.parse(JSON.stringify(players));
    scenarioOriginMeta = {
      forwards: settings.forwards,
      defense: settings.defense,
      time_limit: settings.time_limit,
      result: lastResult,
      dof: dofPending ? null : lastDofResult,
    };
    updateDirtyState();
    return true;
  }

  function openScenarioDialog(isBranch) {
    scenarioDialogTitle.textContent = isBranch ? "Branch scenario" : "Save as scenario";
    scenarioSubmitBtn.textContent = isBranch ? "Branch scenario" : "Save scenario";
    scenarioTitleInput.value = nextScenarioTitle();
    scenarioDescriptionInput.value = "";
    scenarioDialog.showModal();
    scenarioTitleInput.focus();
    scenarioTitleInput.select();
  }

  document.getElementById("scenario-cancel-btn").addEventListener("click", () => {
    pendingModeSwitch = null;
    scenarioDialog.close();
  });

  scenarioForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = scenarioTitleInput.value.trim();
    if (!title) return;
    const settings = currentSettings();
    const resp = await fetch(STUDIO_BASE + "/scenarios", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: title,
        description: scenarioDescriptionInput.value.trim(),
        players: players,
        forwards: settings.forwards,
        defense: settings.defense,
        time_limit: settings.time_limit,
        result: lastResult,
        dof: dofPending ? null : lastDofResult,
        parent_scenario_id: loadedScenario ? loadedScenario.id : null,
      }),
    });
    if (resp.ok) {
      const data = await resp.json();
      scenarioTitles.push(title);
      loadedScenario = { id: data.scenario_id, title: title };
      scenarioOrigin = JSON.parse(JSON.stringify(players));
      scenarioOriginMeta = {
        forwards: settings.forwards,
        defense: settings.defense,
        time_limit: settings.time_limit,
        result: lastResult,
        dof: dofPending ? null : lastDofResult,
      };
      updatePanelHeading();
      updateSaveButtonLabels();
      updateDirtyState();
      scenarioDialog.close();
      if (pendingModeSwitch) {
        const target = pendingModeSwitch;
        pendingModeSwitch = null;
        enterMode(target);
      }
    } else {
      alert("Save scenario failed.");
    }
  });

  // --- Primary/alt Save button, context-sensitive by mode --------------

  saveBtn.addEventListener("click", () => {
    if (saveBtn.disabled) return;
    if (mode === "roster") {
      saveRoster();
    } else if (loadedScenario) {
      overwriteLoadedScenario();
    } else {
      openScenarioDialog(false);
    }
  });

  function closeSaveMenu() {
    saveMenu.classList.remove("open");
    saveMenuToggle.setAttribute("aria-expanded", "false");
  }

  saveMenuToggle.addEventListener("click", (e) => {
    e.stopPropagation();
    if (saveMenu.classList.contains("open")) closeSaveMenu();
    else {
      saveMenu.classList.add("open");
      saveMenuToggle.setAttribute("aria-expanded", "true");
    }
  });

  document.addEventListener("click", (e) => {
    if (!saveMenu.contains(e.target) && e.target !== saveMenuToggle) closeSaveMenu();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeSaveMenu();
  });

  saveAltBtn.addEventListener("click", () => {
    closeSaveMenu();
    if (saveAltBtn.disabled) return;
    if (mode === "roster") {
      saveRosterAs();
    } else {
      openScenarioDialog(true);
    }
  });

  // --- Mode switching ----------------------------------------------------

  function applyModeUI() {
    root.classList.toggle("mode-roster", mode === "roster");
    root.classList.toggle("mode-scenario", mode === "scenario");
    for (const btn of modeToggle.querySelectorAll(".mode-toggle-btn")) {
      btn.setAttribute("aria-selected", btn.dataset.mode === mode ? "true" : "false");
    }
    titleInput.readOnly = mode === "scenario";
    updatePanelHeading();
    updateSaveButtonLabels();
  }

  function enterMode(newMode) {
    mode = newMode;
    players = JSON.parse(JSON.stringify(currentOrigin()));
    applyModeUI();
    if (mode === "roster") {
      lastResult = null;
      hideDof();
      render();
    } else if (scenarioOriginMeta) {
      // A scenario is loaded and its cached result still matches
      // scenarioOrigin (nothing's touched it since) - reuse it as-is.
      document.getElementById("setting-forwards").value = scenarioOriginMeta.forwards;
      document.getElementById("setting-defense").value = scenarioOriginMeta.defense;
      document.getElementById("setting-time-limit").value = scenarioOriginMeta.time_limit;
      lastResult = scenarioOriginMeta.result;
      resultPending = false;
      render();
      if (scenarioOriginMeta.dof) {
        // Already have a cached analysis for this exact snapshot - show it
        // instantly rather than paying for a fresh (expensive) recomputation.
        renderDof(scenarioOriginMeta.dof);
      } else if (lastResult.status !== "NO_SOLUTION") {
        runDof();
      }
    } else {
      render();
      doSolve();
    }
    storeMode(mode);
  }

  function requestModeSwitch(target) {
    if (target === mode) return;
    if (!isDirty()) {
      enterMode(target);
      return;
    }
    pendingModeSwitch = target;
    unsavedDialogBody.textContent =
      mode === "roster"
        ? "You have unsaved roster changes. Save them, discard them, or stay here?"
        : "You have unsaved scenario changes. Save them, discard them, or stay here?";
    unsavedDialog.showModal();
  }

  modeToggle.addEventListener("click", (e) => {
    const btn = e.target.closest(".mode-toggle-btn");
    if (!btn) return;
    requestModeSwitch(btn.dataset.mode);
  });

  document.getElementById("unsaved-cancel-btn").addEventListener("click", () => {
    pendingModeSwitch = null;
    unsavedDialog.close();
  });

  document.getElementById("unsaved-discard-btn").addEventListener("click", () => {
    const target = pendingModeSwitch;
    pendingModeSwitch = null;
    unsavedDialog.close();
    enterMode(target);
  });

  document.getElementById("unsaved-save-btn").addEventListener("click", async () => {
    unsavedDialog.close();
    if (mode === "scenario" && !loadedScenario) {
      // Nothing loaded to silently overwrite - fall back to the full
      // Save-as-scenario dialog. pendingModeSwitch stays set so the
      // originally requested switch still happens once that's submitted.
      openScenarioDialog(false);
      return;
    }
    const target = pendingModeSwitch;
    pendingModeSwitch = null;
    const ok = mode === "roster" ? await saveRoster() : await overwriteLoadedScenario();
    if (ok) enterMode(target);
  });

  // --- Initial paint ------------------------------------------------------

  enterMode(mode);
})();

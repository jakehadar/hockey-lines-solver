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

  // Mirrors schemas.DEFAULT_OBJECTIVES - the historical fixed priority order
  // (assigned > preference > balance, all enabled). Server-side is the
  // source of truth for what a solve actually used (see lastResult.objectives
  // / OBJECTIVE_SHORT_LABELS below); this is only the client's starting point.
  const DEFAULT_OBJECTIVES = [
    { key: "assigned", enabled: true },
    { key: "preference", enabled: true },
    { key: "balance", enabled: true },
  ];
  const OBJECTIVE_LABELS = {
    assigned: "Maximize players assigned",
    preference: "Maximize position preferences",
    balance: "Balance experience across lines",
  };
  const OBJECTIVE_SHORT_LABELS = { assigned: "Assigned", preference: "Preference", balance: "Balance" };

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
  const OBJECTIVES_OPEN_STORAGE_KEY = "studio.objectivesOpen." + ROSTER_ID;

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

  function loadStoredObjectivesOpen() {
    try {
      return localStorage.getItem(OBJECTIVES_OPEN_STORAGE_KEY) === "1";
    } catch (e) {
      return false;
    }
  }

  function storeObjectivesOpen(isOpen) {
    try {
      localStorage.setItem(OBJECTIVES_OPEN_STORAGE_KEY, isOpen ? "1" : "0");
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
  let loadedScenario = serverLoadedScenario
    ? { id: serverLoadedScenario.id, title: serverLoadedScenario.title, parent_scenario_id: serverLoadedScenario.parent_scenario_id }
    : null;
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
        allow_oop: serverLoadedScenario.allow_oop,
        allow_unwilling: serverLoadedScenario.allow_unwilling,
        objectives: serverLoadedScenario.objectives,
        result: serverLoadedScenario.result,
        dof: serverLoadedScenario.dof,
      }
    : null;

  // The live "Objectives" panel state - order is priority (index 0 =
  // highest). Restored from a loaded scenario's snapshot if present,
  // otherwise the historical default. Kept separate from currentSettings()'s
  // other fields only because it's an array that needs its own render pass.
  let objectiveOrder = (serverLoadedScenario && serverLoadedScenario.objectives
    ? serverLoadedScenario.objectives
    : DEFAULT_OBJECTIVES
  ).map((o) => ({ key: o.key, enabled: o.enabled }));

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
  // {id, title} for every scenario on this roster - drives both the
  // "Scenario N" uniqueness check below and the heading dropdown's
  // "jump to a different scenario" list.
  let scenarioList = (window.SCENARIO_LIST || []).slice();
  let scenarioTitles = scenarioList.map((s) => s.title);
  // { type: "mode", target } for the Roster/Scenario toggle, or
  // { type: "navigate", url } for a plain link (Scenarios, Rosters) - either
  // way, this is what the unsaved-changes dialog carries out once the user
  // picks Discard or Save.
  let pendingAction = null;

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
  // The job id of whichever dof request is currently in flight server-side,
  // if any - lets cancelDof() ask the server to actually stop it (not just
  // abort the client's own wait on it). See dof.py's job registry.
  let currentDofJobId = null;
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
  const saveAsBtn = document.getElementById("save-as-btn");
  const scenarioDialog = document.getElementById("scenario-dialog");
  const scenarioDialogTitle = document.getElementById("scenario-dialog-title");
  const scenarioDialogSubtitle = document.getElementById("scenario-dialog-subtitle");
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
    saveAsBtn.disabled = disabled;
    saveMenuToggle.disabled = disabled;
    // Also covers "Solve": a solve already in flight (or about to be,
    // once the debounce timer fires) shouldn't be kickable again on top of
    // itself.
    solveNowBtn.disabled = disabled;
  }

  function updateSaveButtonLabels() {
    // "Save"/"Save as…" in both modes - which mode is active is already
    // shown by the mode toggle right next to these buttons, so repeating
    // "roster"/"scenario" in the label itself is redundant.
    saveBtn.textContent = "Save";
    if (mode === "roster") {
      saveAltBtn.textContent = "Save as…";
      saveAsBtn.hidden = true;
    } else {
      // "Branch…" makes a new scenario as a child of the currently loaded
      // one (or a fresh root if nothing's loaded); "Save as…" makes a new
      // scenario as a *sibling* of it (same parent) - two different
      // lineage operations, so they get two distinct menu items rather
      // than folding "Save as…" into Branch.
      saveAltBtn.textContent = "Branch…";
      saveAsBtn.hidden = false;
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

  // Reset means "discard my in-progress edits," not "abandon what's loaded" -
  // it reverts to scenarioOrigin (the loaded scenario's last-saved snapshot,
  // or the roster baseline if nothing's loaded) without unloading a loaded
  // scenario. That's easy to misread as "go back to the roster" given the
  // tree of branches a scenario can sit in, so the tooltip spells out
  // exactly where it lands - kept in sync wherever updatePanelHeading() is,
  // since both depend on the same mode/loadedScenario state.
  function updateResetTooltip() {
    if (mode === "roster") {
      resetBtn.title = "Discard in-progress edits and revert to the last-saved roster.";
    } else if (loadedScenario) {
      resetBtn.title = 'Discard in-progress edits and revert to scenario "' + loadedScenario.title + '" as last saved (stays loaded).';
    } else {
      resetBtn.title = "Discard in-progress edits and revert to the current roster.";
    }
  }

  // --- Scenario heading dropdown ------------------------------------------
  //
  // The "Roster"/"Scenario: X" heading doubles as a navigation menu in
  // Scenario mode: unload back to the roster baseline, or jump to a
  // different scenario on this roster. Deliberately not a real button (see
  // .panel-heading-group's CSS) - it's a title first, so it only grows a
  // small caret rather than button chrome, and only when there's actually
  // somewhere to go.

  const panelHeadingGroup = document.getElementById("panel-heading-group");
  const scenarioHeadingCaret = document.getElementById("scenario-heading-caret");
  const scenarioHeadingMenu = document.getElementById("scenario-heading-menu");

  function closeScenarioHeadingMenu() {
    scenarioHeadingMenu.classList.remove("open");
    scenarioHeadingCaret.setAttribute("aria-expanded", "false");
  }

  function renderScenarioHeadingMenu() {
    const items = [];
    if (loadedScenario) {
      items.push('<button type="button" class="heading-menu-item" data-action="unload">Unload (start fresh from roster)</button>');
    }
    const others = scenarioList.filter((s) => !loadedScenario || s.id !== loadedScenario.id);
    if (others.length) {
      if (items.length) items.push('<div class="heading-menu-divider"></div>');
      for (const s of others) {
        items.push('<button type="button" class="heading-menu-item" data-action="load" data-id="' + s.id + '">' + s.title + "</button>");
      }
    }
    if (!items.length) {
      items.push('<div class="heading-menu-empty">No other scenarios yet.</div>');
    }
    scenarioHeadingMenu.innerHTML = items.join("");
  }

  // Whether there's anything useful to show - kept in sync everywhere
  // updatePanelHeading() runs, since both depend on the same mode/
  // loadedScenario/scenarioList state.
  function updateScenarioHeadingMenuAvailability() {
    const available = mode === "scenario" && (loadedScenario || scenarioList.length > 0);
    scenarioHeadingCaret.hidden = !available;
    panelHeadingGroup.classList.toggle("has-menu", available);
    if (!available) closeScenarioHeadingMenu();
  }

  panelHeadingGroup.addEventListener("click", (e) => {
    if (!panelHeadingGroup.classList.contains("has-menu")) return;
    e.stopPropagation();
    if (scenarioHeadingMenu.classList.contains("open")) {
      closeScenarioHeadingMenu();
      return;
    }
    renderScenarioHeadingMenu();
    scenarioHeadingMenu.classList.add("open");
    scenarioHeadingCaret.setAttribute("aria-expanded", "true");
  });

  scenarioHeadingMenu.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    closeScenarioHeadingMenu();
    if (btn.dataset.action === "unload") {
      requestNavigate(STUDIO_BASE);
    } else if (btn.dataset.action === "load") {
      requestNavigate(STUDIO_BASE + "?load_scenario=" + btn.dataset.id);
    }
  });

  document.addEventListener("click", (e) => {
    if (!panelHeadingGroup.contains(e.target)) closeScenarioHeadingMenu();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeScenarioHeadingMenu();
  });

  function currentSettings() {
    return {
      forwards: parseInt(document.getElementById("setting-forwards").value, 10) || 0,
      defense: parseInt(document.getElementById("setting-defense").value, 10) || 0,
      time_limit: parseInt(document.getElementById("setting-time-limit").value, 10) || 5,
      allow_oop: document.getElementById("setting-allow-oop").checked,
      allow_unwilling: document.getElementById("setting-allow-unwilling").checked,
      objectives: objectiveOrder.map((o) => ({ key: o.key, enabled: o.enabled })),
    };
  }

  // --- Objectives panel -------------------------------------------------

  const objectivesListEl = document.getElementById("objectives-list");
  const objectivesUsedEl = document.getElementById("objectives-used");
  const objectivesDetailsEl = document.getElementById("objectives-details");

  // Open/closed state is a per-browser, per-roster preference (same
  // localStorage pattern as mode/auto-solve above), not roster data - so it
  // persists across visits without needing a server round-trip.
  objectivesDetailsEl.open = loadStoredObjectivesOpen();
  objectivesDetailsEl.addEventListener("toggle", () => {
    storeObjectivesOpen(objectivesDetailsEl.open);
  });

  function renderObjectives() {
    const enabledCount = objectiveOrder.filter((o) => o.enabled).length;
    objectivesListEl.innerHTML = objectiveOrder
      .map((o, idx) => {
        const lastEnabled = o.enabled && enabledCount === 1;
        return (
          '<li data-key="' + o.key + '"' + (o.enabled ? "" : ' class="objective-disabled"') + '>' +
          '<label><input type="checkbox" data-action="toggle"' +
          (o.enabled ? " checked" : "") +
          (lastEnabled ? " disabled title=\"At least one objective must stay on\"" : "") +
          "> " + OBJECTIVE_LABELS[o.key] + "</label>" +
          '<span class="objective-reorder">' +
          '<button type="button" data-action="up"' + (idx === 0 ? " disabled" : "") + ' aria-label="Move up">&uarr;</button>' +
          '<button type="button" data-action="down"' + (idx === objectiveOrder.length - 1 ? " disabled" : "") + ' aria-label="Move down">&darr;</button>' +
          "</span></li>"
        );
      })
      .join("");
  }

  objectivesListEl.addEventListener("change", (e) => {
    if (e.target.dataset.action !== "toggle") return;
    const li = e.target.closest("li[data-key]");
    const entry = objectiveOrder.find((o) => o.key === li.dataset.key);
    entry.enabled = e.target.checked;
    renderObjectives();
    scheduleSolve();
  });

  objectivesListEl.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const li = btn.closest("li[data-key]");
    const idx = objectiveOrder.findIndex((o) => o.key === li.dataset.key);
    const swapIdx = idx + (btn.dataset.action === "up" ? -1 : 1);
    if (swapIdx < 0 || swapIdx >= objectiveOrder.length) return;
    [objectiveOrder[idx], objectiveOrder[swapIdx]] = [objectiveOrder[swapIdx], objectiveOrder[idx]];
    renderObjectives();
    scheduleSolve();
  });

  renderObjectives();

  // Sourced from lastResult.objectives (what the server actually solved
  // with), not the live objectiveOrder panel state - those can diverge, e.g.
  // right after loading a scenario whose snapshot used a different order
  // than whatever the panel currently shows.
  function renderObjectivesUsed() {
    const objectives = lastResult && lastResult.objectives;
    if (mode !== "scenario" || !objectives || lastResult.status === "NO_SOLUTION") {
      objectivesUsedEl.textContent = "";
      return;
    }
    const active = objectives.filter((o) => o.enabled).map((o) => OBJECTIVE_SHORT_LABELS[o.key]);
    const off = objectives.filter((o) => !o.enabled).map((o) => OBJECTIVE_SHORT_LABELS[o.key]);
    let text = "Priority: " + active.join(" > ");
    if (off.length) text += "  ·  off: " + off.join(", ");
    objectivesUsedEl.textContent = text;
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
    renderObjectivesUsed();
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
      body: JSON.stringify({
        players: players,
        forwards: settings.forwards,
        defense: settings.defense,
        time_limit: settings.time_limit,
        allow_oop: settings.allow_oop,
        allow_unwilling: settings.allow_unwilling,
        objectives: settings.objectives,
      }),
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
    if (currentDofJobId) {
      // Best-effort: tells the server to stop launching further re-solves
      // for the job we're abandoning. Fire-and-forget - if this never
      // arrives, the old computation just runs to completion server-side
      // and its result gets discarded by the generation check below anyway.
      const jobIdToCancel = currentDofJobId;
      currentDofJobId = null;
      fetch(STUDIO_BASE + "/degrees-of-freedom/cancel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jobIdToCancel }),
        keepalive: true,
      }).catch(() => {});
    }
    dofGeneration++;
    dofPending = true;
    renderStaleness();
  }

  async function runDof() {
    cancelDof();
    const myGeneration = dofGeneration;
    const controller = new AbortController();
    dofAbortController = controller;
    const jobId = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(myGeneration) + "-" + Date.now();
    currentDofJobId = jobId;

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
        body: JSON.stringify({
          players: players,
          forwards: settings.forwards,
          defense: settings.defense,
          time_limit: settings.time_limit,
          allow_oop: settings.allow_oop,
          allow_unwilling: settings.allow_unwilling,
          objectives: settings.objectives,
          job_id: jobId,
        }),
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

  for (const id of ["setting-forwards", "setting-defense", "setting-time-limit", "setting-allow-oop", "setting-allow-unwilling"]) {
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
    if (mode === "roster") {
      players = JSON.parse(JSON.stringify(rosterBaseline));
      titleInput.value = rosterTitleBaseline;
      autosizeTitleInput();
      lastResult = null;
      hideDof();
      render();
      return;
    }
    // Scenario mode: revert to scenarioOrigin (the loaded scenario's
    // last-saved snapshot, or the roster baseline if nothing's loaded) -
    // discards in-progress edits without unloading whatever's loaded. Same
    // "undo my edits" meaning Reset has everywhere else in the app, rather
    // than "abandon my place in the scenario tree."
    players = JSON.parse(JSON.stringify(scenarioOrigin));
    cancelDof(); // in case an edit mid-discard had a solve/dof already in flight
    loadScenarioCleanState();
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
        allow_oop: settings.allow_oop,
        allow_unwilling: settings.allow_unwilling,
        objectives: settings.objectives,
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
      allow_oop: settings.allow_oop,
      allow_unwilling: settings.allow_unwilling,
      objectives: settings.objectives,
      result: lastResult,
      dof: dofPending ? null : lastDofResult,
    };
    updateDirtyState();
    return true;
  }

  // Three distinct ways to create a *new* scenario row, distinguished by
  // what parent_scenario_id they end up with:
  //   "save"     - the plain first save when nothing's loaded yet. No
  //                lineage to inherit, so parent is always null.
  //   "branch"   - a child of the currently loaded scenario (or a fresh
  //                root if nothing's loaded).
  //   "save-as"  - a *sibling* of the currently loaded scenario: same
  //                parent as it has (or null, matching it, if it has none).
  let scenarioDialogKind = "save";

  function parentForDialogKind(kind) {
    if (kind === "branch") return loadedScenario ? loadedScenario.id : null;
    if (kind === "save-as") return loadedScenario ? loadedScenario.parent_scenario_id ?? null : null;
    return null;
  }

  function subtitleForDialogKind(kind) {
    if (kind === "branch") {
      return loadedScenario ? "Branching from “" + loadedScenario.title + "”." : "This will start a new branch.";
    }
    if (kind === "save-as") {
      return loadedScenario ? "Same parent as “" + loadedScenario.title + "”." : "This will start a new scenario.";
    }
    return "";
  }

  function openScenarioDialog(kind) {
    scenarioDialogKind = kind;
    const titles = { save: "Save as scenario", branch: "Branch scenario", "save-as": "Save as" };
    const submitLabels = { save: "Save", branch: "Branch", "save-as": "Save as" };
    scenarioDialogTitle.textContent = titles[kind];
    scenarioSubmitBtn.textContent = submitLabels[kind];
    scenarioDialogSubtitle.textContent = subtitleForDialogKind(kind);
    scenarioTitleInput.value = nextScenarioTitle();
    scenarioDescriptionInput.value = "";
    scenarioDialog.showModal();
    scenarioTitleInput.focus();
    scenarioTitleInput.select();
  }

  document.getElementById("scenario-cancel-btn").addEventListener("click", () => {
    pendingAction = null;
    scenarioDialog.close();
  });

  scenarioForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = scenarioTitleInput.value.trim();
    if (!title) return;
    const settings = currentSettings();
    const parentId = parentForDialogKind(scenarioDialogKind);
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
        allow_oop: settings.allow_oop,
        allow_unwilling: settings.allow_unwilling,
        objectives: settings.objectives,
        result: lastResult,
        dof: dofPending ? null : lastDofResult,
        parent_scenario_id: parentId,
      }),
    });
    if (resp.ok) {
      const data = await resp.json();
      scenarioTitles.push(title);
      scenarioList.push({ id: data.scenario_id, title: title });
      loadedScenario = { id: data.scenario_id, title: title, parent_scenario_id: parentId };
      scenarioOrigin = JSON.parse(JSON.stringify(players));
      scenarioOriginMeta = {
        forwards: settings.forwards,
        defense: settings.defense,
        time_limit: settings.time_limit,
        allow_oop: settings.allow_oop,
        allow_unwilling: settings.allow_unwilling,
        objectives: settings.objectives,
        result: lastResult,
        dof: dofPending ? null : lastDofResult,
      };
      updatePanelHeading();
      updateResetTooltip();
      updateScenarioHeadingMenuAvailability();
      updateSaveButtonLabels();
      updateDirtyState();
      scenarioDialog.close();
      applyPendingAction();
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
      openScenarioDialog("save");
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
      openScenarioDialog("branch");
    }
  });

  saveAsBtn.addEventListener("click", () => {
    closeSaveMenu();
    if (saveAsBtn.disabled) return;
    openScenarioDialog("save-as");
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
    updateResetTooltip();
    updateScenarioHeadingMenuAvailability();
    updateSaveButtonLabels();
  }

  // Populates the settings panel + result/dof for scenarioOrigin: either
  // restored from scenarioOriginMeta's cache (a loaded scenario whose
  // snapshot still matches what's on screen), or a fresh solve if nothing's
  // cached (nothing loaded, or a cache doReset() just intentionally
  // restored to). Shared by enterMode() (switching into Scenario mode) and
  // doReset() (reverting to the same clean state without switching modes).
  function loadScenarioCleanState() {
    if (scenarioOriginMeta) {
      document.getElementById("setting-forwards").value = scenarioOriginMeta.forwards;
      document.getElementById("setting-defense").value = scenarioOriginMeta.defense;
      document.getElementById("setting-time-limit").value = scenarioOriginMeta.time_limit;
      document.getElementById("setting-allow-oop").checked = scenarioOriginMeta.allow_oop !== false;
      // Opposite fallback direction from allow_oop above: allow_unwilling
      // defaults to false, so a missing/undefined cached value (an older
      // scenario, or one from before this setting existed) must read as
      // unchecked, not checked.
      document.getElementById("setting-allow-unwilling").checked = scenarioOriginMeta.allow_unwilling === true;
      // Same fallback direction as allow_oop: an older cached scenario saved
      // before this setting existed has no objectives field, so fall back to
      // the historical default order rather than leaving the panel empty.
      objectiveOrder = (scenarioOriginMeta.objectives || DEFAULT_OBJECTIVES).map((o) => ({ key: o.key, enabled: o.enabled }));
      renderObjectives();
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
  }

  function enterMode(newMode) {
    mode = newMode;
    players = JSON.parse(JSON.stringify(currentOrigin()));
    applyModeUI();
    if (mode === "roster") {
      lastResult = null;
      hideDof();
      render();
    } else {
      loadScenarioCleanState();
    }
    storeMode(mode);
  }

  // Carries out whatever's pending after the unsaved-changes dialog resolves
  // (Discard, or a successful Save) - a mode switch or a plain navigation,
  // whichever requested it. No-op if nothing's pending (e.g. a save
  // triggered directly from the scenario-dialog "Save" flow, not via this
  // confirmation path).
  function applyPendingAction() {
    const action = pendingAction;
    pendingAction = null;
    if (!action) return;
    if (action.type === "mode") enterMode(action.target);
    else if (action.type === "navigate") window.location = action.url;
  }

  function confirmUnsavedChanges(action) {
    pendingAction = action;
    unsavedDialogBody.textContent =
      mode === "roster"
        ? "You have unsaved roster changes. Save them, discard them, or stay here?"
        : "You have unsaved scenario changes. Save them, discard them, or stay here?";
    unsavedDialog.showModal();
  }

  function requestModeSwitch(target) {
    if (target === mode) return;
    if (!isDirty()) {
      enterMode(target);
      return;
    }
    confirmUnsavedChanges({ type: "mode", target });
  }

  // Same guard for plain navigation links (Scenarios, Rosters) - leaving the
  // editor entirely is just as capable of silently dropping in-progress
  // edits as switching modes is.
  function requestNavigate(url) {
    if (!isDirty()) {
      window.location = url;
      return;
    }
    confirmUnsavedChanges({ type: "navigate", url });
  }

  modeToggle.addEventListener("click", (e) => {
    const btn = e.target.closest(".mode-toggle-btn");
    if (!btn) return;
    requestModeSwitch(btn.dataset.mode);
  });

  for (const link of document.querySelectorAll(".back-link, .scenarios-link")) {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      requestNavigate(link.href);
    });
  }

  document.getElementById("unsaved-cancel-btn").addEventListener("click", () => {
    pendingAction = null;
    unsavedDialog.close();
  });

  document.getElementById("unsaved-discard-btn").addEventListener("click", () => {
    unsavedDialog.close();
    applyPendingAction();
  });

  document.getElementById("unsaved-save-btn").addEventListener("click", async () => {
    unsavedDialog.close();
    if (mode === "scenario" && !loadedScenario) {
      // Nothing loaded to silently overwrite - fall back to the full
      // Save-as-scenario dialog. pendingAction stays set so the originally
      // requested switch/navigation still happens once that's submitted.
      openScenarioDialog("save");
      return;
    }
    const ok = mode === "roster" ? await saveRoster() : await overwriteLoadedScenario();
    if (ok) applyPendingAction();
  });

  // --- Initial paint ------------------------------------------------------

  enterMode(mode);
})();

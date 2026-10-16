(() => {
  "use strict";

  const app = document.getElementById("app");
  const overlay = document.getElementById("overlay");
  const toastRegion = document.getElementById("toast-region");
  const STATUSES = ["IDEA", "SELECTED", "RESEARCHING", "RESEARCH_COMPLETE", "SCRIPTING", "SCRIPT_REVIEW", "APPROVED", "VOICE", "VISUALS", "EDITING", "QA", "THUMBNAIL", "UPLOAD_READY", "PUBLISHED", "ANALYZING"];
  const SCORE_FIELDS = [
    ["curiosity", "Curiosity", 25], ["audience", "Audience", 20], ["story", "Story", 20],
    ["visuals", "Visuals", 10], ["evergreen", "Evergreen", 10], ["monetization", "Monetization", 10], ["difficulty", "Difficulty", 5]
  ];
  const SCENE_SECTIONS = ["hook", "promise", "setup", "discovery", "escalation", "big reveal", "consequence", "next curiosity"];
  const state = { overview: null, settings: null, project: null, search: "", statusFilter: "all", pillarFilter: "all", pollTimer: null, pollingActive: false, manualSourceUrls: new Set() };

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const attr = esc;
  const safeHref = value => {
    const raw = String(value || "");
    if (/^https?:\/\//i.test(raw) || /^\/(?!\/)/.test(raw)) return raw;
    return "#";
  };
  const num = (value, decimals = 1) => Number.isFinite(Number(value)) ? Number(value).toFixed(decimals) : "—";
  const date = value => value ? new Date(value).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "—";
  const stamp = value => value ? new Date(value).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—";
  const path = () => window.location.pathname.replace(/\/+$/, "") || "/";
  const statusClass = status => `status-${String(status || "").toLowerCase().replaceAll("_", "-")}`;
  const humanStatus = status => String(status || "UNKNOWN").replaceAll("_", " ");
  const activeNav = current => document.querySelectorAll(".nav-link").forEach(a => a.classList.toggle("active", a.getAttribute("href") === current));

  async function request(url, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
    if (options.method === "POST") headers["X-Studio-Request"] = "1";
    let response;
    try {
      response = await fetch(url, { ...options, headers });
    } catch (error) {
      throw new Error(`Could not reach the studio server. ${error.message || ""}`.trim());
    }
    if (!response.ok) {
      let message = `Request failed (${response.status})`;
      try {
        const body = await response.json();
        if (body && body.error) message = body.error;
      } catch (_) { /* Plain HTTP error response. */ }
      throw new Error(message);
    }
    const type = response.headers.get("content-type") || "";
    return type.includes("application/json") ? response.json() : response;
  }

  function toast(message, error = false) {
    toastRegion.innerHTML = `<div class="toast${error ? " error" : ""}">${esc(message)}</div>`;
    window.clearTimeout(toast.timeout);
    toast.timeout = window.setTimeout(() => { toastRegion.innerHTML = ""; }, 4200);
  }

  function showError(target, error) {
    const node = typeof target === "string" ? document.querySelector(target) : target;
    if (node) {
      let box = node.querySelector(".error-box");
      if (!box) { box = document.createElement("div"); box.className = "error-box"; node.prepend(box); }
      box.textContent = error.message || String(error);
    } else toast(error.message || String(error), true);
  }

  function loading(label = "Loading studio records") {
    app.innerHTML = `<div class="eyebrow">${esc(label)}</div><div class="skeleton" style="height:48px;width:40%;margin:14px 0 25px"></div><div class="skeleton" style="height:230px"></div>`;
  }

  function modal(title, body, onMount) {
    overlay.innerHTML = `<div class="modal-backdrop" data-dismiss="true"><section class="modal" role="dialog" aria-modal="true" aria-label="${attr(title)}"><header class="modal-head"><h2>${esc(title)}</h2><button class="icon-button" type="button" data-close aria-label="Close">×</button></header><div class="modal-body">${body}</div></section></div>`;
    overlay.querySelector("[data-close]").addEventListener("click", closeModal);
    overlay.querySelector(".modal-backdrop").addEventListener("click", e => { if (e.target.dataset.dismiss) closeModal(); });
    if (onMount) onMount(overlay.querySelector(".modal"));
    const first = overlay.querySelector("input,textarea,select,button");
    if (first) first.focus();
  }

  function closeModal() { overlay.innerHTML = ""; }

  async function loadOverview() {
    loading("Checking the slate");
    try {
      state.overview = await request("/api/overview");
      renderRoute();
      if (hasRunningJobs()) pollJobs();
    } catch (error) {
      app.innerHTML = `<div class="eyebrow">Studio connection</div><h1 style="font:500 48px var(--font-display)">The slate is out of reach.</h1><div id="page-errors"></div><button class="btn btn-primary" id="retry">Retry connection</button>`;
      showError("#page-errors", error);
      document.getElementById("retry").onclick = loadOverview;
    }
  }

  function renderRoute() {
    const current = path();
    activeNav(current === "/project" || current.startsWith("/project/") ? "/" : current);
    if (current === "/settings") return renderSettings();
    if (current === "/analytics") return renderAnalytics();
    const match = current.match(/^\/project\/([^/]+)$/);
    if (match) return loadProject(decodeURIComponent(match[1]));
    return renderProjects();
  }

  function filteredProjects() {
    const projects = state.overview?.projects || [];
    const query = state.search.trim().toLowerCase();
    return projects.filter(project => {
      const matchesQuery = !query || [project.topic, project.pillar, project.series, project.next_idea].some(value => String(value || "").toLowerCase().includes(query));
      const matchesStatus = state.statusFilter === "all" || project.status === state.statusFilter;
      const matchesPillar = state.pillarFilter === "all" || project.pillar === state.pillarFilter;
      return matchesQuery && matchesStatus && matchesPillar;
    });
  }

  function renderProjects() {
    if (!state.overview) return loading();
    const projects = filteredProjects();
    const all = state.overview.projects || [];
    const pillars = state.overview.pillars || [...new Set(all.map(p => p.pillar).filter(Boolean))];
    const statuses = state.overview.statuses || [...new Set(all.map(p => p.status).filter(Boolean))];
    app.innerHTML = `
      <section class="page-heading">
        <div><div class="eyebrow">Production desk / ${all.length.toString().padStart(2, "0")} in the slate</div><h1>The world behind<br>the headlines.</h1><p>Faceless YouTube OS is a human-led documentary studio for creators investigating the world behind the headlines. This app NEVER uploads or publishes.</p></div>
        <div class="heading-actions"><button class="btn btn-primary" id="new-project"><span>＋</span> New investigation</button></div>
      </section>
      <div class="toolbar">
        <label class="search-wrap"><span class="search-glyph">⌕</span><input type="text" id="project-search" placeholder="Search topics, series, pillars…" value="${attr(state.search)}" aria-label="Search projects"></label>
        <select class="filter-select" id="status-filter" aria-label="Filter by status"><option value="all">All statuses</option>${statuses.map(s => `<option value="${attr(s)}" ${s === state.statusFilter ? "selected" : ""}>${esc(humanStatus(s))}</option>`).join("")}</select>
        <select class="filter-select" id="pillar-filter" aria-label="Filter by pillar"><option value="all">All pillars</option>${pillars.map(p => `<option value="${attr(p)}" ${p === state.pillarFilter ? "selected" : ""}>${esc(p)}</option>`).join("")}</select>
        <span class="count-note">${projects.length} ${projects.length === 1 ? "record" : "records"} shown</span>
      </div>
      ${all.length ? (projects.length ? `<div class="project-list">${projects.map((p, index) => projectRow(p, index)).join("")}</div>` : `<section class="empty-state"><div class="empty-art"><div class="empty-seal">FILTER</div></div><div class="empty-copy"><div class="eyebrow">No matching investigations</div><h2>Try a different lens.</h2><p>These filters do not match any production records. Clear them to return to the full slate.</p><button class="btn" id="clear-filters">Clear filters</button></div></section>`) : `<section class="empty-state"><div class="empty-art"><div class="empty-seal">FIELD<br>NOTE</div></div><div class="empty-copy"><div class="eyebrow">Your slate starts here</div><h2>No stories in production.</h2><p>Start with a question about the systems behind the headlines. Your projects, research and production history will live here.</p><button class="btn btn-primary" id="empty-create">＋ Start an investigation</button></div></section>`}
      <div class="section-label">Production principles</div><p class="footnote">Every stage is explicit. Scores need confirmation. Claims need verification. Final gates are human decisions. Faceless production never means unattended publishing.</p>`;
    document.getElementById("new-project").onclick = openCreateProject;
    const emptyCreate = document.getElementById("empty-create");
    if (emptyCreate) emptyCreate.onclick = openCreateProject;
    const clear = document.getElementById("clear-filters");
    if (clear) clear.onclick = () => { state.search = ""; state.statusFilter = "all"; state.pillarFilter = "all"; renderProjects(); };
    document.getElementById("project-search").addEventListener("input", e => { state.search = e.target.value; renderProjectsKeepFocus("project-search"); });
    document.getElementById("status-filter").onchange = e => { state.statusFilter = e.target.value; renderProjects(); };
    document.getElementById("pillar-filter").onchange = e => { state.pillarFilter = e.target.value; renderProjects(); };
  }

  function renderProjectsKeepFocus(id) {
    const input = document.getElementById(id); const cursor = input.selectionStart;
    renderProjects();
    const next = document.getElementById(id); next.focus(); next.setSelectionRange(cursor, cursor);
  }

  function projectRow(p, index) {
    return `<a class="project-row" href="/project/${encodeURIComponent(p.id)}" style="animation-delay:${Math.min(index * 35, 280)}ms"><span class="project-index">${String(index + 1).padStart(2, "0")}</span><span><span class="project-topic">${esc(p.topic)}</span><span class="project-meta"><span class="project-pillar">${esc(p.pillar || "Unassigned")}</span>${p.series ? `<span>${esc(p.series)}</span>` : ""}<span>Opened ${esc(date(p.created_at))}</span></span></span><span class="project-pillar-cell">${esc(p.next_idea ? `Next: ${p.next_idea}` : "")}</span><span class="project-status-cell"><span class="status ${statusClass(p.status)}">${esc(humanStatus(p.status))}</span></span><span class="score-cell"><b class="score-value">${p.score?.total != null ? esc(p.score.total) : "—"}</b><span class="score-caption">Story score</span></span><span class="row-arrow">↗</span></a>`;
  }

  function openCreateProject() {
    const pillars = state.overview?.pillars || [];
    modal("A new investigation", `<p class="footnote" style="margin-top:0">Keep the premise pointed. You can shape the rest of the production after it has a place on the slate.</p><form id="create-form" class="form-grid"><div class="form-field wide"><label for="topic">Working title / topic</label><input id="topic" name="topic" type="text" required maxlength="240" placeholder="What is happening beneath the headline?"></div><div class="form-field"><label for="pillar">Editorial pillar</label><select id="pillar" name="pillar" required>${pillars.length ? pillars.map(p => `<option value="${attr(p)}">${esc(p)}</option>`).join("") : `<option value="" disabled selected>No pillars configured</option>`}</select></div><div class="form-field"><label for="quality">Production quality</label><select id="quality" name="quality"><option>HD</option><option selected>4K</option></select></div><div class="form-field"><label for="series">Series (optional)</label><input id="series" name="series" type="text" maxlength="160" placeholder="Series name"></div><div class="form-field"><label for="next_idea">Next idea (optional)</label><input id="next_idea" name="next_idea" type="text" maxlength="240" placeholder="Related future investigation"></div><div class="form-field wide"><div id="create-errors"></div></div><div class="form-field wide"><div class="form-actions"><button class="btn" type="button" data-close>Cancel</button><button class="btn btn-primary" type="submit" ${pillars.length ? "" : "disabled"}>Create project</button></div></div></form>`, modalNode => {
      modalNode.querySelector("[data-close]").onclick = closeModal;
      modalNode.querySelector("#create-form").onsubmit = async e => {
        e.preventDefault();
        const form = new FormData(e.currentTarget);
        const body = Object.fromEntries(form.entries());
        body.series = body.series.trim() || undefined;
        body.next_idea = body.next_idea.trim() || undefined;
        const submit = e.currentTarget.querySelector('[type="submit"]'); submit.disabled = true; submit.textContent = "Creating…";
        try {
          const created = await request("/api/projects", { method: "POST", body: JSON.stringify(body) });
          closeModal(); toast("Project added to the slate.");
          if (created && created.id != null) navigate(`/project/${encodeURIComponent(created.id)}`);
          else { await refreshOverview(); navigate("/"); }
        } catch (error) { showError("#create-errors", error); submit.disabled = false; submit.textContent = "Create project"; }
      };
    });
  }

  function navigate(to) { history.pushState({}, "", to); renderRoute(); window.scrollTo(0, 0); }
  async function refreshOverview() { state.overview = await request("/api/overview"); }
  async function loadProject(id) {
    loading("Opening investigation");
    try { state.project = await request(`/api/projects/${encodeURIComponent(id)}`); renderProject(); if (hasRunningJobs()) pollJobs(); }
    catch (error) {
      app.innerHTML = `<div class="eyebrow">Investigation unavailable</div><h1 style="font:500 48px var(--font-display)">This record could not be opened.</h1><div id="page-errors"></div><button class="btn" id="back-projects">Back to projects</button>`;
      showError("#page-errors", error); document.getElementById("back-projects").onclick = () => navigate("/");
    }
  }

  const stages = [
    ["IDEA","Idea"],["SELECTED","Selected"],["RESEARCHING","Research"],["RESEARCH_COMPLETE","Research complete"],["SCRIPTING","Script"],["SCRIPT_REVIEW","Script review"],["APPROVED","Approved"],["VOICE","Narration"],["VISUALS","Footage"],["EDITING","Edit"],["QA","QA"],["THUMBNAIL","Packaging"],["UPLOAD_READY","Upload ready"],["PUBLISHED","Published"],["ANALYZING","Analyzing"]
  ];
  function workflow(status) {
    let current = STATUSES.indexOf(status);
    return `<div class="workflow" aria-label="Production workflow">${stages.map(([key, label], index) => {
      const position = STATUSES.indexOf(key);
      return `<div class="workflow-step ${position < current ? "done" : ""} ${position === current ? "current" : ""}" title="${esc(humanStatus(key))}">${esc(label)}</div>`;
    }).join("")}</div>`;
  }

  function renderProject() {
    const p = state.project;
    if (!p) return;
    const job = p.job || state.overview?.jobs?.[p.id];
    const currentStatus = p.status;
    app.innerHTML = `
      <div class="detail-head"><div><div class="eyebrow">Investigation ${esc(p.id)} / ${esc(date(p.created_at))}</div><h1>${esc(p.topic)}</h1><div class="detail-sub"><span>${esc(p.pillar)}</span>${p.series ? `<span>· ${esc(p.series)}</span>` : ""}<span>· ${esc(p.quality || "HD")}</span><span class="status ${statusClass(currentStatus)}">${esc(humanStatus(currentStatus))}</span></div></div><div class="detail-actions"><button class="btn btn-small" id="refresh-project">↻ Refresh</button><a class="btn btn-small" href="/">← All projects</a></div></div>
      <div class="next-action"><strong>Next action</strong><span>${esc(p.next_action || "Review the current production stage and choose the next human decision.")}</span></div>
      ${job?.running ? `<div class="next-action"><strong>Background job</strong><span>${esc(job.message || "Production step is running.")}</span></div>` : ""}
      ${job?.error ? `<div class="error-box">${esc(job.error)}</div>` : ""}
      ${workflow(currentStatus)}
      <div class="stage-grid"><div class="stage-main">
        ${renderScore(p)}
        ${renderResearch(p)}
        ${renderScript(p)}
        ${renderPackaging(p)}
        ${renderProjectSettings(p)}
        ${renderProduction(p)}
        ${renderAnalyticsPanel(p)}
        ${renderGates(p)}
      </div><aside class="stage-aside">
        ${renderFactCheck(p)}
        ${renderQA(p)}
        ${renderApprovals(p)}
        ${renderHistory(p)}
      </aside></div>`;
    document.getElementById("refresh-project").onclick = () => loadProject(p.id);
    wireProjectActions();
    if (job?.running) {
      app.querySelectorAll("input, textarea, select, button").forEach(control => {
        if (control.id !== "refresh-project") control.disabled = true;
      });
    }
  }

  function renderScore(p) {
    const score = p.score || {};
    const total = Number(score.total || 0);
    return `<section class="panel"><header class="panel-head"><h2>Story score</h2><button class="btn btn-small" data-action="suggest-score">Suggest a score</button></header><div class="panel-content">
      ${p.score_suggestion ? `<div class="next-action"><strong>Suggestion only</strong><span>${esc(JSON.stringify(p.score_suggestion))} — not applied or confirmed.</span></div>` : ""}
      <div class="score-layout"><div class="score-total"><span>Editorial potential / 95</span><b>${score.total != null ? esc(score.total) : "—"}</b><span>${score.confirmed ? "Human-confirmed" : "Awaiting confirmation"}</span></div><div><div class="score-bars">${SCORE_FIELDS.map(([key,label,max]) => `<div class="score-bar"><label><span>${esc(label)}</span><small>${esc(score[key] ?? 0)} / ${max}</small></label><div class="bar-track"><div class="bar-fill" style="width:${Math.max(0, Math.min(100, (Number(score[key] || 0) / max) * 100))}%"></div></div></div>`).join("")}</div>
      <div class="score-controls">${SCORE_FIELDS.map(([key,label,max]) => `<div class="form-field"><label for="score-${key}">${esc(label)} / ${max}</label><input id="score-${key}" type="number" min="0" max="${max}" step="1" value="${attr(score[key] ?? 0)}"></div>`).join("")}<div class="form-field wide"><label for="override-reason">Override reason (if overriding)</label><input id="override-reason" type="text" value="${attr(score.override_reason || "")}" placeholder="Briefly explain a material adjustment"></div></div>
      <div class="score-footer"><button class="btn btn-primary btn-small" data-action="save-score">${score.confirmed ? "Save score" : "Confirm score"}</button>${score.override_reason ? `<span class="footnote">Override: ${esc(score.override_reason)}</span>` : ""}</div>
      </div></div></section>`;
  }

  function renderResearch(p) {
    const questions = p.questions || []; const sources = p.sources || []; const claims = p.claims || [];
    return `<div class="section-label">01 / Evidence desk</div><section class="panel"><header class="panel-head"><h2>Research questions</h2></header><div class="panel-content">
      ${questions.length ? `<ol class="question-list">${questions.map(q => `<li>${esc(q)}</li>`).join("")}</ol>` : emptyInline("No research questions recorded yet.")}
      </div></section>
      <section class="panel"><header class="panel-head"><h2>Sources <span class="footnote">(${sources.length})</span></h2><button class="btn btn-small" data-action="add-source">＋ Add source</button></header><div class="panel-content">
      ${sources.length ? `<div class="list-stack">${sources.map((s,i) => `<article class="list-card"><div class="list-card-head"><div><h4>${esc(s.title || s.url || `Source ${i + 1}`)}</h4>${s.url ? `<a class="linkish" href="${attr(safeHref(s.url))}" target="_blank" rel="noopener noreferrer">${esc(s.url)}</a>` : ""}<p>${s.publication_date ? `Published ${esc(s.publication_date)} · ` : ""}${esc(sourceOrigin(s))}</p></div><span class="tag">S${i + 1}</span></div>${s.text ? `<p>${esc(s.text)}</p>` : ""}</article>`).join("")}</div>` : emptyInline("No sources attached. Automated research may retrieve sources with Firecrawl. Add page text you have reviewed to label it as human-supplied.")}
      </div></section>
      <section class="panel"><header class="panel-head"><h2>Claims & verification <span class="footnote">(${claims.length})</span></h2><button class="btn btn-small" data-action="add-claim" ${sources.length ? "" : "disabled"}>＋ Add claim</button></header><div class="panel-content">
      ${claims.length ? `<div class="list-stack">${claims.map((c,i) => claimCard(c,i,sources)).join("")}</div>` : emptyInline("No claims have been captured. A claim must link to one of the sources above.")}
      </div></section>`;
  }

  function claimCard(c,index,sources) {
    const source = sources.find(s => s.url === c.source_url) || sources[index] || {};
    const status = c.status || "needs_checking";
    return `<article class="list-card" data-claim-id="${attr(c.id)}"><div class="list-card-head"><div><h4>${esc(c.claim)}</h4><p>${esc(c.source_title || source.title || c.source_url || "Source not identified")}${c.publication_date ? ` · ${esc(c.publication_date)}` : ""}</p></div><span class="tag ${status === "verified" ? "" : "warn"}">${esc(humanStatus(status))}</span></div>${c.quote ? `<blockquote>${esc(c.quote)}</blockquote>` : ""}${c.metric ? `<p>Metric: ${esc(c.metric)}</p>` : ""}${c.date_checked ? `<p>Checked ${esc(date(c.date_checked))}${c.verified_by ? ` by ${esc(c.verified_by)}` : ""}</p>` : ""}${c.note ? `<p>Verification note: ${esc(c.note)}</p>` : ""}${c.issues?.length ? `<p class="not-ready">${esc(c.issues.join(" · "))}</p>` : ""}<div class="card-actions"><button class="btn btn-small" data-action="edit-claim" data-id="${attr(c.id)}">Edit claim</button><button class="btn btn-small" data-action="verify-claim" data-id="${attr(c.id)}">Record human verification</button></div></article>`;
  }

  function sourceOrigin(source) {
    const origin = String(source.origin || source.provider || source.method || source.source_type || source.retrieval_method || source.kind || "").toLowerCase();
    const manualKey = `manual-source:${state.project?.id}:${source.url}`;
    let persistedManual = false;
    try { persistedManual = localStorage.getItem(manualKey) === "true"; } catch (_) {}
    if (state.manualSourceUrls.has(source.url) || persistedManual || /human|manual|user/.test(origin)) return "Human-supplied page text";
    if (/firecrawl/.test(origin)) return "Fetched live with Firecrawl";
    if (origin) return `Source origin: ${source.origin || source.provider || source.method || source.source_type}`;
    return "Research source · origin recorded by the studio";
  }

  function renderScript(p) {
    const scenes = p.script?.scenes || [];
    return `<div class="section-label">02 / Script room</div><section class="panel"><header class="panel-head"><div><h2>Scene-by-scene script</h2><span class="footnote">${scenes.length} ${scenes.length === 1 ? "scene" : "scenes"} · each revision is retained</span></div><div class="heading-actions"><a class="btn btn-small" href="/api/projects/${encodeURIComponent(p.id)}/export-script" download>Export JSON</a><button class="btn btn-small" data-action="import-script">Import JSON</button></div></header><div class="panel-content"><div class="editor-toolbar"><button class="btn btn-small" data-action="add-scene">＋ Add scene</button><button class="btn btn-primary btn-small" data-action="save-script">Save script & recheck</button><button class="btn btn-small" data-action="dry-run">Dry-run chunk plan</button><span class="footnote">No production step is automatically run.</span></div>
      <div class="script-scenes">${scenes.length ? scenes.map((scene,i) => sceneEditor(scene,i,p.claims || [])).join("") : emptyInline("No scenes yet. Add the opening beat, then build the investigation in sequence.")}</div>
      ${p.dry_run ? `<div class="divider"></div><div class="eyebrow">Voice chunk plan · ${esc(p.dry_run.total_characters ?? 0)} characters</div>${(p.dry_run.chunks || []).map(chunk => `<div class="list-card"><div class="list-card-head"><strong>Chunk ${esc(chunk.index)} · ${esc(chunk.characters)} characters</strong><span class="footnote">${esc(chunk.checksum)}</span></div><p>${esc(chunk.text)}</p></div>`).join("")}` : ""}
      </div></section>
      <section class="panel"><header class="panel-head"><h2>Script revisions</h2></header><div class="panel-content">${p.revisions?.length ? [...p.revisions].reverse().map(rev => `<div class="revision-row"><div><strong>Revision ${esc(rev.revision)}</strong><br><span>${esc(rev.reason || "Script updated")}</span></div><time>${esc(stamp(rev.at))}</time></div>`).join("") : emptyInline("Every saved edit creates a revision here.")}</div></section>`;
  }

  function sceneEditor(scene,index,claims) {
    const selected = scene.claim_ids || [];
    return `<article class="scene-card" data-scene="${index}"><header class="scene-top"><span class="scene-number">${String(index + 1).padStart(2,"0")}</span><select data-field="section" aria-label="Scene section">${SCENE_SECTIONS.map(section => `<option value="${attr(section)}" ${scene.section === section ? "selected" : ""}>${esc(section)}</option>`).join("")}</select><span class="footnote">ID ${esc(scene.id || "new")}</span><button class="icon-button scene-delete" data-action="remove-scene" aria-label="Remove scene">×</button></header><div class="scene-body"><textarea data-field="text" placeholder="Write the narration and scene beats…">${esc(scene.text || "")}</textarea><div class="scene-extras"><input data-field="footage_query" type="text" value="${attr(scene.footage_query || "")}" placeholder="Footage search / visual direction"><input data-field="id" type="text" value="${attr(scene.id || "")}" placeholder="Scene ID (optional)"></div>${claims.length ? `<div class="pill-row">${claims.map(c => `<label class="claim-pill"><input type="checkbox" data-claim-id="${attr(c.id)}" ${selected.includes(c.id) ? "checked" : ""}>${esc((c.claim || "").slice(0,56))}${(c.claim || "").length > 56 ? "…" : ""}</label>`).join("")}</div>` : ""}</div></article>`;
  }

  function renderPackaging(p) {
    const titles = p.titles || [];
    const families = [...new Set(titles.map(t => t.family || "Other"))];
    const thumb = p.thumbnail || {};
    return `<div class="section-label">03 / Packaging desk</div><section class="panel"><header class="panel-head"><h2>Title & thumbnail</h2><span class="footnote">${titles.length} title options</span></header><div class="panel-content"><div class="package-layout"><div>
      ${families.length ? families.map(family => `<div class="title-family"><div class="title-family-head">${esc(family)}</div>${titles.filter(t => (t.family || "Other") === family).map(t => `<label class="title-option"><input type="radio" name="selected-title" value="${attr(t.title)}" ${(p.selected_title || "") === t.title ? "checked" : ""}>${esc(t.title)}</label>`).join("")}</div>`).join("") : emptyInline("No title families available yet. Run the title step when the production stage is ready.")}
      <div class="form-field" style="margin-top:12px"><label for="selected-title-custom">Selected title · editable</label><input id="selected-title-custom" type="text" value="${attr(p.selected_title || "")}" placeholder="Choose a title above or write your own"></div>
      </div><div><div class="thumbnail-preview" id="thumbnail-preview"><small>${esc(thumb.object || "Subject / object")}</small><strong>${esc(thumb.text || "The story in one frame")}</strong><small>${esc(thumb.emotion || "Emotional angle")}</small></div><div class="form-grid" style="margin-top:11px"><div class="form-field"><label for="thumb-object">Object</label><input id="thumb-object" type="text" value="${attr(thumb.object || "")}"></div><div class="form-field"><label for="thumb-emotion">Emotion</label><input id="thumb-emotion" type="text" value="${attr(thumb.emotion || "")}"></div><div class="form-field wide"><label for="thumb-text">Thumbnail text</label><input id="thumb-text" type="text" value="${attr(thumb.text || "")}"></div></div></div></div>
      <div class="form-field" style="margin-top:16px"><label for="description">Description</label><textarea id="description" rows="6" placeholder="Documentary description, context and disclosures…">${esc(p.description || "")}</textarea></div><div class="form-actions"><button class="btn btn-primary btn-small" data-action="save-packaging">Save packaging</button></div>
      </div></section>`;
  }

  function renderProjectSettings(p) {
    return `<div class="section-label">Production preferences</div><section class="panel"><header class="panel-head"><h2>Project settings</h2><span class="footnote">Topic and series are fixed after creation</span></header><div class="panel-content"><form id="project-settings-form" class="form-grid">
      <div class="form-field wide"><label for="project-next-idea">Next idea</label><input id="project-next-idea" name="next_idea" type="text" value="${attr(p.next_idea || "")}" placeholder="An adjacent story for the slate"></div>
      <div class="form-field"><label for="project-quality">Output quality</label><select id="project-quality" name="quality"><option ${p.quality === "HD" ? "selected" : ""}>HD</option><option ${p.quality === "4K" ? "selected" : ""}>4K</option></select></div>
      <div class="form-field"><label>Captions</label><label class="claim-pill" style="width:max-content"><input name="burn_captions" type="checkbox" ${p.burn_captions ? "checked" : ""}>Burn captions into video</label></div>
      <div class="form-field wide"><div class="form-actions"><button class="btn btn-small" type="submit">Save project settings</button></div></div>
    </form></div></section>`;
  }

  function renderProduction(p) {
    const footage = p.footage || [];
    const files = p.files || [];
    return `<div class="section-label">04 / Production & delivery</div>
      <section class="panel"><header class="panel-head"><h2>Production controls</h2><span class="footnote">Runs one machine step at a time</span></header><div class="panel-content">
      <div class="editor-toolbar">${["research","script","voice","footage","cut","qa","titles"].map(step => `<button class="btn btn-small" data-action="run-step" data-step="${step}" ${p.job?.running ? "disabled" : ""}>Run ${esc(step)}</button>`).join("")}<button class="btn btn-small" data-action="rerun" ${p.job?.running ? "disabled" : ""}>Resume approved production</button><button class="btn btn-small" data-action="autopilot" ${p.job?.running ? "disabled" : ""}>Start autopilot</button></div>
      <p class="footnote">A single step run performs only that step. Autopilot runs available machine steps in the background and stops for human gates. It never publishes.</p>
      </div></section>
      <section class="panel"><header class="panel-head"><h2>Voice & music</h2></header><div class="panel-content"><div class="two-col">
      <div><div class="eyebrow">Narration preview</div>${files.filter(f => /voice|narrat|audio|mp3|wav/i.test(f.name || f.url || "")).length ? files.filter(f => /voice|narrat|audio|mp3|wav/i.test(f.name || f.url || "")).map(f => `<audio class="audio-player" controls preload="none" src="${attr(fileUrl(p.id,f.url))}"></audio><a class="file-link" href="${attr(fileUrl(p.id,f.url))}" download>${esc(f.name || f.url)}</a>`).join("") : emptyInline("Narration audio will appear here when the voice step has produced a file.")}</div>
      <div><div class="eyebrow">Music bed</div><form id="music-form" class="upload-box"><input type="file" name="music" accept="audio/*" required><label class="claim-pill"><input type="checkbox" name="rights" required>I confirm I have the rights to use this audio.</label><span class="footnote">Audio files only · maximum 50 MB.</span><button class="btn btn-small" type="submit">Upload music</button><div id="music-errors"></div></form></div>
      </div></div></section>
      <section class="panel"><header class="panel-head"><h2>Visual assets <span class="footnote">(${footage.length})</span></h2></header><div class="panel-content">${footage.length ? `<div class="list-stack">${footage.map(f => `<div class="list-card"><div class="list-card-head"><strong>${esc(f.path || f.scene_id || "Footage asset")}</strong><span class="tag">${f.width ? `${esc(f.width)} × ${esc(f.height)}` : "Asset"}</span></div><p>Scene ${esc(f.scene_id || "—")} · ${f.upscaled ? "Upscaled" : "Original"}${f.photographer ? ` · ${esc(f.photographer)}` : ""}</p>${f.url ? `<a href="${attr(f.url)}" target="_blank" rel="noopener noreferrer" class="linkish">${esc(f.url)}</a>` : ""}</div>`).join("")}</div>` : emptyInline("Footage will appear as a machine step completes. Assets are not represented until the backend has produced them.")}</div></section>
      <section class="panel"><header class="panel-head"><h2>Project files</h2><span class="footnote">Allowlisted downloads</span></header><div class="panel-content">${files.length ? `<div class="file-list">${files.map(f => `<a class="file-link" href="${attr(fileUrl(p.id,f.url))}" download><span>${esc(f.name || f.url)}</span><span>Download ↗</span></a>`).join("")}</div>` : emptyInline("No downloadable package files yet. They will be listed here when available.")}</div></section>
      ${p.youtube_url ? `<section class="panel"><header class="panel-head"><h2>Recorded YouTube URL</h2></header><div class="panel-content"><a class="linkish" href="${attr(safeHref(p.youtube_url))}" target="_blank" rel="noopener noreferrer">${esc(p.youtube_url)}</a><p class="footnote">Recorded by a human operator. The studio did not upload or publish this video.</p></div></section>` : ""}`;
  }

  function fileUrl(id, value) {
    if (!value) return "#";
    if (/^(https?:|\/)/i.test(value)) return value;
    return `/files/${encodeURIComponent(id)}/${String(value).split("/").map(encodeURIComponent).join("/")}`;
  }

  function renderAnalyticsPanel(p) {
    const analytics = p.analytics || {};
    const retention = analytics.retention || [];
    return `<div class="section-label">05 / Audience signals</div><section class="panel"><header class="panel-head"><h2>Performance record</h2></header><div class="panel-content"><div class="metric-grid"><div class="metric-card"><b>${analytics.ctr != null ? `${esc(num(analytics.ctr))}%` : "—"}</b><span>Click-through rate</span></div><div class="metric-card"><b>${analytics.average_percentage_viewed != null ? `${esc(num(analytics.average_percentage_viewed))}%` : "—"}</b><span>Average viewed</span></div></div>
      ${retention.length ? `<table class="retention-table"><thead><tr><th>Time</th><th>Retention</th></tr></thead><tbody>${retention.map(r => `<tr><td>${esc(r.time)}</td><td>${esc(r.percent)}%</td></tr>`).join("")}</tbody></table>` : `<p class="footnote">No retention observations recorded.</p>`}
      <div class="divider"></div><form id="analytics-form" class="form-grid"><div class="form-field"><label for="ctr">CTR (%)</label><input id="ctr" name="ctr" type="number" min="0" max="100" step="0.1" value="${attr(analytics.ctr ?? "")}" placeholder="0–100"></div><div class="form-field"><label for="apv">Average viewed (%)</label><input id="apv" name="average_percentage_viewed" type="number" min="0" max="100" step="0.1" value="${attr(analytics.average_percentage_viewed ?? "")}" placeholder="0–100"></div><div class="form-field wide"><label for="retention-csv">Retention CSV · time in seconds (optional)</label><textarea id="retention-csv" name="retention_csv" rows="3" placeholder="time,percent&#10;30,68">${retention.length ? attr(`time,percent\n${retention.map(r => `${r.time},${r.percent}`).join("\n")}`) : ""}</textarea></div><div class="form-field wide"><div class="form-actions"><button class="btn btn-primary btn-small" type="submit">Save analytics</button></div></div></form>
      ${p.analytics_report ? `<div class="divider"></div><div class="eyebrow">Plain-language result</div>${renderAnalyticsReport(p.analytics_report)}` : ""}
      </div></section>`;
  }

  function renderFactCheck(p) {
    const fc = p.fact_check;
    if (!fc) return `<section class="panel"><header class="panel-head"><h3>Fact check</h3></header><div class="panel-content">${emptyInline("Run the fact-check workflow to see current results.")}</div></section>`;
    const blocking = fc.blocking || []; const warnings = fc.warnings || [];
    return `<section class="panel"><header class="panel-head"><h3>Fact check</h3><span class="tag ${fc.passed ? "" : "warn"}">${fc.passed ? "Passed" : "Needs review"}</span></header><div class="panel-content"><p class="footnote">${esc(fc.words ?? 0)} words · ${esc(num(fc.estimated_minutes,1))} min estimate</p>${blocking.length ? `<div class="eyebrow not-ready">Blocking</div>${blocking.map(item => `<div class="fact-row"><strong>${esc(typeof item === "string" ? item : item.claim || item.message || JSON.stringify(item))}</strong>${item.note ? `<p>${esc(item.note)}</p>` : ""}</div>`).join("")}` : `<p class="ready">No blocking issues reported</p>`}${warnings.length ? `<div class="eyebrow" style="margin-top:13px">Warnings</div>${warnings.map(item => `<div class="fact-row"><p>${esc(typeof item === "string" ? item : item.message || item.claim || JSON.stringify(item))}</p></div>`).join("")}` : ""}</div></section>`;
  }

  function renderQA(p) {
    const qa = p.qa;
    if (!qa) return `<section class="panel"><header class="panel-head"><h3>Quality assurance</h3></header><div class="panel-content">${emptyInline("Quality checks appear after the QA step has run.")}</div></section>`;
    const checks = (items, kind) => (items || []).length
      ? (items || []).map(item => `<div class="readiness"><span>${esc(item.label || "Unnamed check")}</span><span class="${item.passed ? "ready" : "not-ready"}">${item.passed ? "Pass" : "Review"}${kind ? ` · ${kind}` : ""}</span></div>`).join("")
      : `<p class="footnote">No ${kind.toLowerCase()} checks recorded.</p>`;
    return `<section class="panel"><header class="panel-head"><h3>Quality assurance</h3><span class="tag ${qa.passed ? "" : "warn"}">${qa.passed ? "Passed" : "In review"}</span></header><div class="panel-content"><div class="eyebrow">Required checks</div>${checks(qa.required,"required")}<div class="divider"></div><div class="eyebrow">Advisory checks</div>${checks(qa.advisory,"advisory")}<div class="divider"></div><div class="readiness"><span>Upscaled clips</span><strong>${esc(qa.upscaled_clips ?? 0)}</strong></div></div></section>`;
  }

  function plainValue(value) {
    if (value == null) return "not recorded";
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
    if (Array.isArray(value)) return value.map(plainValue).join("; ");
    return Object.entries(value).map(([key,item]) => `${key.replaceAll("_"," ")}: ${plainValue(item)}`).join(" · ");
  }

  function eventSummary(kind,event) {
    if (!event) return "";
    const verb=kind==="drop"?"The steepest drop":"The biggest rise";
    const parts=[];
    if(event.start!=null || event.end!=null) parts.push(`from ${plainValue(event.start ?? "—")} to ${plainValue(event.end ?? "—")}`);
    if(event.change!=null) parts.push(`change ${plainValue(event.change)}`);
    if(event.percentage_points_per_second!=null) parts.push(`${plainValue(event.percentage_points_per_second)} percentage points per second`);
    const scene=[event.section,event.scene_id].filter(Boolean).join(" · ");
    if(scene) parts.push(`scene ${scene}`);
    return `${verb}: ${parts.length?parts.join(", "):plainValue(event)}`;
  }

  function renderAnalyticsReport(report) {
    if (typeof report === "string") return `<p class="plain-report">${esc(report)}</p>`;
    if (!report || typeof report !== "object") return `<p class="plain-report">${esc(plainValue(report))}</p>`;
    const lines=[];
    if(report.comparison!=null) lines.push(`<p><strong>Comparison</strong> ${esc(plainValue(report.comparison))}</p>`);
    if(report.baseline_count!=null) lines.push(`<p><strong>Baseline</strong> Based on ${esc(plainValue(report.baseline_count))} comparable ${Number(report.baseline_count)===1?"project":"projects"}.</p>`);
    if(report.median_ctr!=null) lines.push(`<p><strong>Median CTR</strong> ${esc(plainValue(report.median_ctr))}${typeof report.median_ctr==="number"?"%":""}</p>`);
    if(report.median_average_percentage_viewed!=null) lines.push(`<p><strong>Median average viewed</strong> ${esc(plainValue(report.median_average_percentage_viewed))}${typeof report.median_average_percentage_viewed==="number"?"%":""}</p>`);
    if(report.retention && typeof report.retention==="object") {
      const retention=report.retention;
      if(retention.at_30_seconds!=null) lines.push(`<p><strong>At 30 seconds</strong> ${esc(plainValue(retention.at_30_seconds))}${typeof retention.at_30_seconds==="number"?"%":""}</p>`);
      if(retention.at_30_scene!=null) lines.push(`<p><strong>At the 30-second scene</strong> ${esc(plainValue(retention.at_30_scene))}</p>`);
      if(retention.steepest_drop) lines.push(`<p>${esc(eventSummary("drop",retention.steepest_drop))}</p>`);
      if(retention.biggest_rise) lines.push(`<p>${esc(eventSummary("rise",retention.biggest_rise))}</p>`);
      if(retention.scene_timing!=null) lines.push(`<p><strong>Scene timing</strong> ${esc(plainValue(retention.scene_timing))}</p>`);
    }
    return lines.length?`<div class="plain-report">${lines.join("")}</div>`:`<p class="plain-report">${esc(plainValue(report))}</p>`;
  }

  function renderApprovals(p) {
    const approvals = p.approvals || {};
    return `<section class="panel"><header class="panel-head"><h3>Human gates</h3></header><div class="panel-content">${["topic","research","script","packaging"].map(key => `<div class="readiness"><span>${esc(humanStatus(key))}</span><span class="${approvals[key] ? "ready" : "not-ready"}">${approvals[key] ? "Approved" : "Pending"}</span></div>`).join("")}<p class="footnote" style="margin-bottom:0">A gate advances only once and only at its correct stage. There is no automatic publishing.</p></div></section>`;
  }

  function renderHistory(p) {
    const history = p.history || [];
    return `<section class="panel"><header class="panel-head"><h3>Stage history</h3></header><div class="panel-content">${history.length ? [...history].reverse().map(item => `<div class="history-item"><time>${esc(stamp(item.at))}</time><span>${esc(humanStatus(item.from || "Created"))} → ${esc(humanStatus(item.to || ""))}<br><small>${esc(item.actor || "Studio")}</small></span></div>`).join("") : emptyInline("Status transitions will be recorded here.")}</div></section>`;
  }

  function renderGates(p) {
    const status = p.status;
    const gateMap = {
      IDEA: ["topic", "Confirm topic", "Approve this story premise and continue to selection."],
      RESEARCHING: ["research", "Approve research", "Confirm the sources and claims before continuing the investigation."],
      SCRIPT_REVIEW: ["script", "Approve script", "Confirm the fact-checked script before production."],
      THUMBNAIL: ["packaging", "Approve packaging", "Confirm the title, thumbnail and description."],
      UPLOAD_READY: ["published", "Record published URL", "Record a URL after you publish manually. This app never uploads or publishes."]
    };
    const index = STATUSES.indexOf(status);
    const approvals = p.approvals || {};
    const reapprovals = [];
    if (index >= 3 && !approvals.research) reapprovals.push(["research","Reapprove research","Restore the withdrawn research approval. The production stage will not move."]);
    if (index >= 6 && !approvals.script) reapprovals.push(["script","Reapprove script","Restore the withdrawn script approval. The production stage will not move."]);
    if (status === "UPLOAD_READY" && !approvals.packaging) reapprovals.push(["packaging","Reapprove packaging","Restore the withdrawn packaging approval. The production stage will not move."]);
    const found = gateMap[status];
    if (!found && !reapprovals.length) return `<section class="panel"><header class="panel-head"><h2>Human approval</h2></header><div class="panel-content"><p class="footnote">No gate is currently available at <strong>${esc(humanStatus(status))}</strong>. Complete the current stage first. Gates cannot be skipped.</p></div></section>`;
    return `<section class="panel"><header class="panel-head"><h2>${found ? "Human approval" : "Approval restoration"}</h2><span class="tag warn">Decision point</span></header><div class="panel-content">${found ? gateCard(...found) : ""}${reapprovals.map(item=>gateCard(...item,true)).join("")}</div></section>`;
  }

  function gateCard(gate,title,copy,reapproval=false) {
    return `<div class="gate-block"><h3>${esc(title)}</h3><p>${esc(copy)}</p><div class="gate-actions">${gate === "published" ? `<input type="url" id="gate-youtube" placeholder="https://www.youtube.com/watch?v=…" aria-label="YouTube URL">` : ""}<textarea data-gate-note="${gate}" placeholder="${reapproval ? "Optional reapproval note" : "Optional decision note"}"></textarea><button class="btn btn-primary" data-action="gate" data-gate="${gate}" data-reapproval="${reapproval ? "true" : "false"}">${esc(title)}</button></div></div>`;
  }

  function emptyInline(text) { return `<div class="footnote">${esc(text)}</div>`; }

  function wireProjectActions() {
    const p = state.project;
    app.querySelectorAll("[data-action]").forEach(button => {
      button.addEventListener("click", () => {
        const action = button.dataset.action;
        const handlers = {
          "save-score": () => saveScore(button), "suggest-score": () => suggestScore(button), "add-source": () => openSourceForm(),
          "add-claim": () => openClaimForm(), "edit-claim": () => openClaimForm(button.dataset.id),
          "verify-claim": () => openVerifyForm(button.dataset.id), "add-scene": addScene,
          "remove-scene": () => button.closest(".scene-card").remove(), "save-script": saveScript,
          "import-script": importScript, "dry-run": dryRun, "save-packaging": savePackaging,
          "run-step": () => runStep(button.dataset.step),
          "rerun": rerun, "autopilot": autopilot, "gate": () => submitGate(button.dataset.gate, button.dataset.reapproval === "true")
        };
        if (handlers[action]) handlers[action]();
      });
    });
    const titleRadios = app.querySelectorAll('[name="selected-title"]');
    titleRadios.forEach(radio => radio.addEventListener("change", () => {
      if (radio.checked) {
        state.project.selected_title = radio.value;
        const custom = document.getElementById("selected-title-custom");
        if (custom) custom.value = radio.value;
      }
    }));
    ["thumb-object","thumb-emotion","thumb-text"].forEach(id => document.getElementById(id)?.addEventListener("input", updateThumbPreview));
    document.getElementById("music-form")?.addEventListener("submit", uploadMusic);
    document.getElementById("analytics-form")?.addEventListener("submit", saveAnalytics);
    document.getElementById("project-settings-form")?.addEventListener("submit", saveProjectSettings);
  }

  function updateThumbPreview() {
    const preview = document.getElementById("thumbnail-preview");
    if (!preview) return;
    preview.querySelector("small").textContent = document.getElementById("thumb-object").value || "Subject / object";
    preview.querySelector("strong").textContent = document.getElementById("thumb-text").value || "The story in one frame";
    preview.querySelectorAll("small")[1].textContent = document.getElementById("thumb-emotion").value || "Emotional angle";
  }

  async function postAction(action, body, options = {}) {
    const button = options.button;
    if (button) { button.disabled = true; button.dataset.oldText = button.textContent; button.textContent = options.loadingText || "Working…"; }
    try {
      const result = await request(`/api/projects/${encodeURIComponent(state.project.id)}/${action}`, { method: "POST", body: JSON.stringify(body || {}) });
      if (["autopilot","run","rerun"].includes(action) && result && typeof result === "object" && "running" in result && !("status" in result)) {
        state.project.job = result;
        state.pollingActive = Boolean(result.running);
        toast(result.message || "Background job started.");
        renderProject();
        if (result.running) pollJobs();
        return result;
      }
      if (result && typeof result === "object" && ("status" in result || "topic" in result)) state.project = result;
      else if (result && action === "dry_run") state.project.dry_run = result;
      renderProject(); toast(options.success || "Saved.");
      return result;
    } catch (error) {
      showError(".stage-main", error);
      if (button) { button.disabled = false; button.textContent = button.dataset.oldText || "Retry"; }
      return null;
    }
  }

  async function saveScore(button) {
    const p = state.project; const score = {};
    for (const [key,,max] of SCORE_FIELDS) {
      const value = Number(document.getElementById(`score-${key}`).value);
      if (!Number.isInteger(value) || value < 0 || value > max) return toast(`${key} must be an integer from 0 to ${max}.`, true);
      score[key] = value;
    }
    score.confirmed = true;
    score.override_reason = document.getElementById("override-reason").value.trim();
    await postAction("score", score, { button, loadingText: "Saving…", success: "Score saved and confirmed." });
  }
  async function suggestScore(button) {
    button = button || app.querySelector('[data-action="suggest-score"]');
    await postAction("suggest_score", {}, { button, loadingText: "Suggesting…", success: "Score suggestion returned. It has not been applied." });
  }

  function openSourceForm() {
    modal("Add reviewed source", `<p class="footnote" style="margin-top:0">Text entered here is labelled human-supplied. Automated research may separately retrieve sources live with Firecrawl.</p><form id="source-form" class="form-grid"><div class="form-field wide"><label>Source title</label><input name="title" required></div><div class="form-field wide"><label>Source URL</label><input name="url" type="url" required placeholder="https://…"></div><div class="form-field"><label>Publication date</label><input name="publication_date" type="date"></div><div class="form-field wide"><label>Reviewed page text</label><textarea name="text" rows="7" required placeholder="Paste the relevant passage you reviewed…"></textarea></div><div class="form-field wide"><div id="form-errors"></div><div class="form-actions"><button class="btn" type="button" data-close>Cancel</button><button class="btn btn-primary">Add source</button></div></div></form>`, node => {
      node.querySelector("[data-close]").onclick = closeModal;
      node.querySelector("form").onsubmit = async e => {
        e.preventDefault();
        const body = Object.fromEntries(new FormData(e.currentTarget));
        try { const updated = await request(`/api/projects/${encodeURIComponent(state.project.id)}/source`, { method:"POST", body:JSON.stringify(body) }); state.manualSourceUrls.add(body.url);try{localStorage.setItem(`manual-source:${state.project.id}:${body.url}`,"true");}catch(_){} state.project=updated;closeModal();renderProject();toast("Source added and labelled human-supplied."); }
        catch(error) { showError("#form-errors", error); }
      };
    });
  }

  function openClaimForm(id) {
    const existing = (state.project.claims || []).find(c => String(c.id) === String(id));
    const sourceIndex = existing ? Math.max(0,(state.project.sources || []).findIndex(s => s.url === existing.source_url)) : 0;
    const sourceOptions = (state.project.sources || []).map((s,i) => `<option value="${i}" ${i === sourceIndex ? "selected" : ""}>${i+1}. ${esc(s.title || s.url)}</option>`).join("");
    if (!sourceOptions) return toast("Add a reviewed source before recording a claim.", true);
    modal(existing ? "Edit claim" : "Record a claim", `<form id="claim-form" class="form-grid"><div class="form-field wide"><label>Claim</label><textarea name="claim" required rows="3">${esc(existing?.claim || "")}</textarea></div><div class="form-field wide"><label>Supporting quote</label><textarea name="quote" required rows="4">${esc(existing?.quote || "")}</textarea></div><div class="form-field"><label>Source</label><select name="source_index">${sourceOptions}</select></div><div class="form-field"><label>Metric / detail (optional)</label><input name="metric" value="${attr(existing?.metric || "")}"></div><div class="form-field wide"><div id="form-errors"></div><div class="form-actions"><button class="btn" type="button" data-close>Cancel</button><button class="btn btn-primary">${existing ? "Save claim" : "Add claim"}</button></div></div></form>`, node => {
      node.querySelector("[data-close]").onclick=closeModal;
      node.querySelector("form").onsubmit=async e=>{
        e.preventDefault();const body=Object.fromEntries(new FormData(e.currentTarget));body.source_index=Number(body.source_index);if(existing)body.id=existing.id;else if(!body.metric)delete body.metric;
        try{state.project=await request(`/api/projects/${encodeURIComponent(state.project.id)}/claim`,{method:"POST",body:JSON.stringify(body)});closeModal();renderProject();toast("Claim saved. Research approval has been invalidated and verification reset.");}
        catch(error){showError("#form-errors",error);}
      };
    });
  }

  function openVerifyForm(id) {
    const claim=(state.project.claims||[]).find(c=>String(c.id)===String(id)); if(!claim)return;
    modal("Record human verification", `<p class="footnote">${esc(claim.claim)}</p><form id="verify-form" class="form-grid"><div class="form-field"><label>Outcome</label><select name="status"><option value="verified">Verified</option><option value="rejected">Rejected</option><option value="needs_checking">Needs checking</option></select></div><div class="form-field wide"><label>Verification note (required)</label><textarea name="note" rows="5" required placeholder="What did you inspect? What supports this decision?"></textarea></div><div class="form-field wide"><div id="form-errors"></div><div class="form-actions"><button class="btn" type="button" data-close>Cancel</button><button class="btn btn-primary">Record decision</button></div></div></form>`, node=>{
      node.querySelector("[data-close]").onclick=closeModal;
      node.querySelector("form").onsubmit=async e=>{e.preventDefault();const body=Object.fromEntries(new FormData(e.currentTarget));body.id=id;try{state.project=await request(`/api/projects/${encodeURIComponent(state.project.id)}/verify_claim`,{method:"POST",body:JSON.stringify(body)});closeModal();renderProject();toast("Verification decision recorded.");}catch(error){showError("#form-errors",error);}};
    });
  }

  function addScene() {
    const editor=app.querySelector(".editor-toolbar");
    if(!editor)return;
    const scenes=app.querySelector(".script-scenes");
    if(!scenes)return;
    const empty=scenes.querySelector(".footnote");
    if(empty && empty.textContent.includes("No scenes yet")) empty.remove();
    const holder=document.createElement("div");holder.innerHTML=sceneEditor({id:"",section:"hook",text:"",claim_ids:[],footage_query:""},app.querySelectorAll(".scene-card").length,state.project.claims||[]);
    const card=holder.firstElementChild;
    scenes.append(card);
    card.querySelector('[data-action="remove-scene"]').addEventListener("click", e => {
      e.preventDefault();
      if (card.querySelector('[data-field="text"]').value.trim() && !window.confirm("Remove this scene and its unsaved edits?")) return;
      card.remove();
    });
    card.querySelector('[data-field="text"]').focus();
  }

  async function saveScript(button) {
    const scenes=[...app.querySelectorAll(".scene-card")].map(card=>{
      const selected=[...card.querySelectorAll('[data-claim-id]:checked')].map(input=>input.dataset.claimId);
      const scene={section:card.querySelector('[data-field="section"]').value,text:card.querySelector('[data-field="text"]').value,footage_query:card.querySelector('[data-field="footage_query"]').value,claim_ids:selected};
      const id=card.querySelector('[data-field="id"]').value.trim(); if(id)scene.id=id;
      return scene;
    });
    if(!scenes.every(s=>s.text.trim())) return toast("Each scene needs script text before saving.",true);
    const target=button?.currentTarget || app.querySelector('[data-action="save-script"]');
    await postAction("script",{scenes},{button:target,loadingText:"Saving & checking…",success:"Script saved, revised and rechecked."});
  }

  function importScript() {
    modal("Import script JSON", `<p class="footnote" style="margin-top:0">Paste the exported script object with a scenes array. Import saves a revision immediately and rechecks the script.</p><form id="import-form"><div class="form-field"><label>Script JSON object</label><textarea name="text" required rows="12" placeholder='{"scenes":[{"id":"scene-1","section":"hook","text":"…","claim_ids":[],"footage_query":"…"}]}'></textarea></div><div id="form-errors"></div><div class="form-actions"><button type="button" class="btn" data-close>Cancel</button><button class="btn btn-primary">Import & save revision</button></div></form>`, node=>{
      node.querySelector("[data-close]").onclick=closeModal;
      node.querySelector("form").onsubmit=async e=>{e.preventDefault();let text=new FormData(e.currentTarget).get("text");try{const parsed=JSON.parse(text);if(Array.isArray(parsed))text=JSON.stringify({scenes:parsed});else if(!parsed || !Array.isArray(parsed.scenes))throw new Error("Expected an exported object with a scenes array.");text=typeof text==="string"?text:JSON.stringify(parsed);const result=await request(`/api/projects/${encodeURIComponent(state.project.id)}/import_script`,{method:"POST",body:JSON.stringify({text})});state.project=result;closeModal();renderProject();toast("Script imported, revision saved and fact check refreshed.");}catch(error){showError("#form-errors",error);}};
    });
  }

  async function dryRun() {
    const button=app.querySelector('[data-action="dry-run"]'); const result=await postAction("dry_run",{}, {button,loadingText:"Planning…",success:"Chunk plan ready."});
    if(result && !result.status) { state.project.dry_run=result; renderProject(); }
  }

  async function savePackaging(button) {
    const selected=document.getElementById("selected-title-custom")?.value.trim() || "";
    const thumbnail={object:document.getElementById("thumb-object").value,emotion:document.getElementById("thumb-emotion").value,text:document.getElementById("thumb-text").value};
    await postAction("packaging",{selected_title:selected,thumbnail,description:document.getElementById("description").value},{button:button?.currentTarget || app.querySelector('[data-action="save-packaging"]'),loadingText:"Saving…",success:"Packaging saved."});
  }

  async function saveProjectSettings(event) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const body = {
      next_idea: String(form.get("next_idea") || "").trim(),
      quality: String(form.get("quality") || "HD"),
      burn_captions: form.has("burn_captions")
    };
    await postAction("settings", body, { button: event.currentTarget.querySelector('[type="submit"]'), loadingText: "Saving…", success: "Project settings saved." });
  }

  async function submitGate(gate, reapproval=false) {
    const body={gate,note:app.querySelector(`[data-gate-note="${gate}"]`)?.value || ""};
    if(gate==="published") {
      body.youtube_url=document.getElementById("gate-youtube")?.value.trim() || "";
      if(!body.youtube_url) return toast("Record the YouTube URL before confirming this gate.",true);
      if(!/^https:\/\/(www\.)?(youtube\.com|youtu\.be)\//i.test(body.youtube_url)) return toast("Enter a YouTube URL beginning with https://youtube.com or https://youtu.be.",true);
      if(!window.confirm("Record this URL after your manual publication? This app never uploads or publishes."))return;
    } else if(!window.confirm(reapproval ? `Restore ${gate} approval? The project will stay at its current stage.` : `Confirm the ${gate} gate? This advances the project exactly one stage.`)) return;
    await postAction("gate",body,{button:app.querySelector(`[data-gate="${gate}"][data-reapproval="${reapproval}"]`),loadingText:reapproval ? "Restoring…" : "Advancing…",success:reapproval ? `${gate} approval restored without moving the project.` : `${gate} gate recorded.`});
  }

  async function runStep(step) {
    if(!window.confirm(`Run the ${step} machine step for this project?`))return;
    await postAction("run",{step},{button:app.querySelector(`[data-step="${step}"]`),loadingText:"Starting…",success:`${step} step started.`});
    pollJobs();
  }
  async function rerun() {
    if(!window.confirm("Resume invalidated approved production at its current stage?"))return;
    await postAction("rerun",{},{button:app.querySelector('[data-action="rerun"]'),loadingText:"Resuming…",success:"Production resume requested."});pollJobs();
  }
  async function autopilot() {
    if(!window.confirm("Start background autopilot? It will stop at human gates and never publish."))return;
    await postAction("autopilot",{},{button:app.querySelector('[data-action="autopilot"]'),loadingText:"Starting…",success:"Autopilot started."});pollJobs();
  }

  async function saveAnalytics(event) {
    event.preventDefault(); const form=new FormData(event.currentTarget);
    const ctr=Number(form.get("ctr"));const average_percentage_viewed=Number(form.get("average_percentage_viewed"));
    if(!Number.isFinite(ctr)||ctr<0||ctr>100||!Number.isFinite(average_percentage_viewed)||average_percentage_viewed<0||average_percentage_viewed>100)return toast("CTR and average viewed must both be values from 0 to 100.",true);
    const retention_csv=String(form.get("retention_csv")||"").trim();
    await postAction("analytics",{ctr,average_percentage_viewed,...(retention_csv?{retention_csv}:{})},{button:event.currentTarget.querySelector('[type="submit"]'),loadingText:"Saving…",success:"Analytics saved."});
  }

  async function uploadMusic(event) {
    event.preventDefault();const form=event.currentTarget;const file=form.querySelector('input[type="file"]').files[0];
    if(!file)return toast("Choose an audio file first.",true);
    if(file.size>50*1024*1024)return showError("#music-errors",new Error("Audio must be 50 MB or smaller."));
    if(!form.querySelector('[name="rights"]').checked)return showError("#music-errors",new Error("Confirm that you have the rights to use this audio."));
    const button=form.querySelector('[type="submit"]');button.disabled=true;button.textContent="Uploading…";
    try {
      const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(",")[1]);reader.onerror=()=>reject(new Error("Could not read the selected audio file."));reader.readAsDataURL(file);});
      await postAction("music",{filename:file.name,data,rights_confirmed:true},{success:"Music uploaded with rights confirmation."});
    } catch(error) { showError("#music-errors",error); }
    finally { const current=form.querySelector('[type="submit"]');if(current){current.disabled=false;current.textContent="Upload music";} }
  }

  function hasRunningJobs() {
    const overviewJobs=Object.values(state.overview?.jobs || {}).some(job=>job?.running);
    const currentProject=path().match(/^\/project\/([^/]+)$/);
    return overviewJobs || Boolean(currentProject && state.project?.job?.running);
  }

  async function pollJobs() {
    if(state.pollTimer || !hasRunningJobs()) return;
    state.pollingActive=true;
    state.pollTimer=window.setTimeout(async function tick(){
      state.pollTimer=null;
      const wasRunning=state.pollingActive;
      const currentProjectId=path().match(/^\/project\/([^/]+)$/)?.[1];
      try {
        const overview=await request("/api/overview"); state.overview=overview;
        if(currentProjectId) {
          const id=decodeURIComponent(currentProjectId);
          const previouslyThisJob=Boolean(state.project?.job?.running || overview.jobs?.[id]?.running);
          if (!previouslyThisJob && !wasRunning) {
            state.pollingActive=hasRunningJobs();
            if(state.pollingActive) pollJobs();
            return;
          }
          const updated=await request(`/api/projects/${encodeURIComponent(id)}`);
          const previous=state.project?.job;
          state.project=updated;
          if(path()===`/project/${encodeURIComponent(id)}` && (previous?.running || updated.job?.running || overview.jobs?.[id]?.running))renderProject();
          if(previous?.running && !updated.job?.running) toast(updated.job?.error ? `Background job failed: ${updated.job.error}` : "Background job finished.");
        }
        const anyRunning=hasRunningJobs();
        if(wasRunning && !anyRunning && path()==="/") renderProjects();
        state.pollingActive=anyRunning;
      } catch (_) { /* Keep the last good screen; next poll retries. */ }
      if(state.pollingActive) pollJobs();
    },2000);
  }

  async function renderSettings() {
    app.innerHTML=`<div class="eyebrow">Loading server readiness</div><div class="skeleton" style="height:190px;margin-top:16px"></div>`;
    try { state.settings = await request("/api/settings"); }
    catch (error) {
      app.innerHTML=`<section class="page-heading"><div><div class="eyebrow">Studio configuration / readiness</div><h1>Configuration<br>unavailable.</h1><p>Could not load provider readiness from the server.</p></div></section><div id="settings-error"></div><button class="btn" id="retry-settings">Retry readiness</button>`;
      showError("#settings-error",error);document.getElementById("retry-settings").onclick=renderSettings;return;
    }
    const settings=state.settings || {};
    app.innerHTML=`<section class="page-heading"><div><div class="eyebrow">Studio configuration / readiness</div><h1>Ready to make<br>the next move?</h1><p>Provider keys stay on the server. This screen reports readiness and missing configuration names only—never secret values.</p></div></section>
    <section class="settings-hero"><div><div class="eyebrow" style="color:rgba(243,241,232,.66)">Production environment</div><h2>${settings.ffmpeg ? "Media tools are available." : "Media tools need attention."}</h2><p>Generation and delivery depend on provider configuration and a working local media pipeline.</p></div><span class="settings-mark">${settings.ffmpeg ? "FFMPEG / READY" : "FFMPEG / NOT READY"}</span></section>
    <div class="two-col" style="margin-top:20px"><section class="panel"><header class="panel-head"><h2>Provider readiness</h2></header><div class="panel-content">${settings.providers?.length ? settings.providers.map(provider=>`<div class="readiness"><div><strong>${esc(provider.name)}</strong>${provider.missing?.length?`<div class="footnote" style="margin-top:4px">Missing: ${provider.missing.map(esc).join(", ")}</div>`:""}</div><span class="${provider.ready?"ready":"not-ready"}">${provider.ready?"Ready":"Needs setup"}</span></div>`).join("") : emptyInline("No provider status was returned by the settings service.")}</div></section>
    <section class="panel"><header class="panel-head"><h2>Configure safely</h2></header><div class="panel-content"><ol class="guide-list"><li><div><strong>Set provider keys on the server</strong><p>Use your deployment environment or secret manager. Keys are not entered or exposed in this browser.</p></div></li><li><div><strong>Restart the application service</strong><p>After configuration, allow the backend to reload its environment and verify readiness here.</p></div></li><li><div><strong>Keep review in the loop</strong><p>Providers can prepare research and production assets. A human retains every editorial gate and publishing action.</p></div></li></ol><div class="divider"></div><button class="btn btn-small" id="refresh-settings">↻ Refresh readiness</button></div></section></div>
    <div class="section-label">Environment notes</div><section class="panel"><div class="panel-content"><p class="footnote">Configuration values are intentionally never returned by the API. Missing variable names may be displayed to help your operator configure the deployment; paste secrets into the server's secure environment, not into the UI.</p><p class="footnote">FFmpeg status: <strong>${settings.ffmpeg ? "available" : "unavailable"}</strong>. Media and music files are only available after the backend reports them.</p></div></section>`;
    document.getElementById("refresh-settings").onclick=async()=>{await renderSettings();toast("Readiness refreshed.");};
  }

  async function renderAnalytics() {
    loading("Gathering recorded audience signals");
    const overviewProjects=state.overview?.projects||[];
    const details=await Promise.allSettled(overviewProjects.map(p=>request(`/api/projects/${encodeURIComponent(p.id)}`)));
    if(path()!=="/analytics") return;
    const projects=overviewProjects.map((project,index)=>details[index].status==="fulfilled"?{...project,...details[index].value}:project);
    const measured=projects.filter(p=>(p.analytics && (p.analytics.ctr!=null || p.analytics.average_percentage_viewed!=null)) || p.analytics_report);
    const avg=(key)=>{const values=measured.map(p=>Number(p.analytics?.[key])).filter(Number.isFinite);return values.length?values.reduce((a,b)=>a+b,0)/values.length:null;};
    const ctr=avg("ctr"),apv=avg("average_percentage_viewed");
    app.innerHTML=`<section class="page-heading"><div><div class="eyebrow">Audience desk / actual project records</div><h1>What held<br>their attention?</h1><p>Channel comparison from analytics entered for your projects. No sample data, forecast charts or invented performance.</p></div></section>
    <div class="metric-grid" style="max-width:650px"><div class="metric-card"><b>${ctr==null?"—":`${num(ctr)}%`}</b><span>Mean recorded CTR</span></div><div class="metric-card"><b>${apv==null?"—":`${num(apv)}%`}</b><span>Mean average viewed</span></div></div>
    <div class="section-label">Project comparison / ${measured.length} measured of ${projects.length}</div>
    ${projects.length?`<section class="panel"><header class="panel-head"><h2>Production performance</h2><span class="footnote">Only recorded metrics shown</span></header><div>${projects.map(p=>`<div class="analytics-project"><div><h3>${esc(p.topic)}</h3><small>${esc(p.pillar||"—")} · ${esc(humanStatus(p.status))}</small></div><div><b>${p.analytics?.ctr==null?"—":`${num(p.analytics.ctr)}%`}</b><small class="footnote" style="display:block">CTR</small></div><div><b>${p.analytics?.average_percentage_viewed==null?"—":`${num(p.analytics.average_percentage_viewed)}%`}</b><small class="footnote" style="display:block">APV</small></div><a class="btn btn-small" href="/project/${encodeURIComponent(p.id)}">Open record ↗</a></div>`).join("")}</div></section>`:`<section class="empty-state"><div class="empty-art"><div class="empty-seal">SIGNAL</div></div><div class="empty-copy"><div class="eyebrow">No project records</div><h2>Nothing to compare yet.</h2><p>When projects exist, their actual CTR and average percentage viewed will appear here. There are no sample records.</p><a href="/" class="btn">Go to projects</a></div></section>`}
    <div class="section-label">Plain-language results</div><section class="panel"><header class="panel-head"><h2>Recorded reports</h2></header><div class="panel-content">${measured.length?measured.map(p=>`<div class="fact-row"><strong>${esc(p.topic)}</strong>${p.analytics_report?renderAnalyticsReport(p.analytics_report):`<p>Recorded: CTR ${p.analytics?.ctr==null?"—":`${num(p.analytics.ctr)}%`}; average percentage viewed ${p.analytics?.average_percentage_viewed==null?"—":`${num(p.analytics.average_percentage_viewed)}%`}.</p>`}</div>`).join(""):emptyInline("Reports will be shown here once project analytics have been recorded.")}</div></section>`;
  }

  document.addEventListener("click", event => {
    const link=event.target.closest("a[href]");
    if(!link || link.target || link.hasAttribute("download") || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const url=new URL(link.href,location.href);
    if(url.origin===location.origin && !url.pathname.startsWith("/api/") && !url.pathname.startsWith("/files/")) {
      event.preventDefault();
      if(url.pathname===path())return;
      history.pushState({}, "", url.pathname); renderRoute(); window.scrollTo(0,0);
    }
  });
  window.addEventListener("popstate", renderRoute);
  loadOverview();
})();

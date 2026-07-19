"use strict";

const app = document.querySelector("#app");
const toastRegion = document.querySelector("#toast-region");
const FIXTURE_EMAIL = "alex.green.uoft@clawbench.cc";
const FIXTURE_NAME = "Alex Green";
const REQUESTER_ID = 1001;
const TEST_AGENT_ID = 2002;
const SUPPORT_GROUP_ID = 3001;
const EXPECTED_DESCRIPTION =
  "The invoice for Order #10042 includes a duplicate $125.00 service charge. Please review the billing discrepancy and issue a corrected invoice.";

let state = null;
let draftTimer = null;

class ApiError extends Error {
  constructor(message, status, payload) {
    super(message);
    this.status = status;
    this.payload = payload;
  }
}

async function api(path, options = {}) {
  const config = { ...options, headers: { ...(options.headers || {}) } };
  if (config.body && !config.headers["Content-Type"]) {
    config.headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, config);
  let payload = {};
  try {
    payload = await response.json();
  } catch (_error) {
    payload = { error: "The local service returned an unreadable response" };
  }
  if (!response.ok) {
    throw new ApiError(payload.error || `Request failed (${response.status})`, response.status, payload);
  }
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function icon(name, label = "") {
  const aria = label ? `aria-label="${escapeHtml(label)}"` : "aria-hidden=\"true\"";
  return `<i data-lucide="${name}" ${aria}></i>`;
}

function activateIcons() {
  if (window.lucide) {
    window.lucide.createIcons({ attrs: { "stroke-width": 1.8 } });
  }
}

function toast(message) {
  const node = document.createElement("div");
  node.className = "toast";
  node.textContent = message;
  toastRegion.append(node);
  window.setTimeout(() => node.remove(), 3600);
}

function navigate(path, replace = false) {
  if (replace) {
    history.replaceState({}, "", path);
  } else {
    history.pushState({}, "", path);
  }
  renderRoute();
}

function currentPath() {
  return window.location.pathname;
}

function accountReady() {
  return Boolean(
    state?.session?.authenticated && state?.session?.verified && state?.workspace?.completed,
  );
}

function routeForState() {
  if (!state?.account) return "/signup";
  if (!state.account.verified) return "/signup/verify";
  if (!state.workspace) return "/signup/workspace";
  if (!state.session.authenticated) return "/login";
  return "/a/dashboard";
}

async function refreshState() {
  state = await api("/api/bootstrap");
  return state;
}

function authBrand() {
  return `
    <aside class="auth-brand">
      <a class="brand-lockup" href="/" data-route>
        <img src="/static/assets/freshdesk-mark.svg" alt=""><span>freshdesk</span>
      </a>
      <div class="auth-message">
        <h1>Support customers from one clear inbox</h1>
        <p>Start a local Sprout workspace with the essential tools for organizing and resolving requests.</p>
        <ul class="feature-list">
          <li>${icon("check-circle")} Shared support inbox</li>
          <li>${icon("check-circle")} Assignment and priority controls</li>
          <li>${icon("check-circle")} Ticket history that persists</li>
        </ul>
      </div>
      <p class="local-note">Local replica. Account, email, workspace, and customer data remain on this device.</p>
    </aside>`;
}

function authShell(content) {
  app.innerHTML = `<div class="auth-shell">${authBrand()}<main class="auth-main">${content}</main></div>`;
  activateIcons();
}

function renderSignup() {
  const draft = state?.signupDraft || {};
  authShell(`
    <form id="signup-form" class="auth-form" novalidate>
      <h2>Start your Freshdesk account</h2>
      <p>Free Sprout workspace. No credit card required.</p>
      <div id="form-alert"></div>
      <div class="form-grid">
        <div class="field">
          <label class="required" for="full-name">Full name</label>
          <input id="full-name" name="full_name" autocomplete="name" value="${escapeHtml(draft.full_name || FIXTURE_NAME)}" required>
          <span class="field-error" data-error="full_name"></span>
        </div>
        <div class="field">
          <label class="required" for="email">Work email</label>
          <input id="email" name="email" type="email" autocomplete="email" value="${escapeHtml(draft.email || FIXTURE_EMAIL)}" required>
          <span class="field-error" data-error="email"></span>
        </div>
        <div class="field">
          <label class="required" for="password">Password</label>
          <input id="password" name="password" type="password" autocomplete="new-password" minlength="8" required>
          <span class="field-hint">At least 8 characters</span>
          <span class="field-error" data-error="password"></span>
        </div>
        <label class="checkbox-row">
          <input id="accepted-terms" name="accepted_terms" type="checkbox">
          <span>I agree to the local replica terms and privacy boundary.</span>
        </label>
        <span class="field-error" data-error="accepted_terms"></span>
        <button class="btn btn-primary btn-block" type="submit">Create account ${icon("arrow-right")}</button>
      </div>
      <p class="field-hint" style="margin-top:16px">Already registered in this browser? <a href="/login" data-route>Log in</a></p>
    </form>`);
  const form = document.querySelector("#signup-form");
  const saveSignupDraft = () => {
    window.clearTimeout(draftTimer);
    draftTimer = window.setTimeout(() => {
      api("/api/signup/draft", {
        method: "POST",
        body: JSON.stringify({
          full_name: form.full_name.value,
          email: form.email.value,
          workspace_name: draft.workspace_name || "",
          workspace_domain: draft.workspace_domain || "",
        }),
      }).catch(() => {});
    }, 250);
  };
  form.addEventListener("input", saveSignupDraft);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearErrors(form);
    const submit = form.querySelector("button[type=submit]");
    submit.disabled = true;
    try {
      await api("/api/auth/register", {
        method: "POST",
        body: JSON.stringify({
          full_name: form.full_name.value.trim(),
          email: form.email.value.trim(),
          password: form.password.value,
          accepted_terms: form.accepted_terms.checked,
        }),
      });
      await refreshState();
      navigate("/signup/verify");
    } catch (error) {
      showFormError(form, error);
    } finally {
      submit.disabled = false;
    }
  });
}

function renderVerify() {
  if (!state?.account) {
    navigate("/signup", true);
    return;
  }
  authShell(`
    <form id="verify-form" class="auth-form" novalidate>
      <h2>Verify your email</h2>
      <p>We prepared a local verification for <strong>${escapeHtml(state.account.email)}</strong>. No message was sent outside this replica.</p>
      <div class="notice info">${icon("mail-check")} <div>Local verification code: <strong>246810</strong></div></div>
      <div id="form-alert"></div>
      <div class="form-grid">
        <div class="field">
          <label class="required" for="verification-code">Verification code</label>
          <input class="verification-code" id="verification-code" name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" required>
          <span class="field-error" data-error="code"></span>
        </div>
        <button class="btn btn-primary btn-block" type="submit">Verify and continue ${icon("arrow-right")}</button>
      </div>
    </form>`);
  const form = document.querySelector("#verify-form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearErrors(form);
    try {
      await api("/api/auth/verify", {
        method: "POST",
        body: JSON.stringify({ code: form.code.value.trim() }),
      });
      await refreshState();
      navigate("/signup/workspace");
    } catch (error) {
      showFormError(form, error);
    }
  });
}

function renderWorkspace() {
  if (!state?.account?.verified) {
    navigate(routeForState(), true);
    return;
  }
  const signupDraft = state.signupDraft || {};
  authShell(`
    <form id="workspace-form" class="auth-form" novalidate>
      <h2>Create your workspace</h2>
      <p>This is where Alex Green and the local support team will manage tickets.</p>
      <div id="form-alert"></div>
      <div class="plan-summary">
        <div><strong>Sprout</strong><span>Essential ticketing for a small support team</span></div>
        <div class="plan-price">Free</div>
      </div>
      <div class="form-grid">
        <div class="field">
          <label class="required" for="workspace-name">Workspace name</label>
          <input id="workspace-name" name="name" value="${escapeHtml(signupDraft.workspace_name || "Pinecrest Support")}" required>
          <span class="field-error" data-error="name"></span>
        </div>
        <div class="field">
          <label class="required" for="workspace-domain">Freshdesk address</label>
          <div style="display:flex;align-items:center;gap:8px">
            <input id="workspace-domain" name="domain" value="${escapeHtml(signupDraft.workspace_domain || "pinecrest-support")}" required>
            <span class="field-hint">.freshdesk.local</span>
          </div>
          <span class="field-error" data-error="domain"></span>
        </div>
        <button class="btn btn-primary btn-block" type="submit">Launch Freshdesk ${icon("rocket")}</button>
      </div>
    </form>`);
  const form = document.querySelector("#workspace-form");
  const saveWorkspaceDraft = () => {
    window.clearTimeout(draftTimer);
    draftTimer = window.setTimeout(() => {
      api("/api/signup/draft", {
        method: "POST",
        body: JSON.stringify({
          full_name: state.account.full_name,
          email: state.account.email,
          workspace_name: form.name.value,
          workspace_domain: form.domain.value,
        }),
      }).catch(() => {});
    }, 250);
  };
  form.addEventListener("input", saveWorkspaceDraft);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearErrors(form);
    try {
      await api("/api/workspaces", {
        method: "POST",
        body: JSON.stringify({ name: form.name.value.trim(), domain: form.domain.value.trim(), plan: "Sprout" }),
      });
      await refreshState();
      navigate("/a/dashboard");
    } catch (error) {
      showFormError(form, error);
    }
  });
}

function renderLogin() {
  if (!state?.account) {
    authShell(`
      <div class="auth-form">
        <h2>No local account yet</h2>
        <p>Create the Sprout workspace first in this browser session.</p>
        <a class="btn btn-primary btn-block" href="/signup" data-route>Create account ${icon("arrow-right")}</a>
      </div>`);
    return;
  }
  authShell(`
    <form id="login-form" class="auth-form" novalidate>
      <h2>Log in to Freshdesk</h2>
      <p>Continue to ${escapeHtml(state.workspace?.name || "your local workspace")}.</p>
      <div id="form-alert"></div>
      <div class="form-grid">
        <div class="field">
          <label class="required" for="login-email">Email</label>
          <input id="login-email" name="email" type="email" autocomplete="email" value="${escapeHtml(state.account.email)}">
        </div>
        <div class="field">
          <label class="required" for="login-password">Password</label>
          <input id="login-password" name="password" type="password" autocomplete="current-password">
        </div>
        <button class="btn btn-primary btn-block" type="submit">Log in ${icon("log-in")}</button>
      </div>
      <p class="field-hint" style="margin-top:16px">Identity recovery stays within this browser and local database.</p>
    </form>`);
  const form = document.querySelector("#login-form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearErrors(form);
    try {
      await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email: form.email.value.trim(), password: form.password.value }),
      });
      await refreshState();
      navigate("/a/dashboard");
    } catch (error) {
      showFormError(form, error);
    }
  });
}

function clearErrors(form) {
  form.querySelectorAll("[data-error]").forEach((node) => {
    node.textContent = "";
  });
  const alert = form.querySelector("#form-alert");
  if (alert) alert.innerHTML = "";
}

function showFormError(form, error) {
  const fields = error?.payload?.fields || {};
  Object.entries(fields).forEach(([key, message]) => {
    const node = form.querySelector(`[data-error="${CSS.escape(key)}"]`);
    if (node) node.textContent = message;
  });
  const alert = form.querySelector("#form-alert");
  if (alert) {
    alert.innerHTML = `<div class="form-alert">${icon("circle-alert")}<span>${escapeHtml(error.message)}</span></div>`;
    activateIcons();
  }
}

function navItem(path, iconName, label, exact = false) {
  const active = exact ? currentPath() === path : currentPath().startsWith(path);
  return `<li><a class="nav-link ${active ? "active" : ""}" href="${path}" data-route title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}">${icon(iconName)}</a></li>`;
}

function appShell(content) {
  app.innerHTML = `
    <div class="app-shell">
      <header class="topbar">
        <div class="topbar-left">
          <a class="brand-lockup" href="/a/dashboard" data-route><img src="/static/assets/freshdesk-mark.svg" alt=""><span>freshdesk</span></a>
          <span class="workspace-title">${escapeHtml(state.workspace.name)} · Sprout</span>
        </div>
        <div class="topbar-actions">
          <button class="icon-btn help-action" data-boundary="integration" title="Help and support" aria-label="Help and support">${icon("circle-help")}</button>
          <a class="icon-btn" href="/a/tickets/new" data-route title="New ticket" aria-label="New ticket">${icon("plus")}</a>
          <button class="profile-button" id="logout-button" title="Log out"><span class="avatar">AG</span><span>Alex Green</span>${icon("log-out")}</button>
        </div>
      </header>
      <nav class="sidebar" aria-label="Main navigation">
        <ul class="nav-list">
          ${navItem("/a/dashboard", "layout-dashboard", "Dashboard", true)}
          ${navItem("/a/tickets", "inbox", "Tickets")}
          ${navItem("/a/team", "users", "Team", true)}
          ${navItem("/a/settings", "settings", "Settings")}
        </ul>
        <ul class="nav-list"><li><button class="nav-link" data-boundary="integration" title="Marketplace" aria-label="Marketplace">${icon("blocks")}</button></li></ul>
      </nav>
      <main class="app-main">${content}</main>
    </div>`;
  activateIcons();
  document.querySelector("#logout-button")?.addEventListener("click", async () => {
    await api("/api/auth/logout", { method: "POST", body: "{}" });
    await refreshState();
    navigate("/login");
  });
  document.querySelectorAll("[data-boundary]").forEach((button) => {
    button.addEventListener("click", async () => {
      await recordBoundary(button.dataset.boundary, "This destination is represented locally; no external service was opened.");
    });
  });
}

async function recordBoundary(kind, detail) {
  try {
    await api("/api/boundary", { method: "POST", body: JSON.stringify({ kind, detail }) });
    toast("Local-only boundary recorded. No external request was sent.");
  } catch (error) {
    toast(error.message);
  }
}

function statusLabel(status) {
  return { 2: "Open", 3: "Pending", 4: "Resolved", 5: "Closed" }[Number(status)] || "Unknown";
}

function priorityLabel(priority) {
  return { 1: "Low", 2: "Medium", 3: "High", 4: "Urgent" }[Number(priority)] || "Unknown";
}

function renderDashboard() {
  const tickets = state.tickets || [];
  const open = tickets.filter((ticket) => Number(ticket.status) === 2).length;
  const high = tickets.filter((ticket) => Number(ticket.priority) >= 3).length;
  appShell(`
    <section class="page">
      <div class="page-header">
        <div><h1>Good morning, Alex</h1><p>Here is what needs attention in ${escapeHtml(state.workspace.name)}.</p></div>
        <a class="btn btn-primary" href="/a/tickets/new" data-route>${icon("plus")} New ticket</a>
      </div>
      <div class="dashboard-grid">
        <article class="panel metric"><p>Open tickets</p><strong>${open}</strong></article>
        <article class="panel metric"><p>High priority</p><strong>${high}</strong></article>
        <article class="panel metric"><p>Due today</p><strong>${open}</strong></article>
        <article class="panel metric"><p>Resolved</p><strong>${tickets.filter((ticket) => Number(ticket.status) === 4).length}</strong></article>
      </div>
      <div class="dashboard-columns">
        <section class="panel">
          <div class="panel-header"><h2>Recent tickets</h2><a href="/a/tickets" data-route>View all</a></div>
          ${ticketListCompact(tickets.slice(0, 4))}
        </section>
        <section class="panel">
          <div class="panel-header"><h2>Get started</h2><span class="badge">Sprout</span></div>
          <div class="panel-body">
            <ul class="onboarding-list">
              <li><span class="done">${icon("check-circle")}</span><div><strong>Workspace created</strong><br><span class="field-hint">${escapeHtml(state.workspace.domain)}.freshdesk.local</span></div></li>
              <li><span class="done">${icon("check-circle")}</span><div><strong>Test Agent ready</strong><br><span class="field-hint">Local team fixture</span></div></li>
              <li>${icon("circle")}<div><a href="/a/tickets/new" data-route><strong>Create your first ticket</strong></a></div></li>
            </ul>
          </div>
        </section>
      </div>
    </section>`);
}

function ticketListCompact(tickets) {
  if (!tickets.length) {
    return `<div class="empty-state" style="min-height:250px">${icon("inbox")}<h2>No tickets yet</h2><p>New customer requests will appear here.</p><a class="btn btn-secondary" href="/a/tickets/new" data-route>Create ticket</a></div>`;
  }
  return `<div class="table-wrap"><table class="ticket-table"><thead><tr><th style="width:58%">Ticket</th><th>Status</th><th>Priority</th></tr></thead><tbody>${tickets.map(ticketRowCompact).join("")}</tbody></table></div>`;
}

function ticketRowCompact(ticket) {
  const description = ticket.description.length > 60 ? `${ticket.description.slice(0, 60)}...` : ticket.description;
  return `<tr><td class="subject-cell"><a href="/a/tickets/${ticket.id}" data-route>#${ticket.id} ${escapeHtml(ticket.subject)}</a><small>${escapeHtml(description)}</small></td><td><span class="badge ${Number(ticket.status) === 2 ? "open" : "resolved"}">${statusLabel(ticket.status)}</span></td><td><span class="badge ${Number(ticket.priority) === 3 ? "high" : ""}">${priorityLabel(ticket.priority)}</span></td></tr>`;
}

async function renderTickets() {
  const simulateError = new URLSearchParams(location.search).get("api_error") === "1";
  appShell(`
    <section class="page">
      <div class="page-header"><div><h1>Tickets</h1><p>Customer requests across your support workspace</p></div><a class="btn btn-primary" href="/a/tickets/new" data-route>${icon("plus")} New ticket</a></div>
      <div class="toolbar">
        <div class="search-wrap">${icon("search")}<input class="search-input" id="ticket-search" type="search" placeholder="Search tickets" aria-label="Search tickets"></div>
        <div class="segmented" aria-label="Ticket status filter"><button class="active" data-filter="all">All</button><button data-filter="open">Open</button><button data-filter="resolved">Resolved</button></div>
      </div>
      <section id="ticket-list" class="panel"><div class="loading-screen" style="min-height:330px">${icon("loader-circle")}<p>Loading tickets...</p></div></section>
    </section>`);
  try {
    const result = await api(`/api/_/tickets${simulateError ? "?simulate_error=1" : ""}`);
    state.tickets = result.tickets;
    drawTicketTable(result.tickets);
  } catch (error) {
    const list = document.querySelector("#ticket-list");
    list.innerHTML = `<div class="error-state">${icon("wifi-off")}<h2>Tickets could not be loaded</h2><p>${escapeHtml(error.message)}</p><button id="retry-list" class="btn btn-primary">${icon("refresh-cw")} Retry</button></div>`;
    activateIcons();
    document.querySelector("#retry-list").addEventListener("click", () => {
      history.replaceState({}, "", "/a/tickets");
      renderTickets();
    });
  }
}

function drawTicketTable(tickets) {
  const list = document.querySelector("#ticket-list");
  if (!tickets.length) {
    list.innerHTML = `<div class="empty-state">${icon("inbox")}<h2>Your inbox is clear</h2><p>Create a ticket to begin tracking a customer request.</p><a class="btn btn-primary" href="/a/tickets/new" data-route>${icon("plus")} New ticket</a></div>`;
    activateIcons();
    return;
  }
  list.innerHTML = `<div class="table-wrap"><table class="ticket-table"><thead><tr><th style="width:45%">Subject</th><th>Requester</th><th>Status</th><th>Priority</th><th>Agent</th></tr></thead><tbody id="ticket-table-body">${tickets.map(ticketRow).join("")}</tbody></table></div>`;
  activateIcons();
  let filter = "all";
  let query = "";
  const apply = () => {
    const filtered = tickets.filter((ticket) => {
      const statusMatch = filter === "all" || (filter === "open" ? Number(ticket.status) === 2 : Number(ticket.status) >= 4);
      return statusMatch && `${ticket.subject} ${ticket.description}`.toLowerCase().includes(query);
    });
    document.querySelector("#ticket-table-body").innerHTML = filtered.length ? filtered.map(ticketRow).join("") : `<tr><td colspan="5"><div class="empty-state" style="min-height:220px">${icon("search-x")}<h2>No matching tickets</h2><p>Clear the search or choose another status.</p></div></td></tr>`;
    activateIcons();
  };
  document.querySelector("#ticket-search").addEventListener("input", (event) => {
    query = event.target.value.trim().toLowerCase();
    apply();
  });
  document.querySelectorAll("[data-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-filter]").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      filter = button.dataset.filter;
      apply();
    });
  });
}

function ticketRow(ticket) {
  return `<tr><td class="subject-cell"><a href="/a/tickets/${ticket.id}" data-route>#${ticket.id} ${escapeHtml(ticket.subject)}</a><small>${escapeHtml(ticket.type)} · Phone</small></td><td>Alex Green</td><td><span class="badge ${Number(ticket.status) === 2 ? "open" : "resolved"}">${statusLabel(ticket.status)}</span></td><td><span class="badge ${Number(ticket.priority) === 3 ? "high" : ""}">${priorityLabel(ticket.priority)}</span></td><td>Test Agent</td></tr>`;
}

function ticketPayload(form) {
  return {
    requester_id: REQUESTER_ID,
    subject: form.subject.value.trim(),
    description: form.description.value.trim(),
    status: Number(form.status.value),
    priority: Number(form.priority.value),
    source: 3,
    group_id: Number(form.group_id.value),
    responder_id: Number(form.responder_id.value),
    type: form.type.value,
  };
}

function ticketFormHtml(draft) {
  return `
    <section class="panel form-panel">
      <form id="ticket-form" class="form-section" novalidate>
        <div id="form-alert"></div>
        <div class="field">
          <span class="required">Requester</span>
          <div class="requester-box">
            <div class="requester-info"><span class="avatar">AG</span><div><strong>Alex Green</strong><small>${FIXTURE_EMAIL} · Pinecrest Technologies Inc.</small></div></div>
            <button type="button" class="icon-btn" id="customer-boundary" title="Add requester" aria-label="Add requester">${icon("user-plus")}</button>
          </div>
          <span class="field-error" data-error="requester_id"></span>
        </div>
        <div class="field">
          <label class="required" for="ticket-subject">Subject</label>
          <input id="ticket-subject" name="subject" maxlength="255" value="${escapeHtml(draft.subject || "")}" required>
          <span class="field-error" data-error="subject"></span>
        </div>
        <div class="field">
          <label class="required" for="ticket-description">Description</label>
          <textarea id="ticket-description" name="description" maxlength="5000" required>${escapeHtml(draft.description || "")}</textarea>
          <div class="list-meta" style="justify-content:space-between"><span class="field-error" data-error="description"></span><span class="field-hint" id="description-count">${String(draft.description || "").length}/5000</span></div>
        </div>
        <div class="two-col">
          <div class="field"><label for="ticket-status">Status</label><select id="ticket-status" name="status"><option value="2" ${Number(draft.status) === 2 ? "selected" : ""}>Open</option><option value="3" ${Number(draft.status) === 3 ? "selected" : ""}>Pending</option></select></div>
          <div class="field"><label class="required" for="ticket-priority">Priority</label><select id="ticket-priority" name="priority"><option value="1" ${Number(draft.priority) === 1 ? "selected" : ""}>Low</option><option value="2" ${Number(draft.priority) === 2 ? "selected" : ""}>Medium</option><option value="3" ${Number(draft.priority) === 3 ? "selected" : ""}>High</option><option value="4" ${Number(draft.priority) === 4 ? "selected" : ""}>Urgent</option></select><span class="field-error" data-error="priority"></span></div>
        </div>
        <div class="two-col">
          <div class="field"><label class="required" for="ticket-type">Type</label><select id="ticket-type" name="type"><option value="">Select type</option><option value="Billing" ${draft.type === "Billing" ? "selected" : ""}>Billing</option><option value="Question" ${draft.type === "Question" ? "selected" : ""}>Question</option><option value="Problem" ${draft.type === "Problem" ? "selected" : ""}>Problem</option><option value="Feature Request" ${draft.type === "Feature Request" ? "selected" : ""}>Feature Request</option></select><span class="field-error" data-error="type"></span></div>
          <div class="field"><label class="required" for="ticket-group">Group</label><select id="ticket-group" name="group_id"><option value="3001">Support</option></select></div>
        </div>
        <div class="field"><label class="required" for="ticket-agent">Agent</label><select id="ticket-agent" name="responder_id"><option value="">Select an agent</option><option value="2002" ${Number(draft.responder_id) === 2002 ? "selected" : ""}>Test Agent</option></select><span class="field-error" data-error="responder_id"></span></div>
        <input name="source" type="hidden" value="3">
        <div class="form-footer">
          <span class="save-state" id="save-state">${draft.updated_at ? "Draft restored" : ""}</span>
          <div class="form-actions"><a class="btn btn-secondary" href="/a/tickets" data-route>Cancel</a><button class="btn btn-primary" type="submit">${icon("check")} Create ticket</button></div>
        </div>
      </form>
    </section>`;
}

function renderNewTicket() {
  appShell(`
    <section class="page">
      <div class="page-header"><div><h1>New ticket</h1><p>Create a request on behalf of a customer</p></div></div>
      <div class="ticket-form-layout">
        ${ticketFormHtml(state.ticketDraft || {})}
        <aside class="panel side-panel">
          <div class="side-section"><h3>Ticket source</h3><p>Phone</p><p class="field-hint">Agent-created ticket</p></div>
          <div class="side-section"><h3>Workspace</h3><p>${escapeHtml(state.workspace.name)}</p><p class="field-hint">Sprout · ${escapeHtml(state.workspace.domain)}.freshdesk.local</p></div>
          <div class="side-section local-boundary"><h3>Local-only customer data</h3><p>Requester, team assignment, and ticket content stay in this SQLite workspace.</p></div>
        </aside>
      </div>
    </section>`);
  const form = document.querySelector("#ticket-form");
  const saveState = document.querySelector("#save-state");
  const count = document.querySelector("#description-count");
  const saveDraft = () => {
    count.textContent = `${form.description.value.length}/5000`;
    saveState.textContent = "Saving draft...";
    window.clearTimeout(draftTimer);
    draftTimer = window.setTimeout(async () => {
      try {
        const result = await api("/api/ticket-draft", { method: "POST", body: JSON.stringify(ticketPayload(form)) });
        saveState.textContent = `Draft saved ${new Date(result.updated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
      } catch (_error) {
        saveState.textContent = "Draft not saved";
      }
    }, 280);
  };
  form.addEventListener("input", saveDraft);
  form.addEventListener("change", saveDraft);
  document.querySelector("#customer-boundary").addEventListener("click", () => recordBoundary("customer", "New requester creation is represented locally; no CRM or email service was contacted."));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    window.clearTimeout(draftTimer);
    clearErrors(form);
    const payload = ticketPayload(form);
    const localErrors = {};
    if (!payload.subject) localErrors.subject = "Subject is required";
    if (payload.description.length < 40) localErrors.description = "Add a meaningful description of at least 40 characters";
    if (!payload.type) localErrors.type = "Select a ticket type";
    if (!payload.responder_id) localErrors.responder_id = "Assign an agent";
    if (Object.keys(localErrors).length) {
      showFormError(form, new ApiError("Complete the required ticket fields", 422, { fields: localErrors }));
      return;
    }
    const submit = form.querySelector("button[type=submit]");
    submit.disabled = true;
    try {
      await api("/api/ticket-draft", { method: "POST", body: JSON.stringify(payload) });
      const result = await api("/api/_/tickets", { method: "POST", body: JSON.stringify(payload) });
      await refreshState();
      navigate(`/a/tickets/${result.id}`);
    } catch (error) {
      showFormError(form, error);
      if (error.status === 503) {
        const alert = form.querySelector("#form-alert .form-alert");
        if (alert) alert.insertAdjacentHTML("beforeend", `<button type="submit" class="btn btn-secondary">${icon("refresh-cw")} Retry</button>`);
        activateIcons();
      }
    } finally {
      submit.disabled = false;
    }
  });
}

async function renderTicketDetail(ticketId) {
  let ticket;
  try {
    ticket = (await api(`/api/_/tickets/${ticketId}`)).ticket;
  } catch (error) {
    if (error.status === 404) {
      renderNotFound("Ticket not found", "The ticket may have been removed or belongs to another local session.");
      return;
    }
    throw error;
  }
  const status = Number(ticket.status);
  appShell(`
    <section class="page">
      <div class="ticket-heading">
        <div><p class="ticket-id">Tickets / #${ticket.id}</p><h1>${escapeHtml(ticket.subject)}</h1></div>
        <div class="detail-actions"><a class="btn btn-secondary" href="/a/tickets/${ticket.id}/edit" data-route>${icon("pencil")} Edit</a>${status >= 4 ? `<button class="btn btn-primary" id="reopen-ticket">${icon("rotate-ccw")} Reopen</button>` : `<button class="btn btn-secondary" id="resolve-ticket">${icon("circle-check")} Resolve</button>`}</div>
      </div>
      <div class="ticket-detail-layout">
        <article class="panel conversation">
          <header class="conversation-header"><span class="avatar">AG</span><div><strong>Alex Green</strong><p>${FIXTURE_EMAIL} · created locally</p></div></header>
          <div class="message-body">${escapeHtml(ticket.description)}</div>
          <footer class="conversation-footer">${icon("phone")} Created by agent via Phone</footer>
        </article>
        <aside class="panel side-panel">
          <div class="panel-header"><h2>Properties</h2><span class="badge ${status === 2 ? "open" : "resolved"}">${statusLabel(status)}</span></div>
          <div class="panel-body"><dl class="property-list">
            <div><dt>Priority</dt><dd><span class="badge ${Number(ticket.priority) === 3 ? "high" : ""}">${priorityLabel(ticket.priority)}</span></dd></div>
            <div><dt>Type</dt><dd>${escapeHtml(ticket.type)}</dd></div>
            <div><dt>Group</dt><dd>Support</dd></div>
            <div><dt>Agent</dt><dd>Test Agent</dd></div>
            <div><dt>Requester</dt><dd>Alex Green</dd></div>
          </dl></div>
          <div class="side-section local-boundary"><h3>Customer context</h3><p>Pinecrest Technologies Inc.</p><p>${FIXTURE_EMAIL}</p></div>
        </aside>
      </div>
    </section>`);
  document.querySelector("#resolve-ticket")?.addEventListener("click", async () => {
    await api(`/api/_/tickets/${ticket.id}`, { method: "PATCH", body: JSON.stringify({ status: 4 }) });
    await refreshState();
    renderTicketDetail(ticket.id);
    toast("Ticket resolved");
  });
  document.querySelector("#reopen-ticket")?.addEventListener("click", async () => {
    await api(`/api/_/tickets/${ticket.id}/reopen`, { method: "POST", body: "{}" });
    await refreshState();
    renderTicketDetail(ticket.id);
    toast("Ticket reopened");
  });
}

async function renderTicketEdit(ticketId) {
  let ticket;
  try {
    ticket = (await api(`/api/_/tickets/${ticketId}`)).ticket;
  } catch (error) {
    renderNotFound("Ticket not found", error.message);
    return;
  }
  appShell(`
    <section class="page">
      <div class="page-header"><div><p class="ticket-id">Tickets / #${ticket.id}</p><h1>Edit ticket</h1></div></div>
      <section class="panel form-panel">
        <form id="edit-ticket-form" class="form-section" novalidate>
          <div id="form-alert"></div>
          <div class="field"><label class="required" for="edit-subject">Subject</label><input id="edit-subject" name="subject" value="${escapeHtml(ticket.subject)}"><span class="field-error" data-error="subject"></span></div>
          <div class="field"><label class="required" for="edit-description">Description</label><textarea id="edit-description" name="description">${escapeHtml(ticket.description)}</textarea><span class="field-error" data-error="description"></span></div>
          <div class="two-col">
            <div class="field"><label for="edit-status">Status</label><select id="edit-status" name="status"><option value="2" ${Number(ticket.status) === 2 ? "selected" : ""}>Open</option><option value="3" ${Number(ticket.status) === 3 ? "selected" : ""}>Pending</option><option value="4" ${Number(ticket.status) === 4 ? "selected" : ""}>Resolved</option><option value="5" ${Number(ticket.status) === 5 ? "selected" : ""}>Closed</option></select></div>
            <div class="field"><label for="edit-priority">Priority</label><select id="edit-priority" name="priority"><option value="1">Low</option><option value="2">Medium</option><option value="3" ${Number(ticket.priority) === 3 ? "selected" : ""}>High</option><option value="4">Urgent</option></select></div>
          </div>
          <div class="form-footer"><span class="save-state">Changes are journaled locally</span><div class="form-actions"><a class="btn btn-secondary" href="/a/tickets/${ticket.id}" data-route>Cancel</a><button class="btn btn-primary" type="submit">${icon("save")} Update</button></div></div>
        </form>
      </section>
    </section>`);
  const form = document.querySelector("#edit-ticket-form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearErrors(form);
    try {
      await api(`/api/_/tickets/${ticket.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          subject: form.subject.value.trim(),
          description: form.description.value.trim(),
          status: Number(form.status.value),
          priority: Number(form.priority.value),
        }),
      });
      await refreshState();
      navigate(`/a/tickets/${ticket.id}`);
    } catch (error) {
      showFormError(form, error);
    }
  });
}

function renderTeam() {
  appShell(`
    <section class="page">
      <div class="page-header"><div><h1>Team</h1><p>Agents in ${escapeHtml(state.workspace.name)}</p></div><button class="btn btn-primary" id="invite-agent">${icon("user-plus")} Invite agent</button></div>
      <section class="panel team-list">
        <div class="team-row"><div class="team-person"><span class="avatar">AG</span><div><strong>Alex Green</strong><div class="field-hint">${FIXTURE_EMAIL}</div></div></div><span class="badge">Account admin</span></div>
        <div class="team-row"><div class="team-person"><span class="avatar" style="background:#b9dcff;color:#173b62">TA</span><div><strong>Test Agent</strong><div class="field-hint">test-agent@freshdesk.local</div></div></div><span class="badge open">Available</span></div>
      </section>
      <div class="notice info" style="margin-top:16px">${icon("shield-check")} Team membership and invitations remain local. No email or external identity provider is contacted.</div>
    </section>`);
  document.querySelector("#invite-agent").addEventListener("click", () => recordBoundary("team", "Agent invitation simulated locally; no email invitation was sent."));
}

function renderSettings() {
  appShell(`
    <section class="page">
      <div class="page-header"><div><h1>Apps and integrations</h1><p>Connect tools to your support workflow</p></div></div>
      <section class="panel integration-grid">
        ${integrationRow("Slack", "Team notifications", "message-square")}
        ${integrationRow("Google Workspace", "Identity and calendar", "calendar-days")}
        ${integrationRow("Stripe", "Billing context", "credit-card")}
      </section>
      <div class="notice info" style="margin-top:16px">${icon("shield-check")} Integration controls are local boundaries. This replica never sends credentials or data to third-party services.</div>
    </section>`);
  document.querySelectorAll("[data-connect]").forEach((button) => {
    button.addEventListener("click", () => recordBoundary("integration", `${button.dataset.connect} connection represented locally; no OAuth or external request occurred.`));
  });
}

function integrationRow(name, description, iconName) {
  return `<div class="integration-row"><div class="team-person"><span class="avatar" style="border-radius:5px;background:#edf3f7;color:#456173">${icon(iconName)}</span><div><strong>${name}</strong><div class="field-hint">${description}</div></div></div><button class="btn btn-secondary" data-connect="${name}">Connect</button></div>`;
}

function renderNotFound(title = "Page not found", message = "The page you requested does not exist in this workspace.") {
  if (accountReady()) {
    appShell(`<section class="page"><div class="panel not-found">${icon("file-question")}<h1>${escapeHtml(title)}</h1><p>${escapeHtml(message)}</p><a class="btn btn-primary" href="/a/dashboard" data-route>${icon("house")} Back to dashboard</a></div></section>`);
  } else {
    authShell(`<div class="auth-form not-found" style="min-height:400px">${icon("file-question")}<h1>${escapeHtml(title)}</h1><p>${escapeHtml(message)}</p><a class="btn btn-primary" href="${routeForState()}" data-route>Continue</a></div>`);
  }
}

async function renderRoute() {
  window.clearTimeout(draftTimer);
  if (!state) {
    try {
      await refreshState();
    } catch (error) {
      app.innerHTML = `<main class="loading-screen"><h1>Freshdesk is unavailable</h1><p>${escapeHtml(error.message)}</p><button class="btn btn-primary" onclick="location.reload()">Retry</button></main>`;
      return;
    }
  }
  const path = currentPath();
  const publicRoutes = new Set(["/", "/signup", "/signup/verify", "/signup/workspace", "/login"]);
  if (path.startsWith("/a/") && !accountReady()) {
    navigate(routeForState(), true);
    return;
  }
  if (path === "/" || path === "/signup") {
    if (accountReady()) navigate("/a/dashboard", true);
    else if (state.account) navigate(routeForState(), true);
    else renderSignup();
  } else if (path === "/signup/verify") {
    renderVerify();
  } else if (path === "/signup/workspace") {
    if (state.workspace) navigate(routeForState(), true);
    else renderWorkspace();
  } else if (path === "/login") {
    if (accountReady()) navigate("/a/dashboard", true);
    else renderLogin();
  } else if (path === "/a/dashboard") {
    renderDashboard();
  } else if (path === "/a/tickets") {
    await renderTickets();
  } else if (path === "/a/tickets/new") {
    renderNewTicket();
  } else if (/^\/a\/tickets\/\d+$/.test(path)) {
    await renderTicketDetail(Number(path.split("/").pop()));
  } else if (/^\/a\/tickets\/\d+\/edit$/.test(path)) {
    await renderTicketEdit(Number(path.split("/")[3]));
  } else if (path === "/a/team") {
    renderTeam();
  } else if (path.startsWith("/a/settings")) {
    renderSettings();
  } else if (!publicRoutes.has(path)) {
    renderNotFound();
  }
  document.title = `${document.querySelector("h1, h2")?.textContent || "Freshdesk"} | Freshdesk`;
}

document.addEventListener("click", (event) => {
  const link = event.target.closest("a[data-route]");
  if (!link || event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey) return;
  const target = new URL(link.href, location.href);
  if (target.origin !== location.origin) return;
  event.preventDefault();
  navigate(`${target.pathname}${target.search}`);
});

window.addEventListener("popstate", renderRoute);
renderRoute();

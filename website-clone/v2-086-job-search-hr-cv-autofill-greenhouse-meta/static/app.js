"use strict";

const app = document.querySelector("#app");
const toastRegion = document.querySelector("#toast-region");
const JOB_ID = "4526154007";
const JOB_PATH = `/codepath/jobs/${JOB_ID}`;
const TERMINAL_PATH = `/v1/boards/codepath/jobs/${JOB_ID}`;

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
    payload = { error: "The local service returned an unreadable response." };
  }
  if (!response.ok) {
    throw new ApiError(payload.error || `Request failed (${response.status}).`, response.status, payload);
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

function navigate(path, replace = false) {
  if (replace) history.replaceState({}, "", path);
  else history.pushState({}, "", path);
  window.scrollTo(0, 0);
  renderRoute();
}

function toast(message) {
  const node = document.createElement("div");
  node.className = "toast";
  node.textContent = message;
  toastRegion.append(node);
  window.setTimeout(() => node.remove(), 3200);
}

function header(active = "jobs") {
  return `
    <header class="site-header">
      <div class="header-inner">
        <a class="brand" href="/codepath" data-route><img src="/static/assets/codepath-mark.svg" alt="CodePath"></a>
        <nav aria-label="Primary navigation">
          <a class="${active === "company" ? "active" : ""}" href="/company" data-route>About CodePath</a>
          <a class="${active === "jobs" ? "active" : ""}" href="/codepath" data-route>Open roles</a>
          <a class="${active === "status" ? "active" : ""}" href="/my-application" data-route>Application status</a>
        </nav>
      </div>
    </header>`;
}

function footer() {
  return `
    <footer class="site-footer">
      <div><strong>CodePath</strong><span>Technology careers with measurable student impact.</span></div>
      <div class="footer-links"><a href="/privacy" data-route>Privacy</a><span>Powered by Greenhouse</span></div>
    </footer>`;
}

function shell(content, active = "jobs") {
  app.innerHTML = `${header(active)}${content}${footer()}`;
  bindRoutes();
}

function bindRoutes() {
  document.querySelectorAll("[data-route]").forEach((link) => {
    link.addEventListener("click", (event) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey) return;
      event.preventDefault();
      navigate(link.getAttribute("href"));
    });
  });
}

function renderCompany() {
  shell(`
    <main>
      <section class="company-hero">
        <div class="company-hero-inner">
          <p class="eyebrow">Careers at CodePath</p>
          <h1>Help build the most diverse generation of software engineers</h1>
          <p>CodePath transforms college computer science education through industry-vetted courses, career support, and a community built for student success.</p>
          <a class="button primary" href="/codepath" data-route>Explore open roles</a>
        </div>
      </section>
      <section class="company-band">
        <div class="company-copy"><h2>Build technology that opens doors</h2><p>Our product and engineering teams create learning experiences used by students at universities across the country. We work remotely, ship thoughtfully, and measure our work by learner outcomes.</p></div>
        <div class="impact-grid" aria-label="CodePath impact">
          <div><strong>40,000+</strong><span>students trained</span></div>
          <div><strong>1,000+</strong><span>universities represented</span></div>
          <div><strong>Remote</strong><span>distributed collaboration</span></div>
        </div>
      </section>
    </main>`, "company");
  bindRoutes();
}

function renderBoard() {
  const application = state.application;
  shell(`
    <main class="board-main">
      <section class="board-heading">
        <img src="/static/assets/codepath-mark.svg" alt="CodePath">
        <div><p class="eyebrow">Current openings</p><h1>Jobs at CodePath</h1><p>Join a remote team helping students turn technical skill into economic opportunity.</p></div>
      </section>
      <div class="board-toolbar">
        <div class="search-box"><span aria-hidden="true">⌕</span><input id="job-search" type="search" placeholder="Search jobs" aria-label="Search jobs"></div>
        <button id="job-alert" class="button secondary" type="button">Create job alert</button>
      </div>
      <section class="openings" aria-labelledby="openings-title">
        <div class="section-title"><h2 id="openings-title">Open positions</h2><span id="job-count">4 roles</span></div>
        <div id="job-list">
          ${jobGroup("Engineering", [
            jobRow(JOB_ID, "Senior Software Engineer", state.job.location, application ? "Applied" : ""),
            jobRow("4402039007", "Staff Software Engineer", state.job.location),
          ])}
          ${jobGroup("Product", [jobRow("4410991007", "Senior Product Manager, Learner Experience", "Remote, United States")])}
          ${jobGroup("General", [jobRow("4398102007", "General Application", "Remote, United States")])}
        </div>
        <div id="no-results" class="empty-state" hidden><h3>No matching roles</h3><p>Try a different title or department.</p></div>
      </section>
    </main>`);

  const input = document.querySelector("#job-search");
  input.addEventListener("input", () => {
    const query = input.value.trim().toLowerCase();
    const rows = [...document.querySelectorAll(".job-row")];
    let visible = 0;
    rows.forEach((row) => {
      const match = row.textContent.toLowerCase().includes(query);
      row.hidden = !match;
      if (match) visible += 1;
    });
    document.querySelectorAll(".job-group").forEach((group) => {
      group.hidden = ![...group.querySelectorAll(".job-row")].some((row) => !row.hidden);
    });
    document.querySelector("#no-results").hidden = visible !== 0;
    document.querySelector("#job-count").textContent = `${visible} ${visible === 1 ? "role" : "roles"}`;
  });
  document.querySelector("#job-alert").addEventListener("click", () => localBoundary("job_alert", "Job alerts stay disabled in this local replica."));
}

function jobGroup(title, rows) {
  return `<div class="job-group"><h3>${escapeHtml(title)}</h3>${rows.join("")}</div>`;
}

function jobRow(id, title, location, badge = "") {
  const active = id === JOB_ID;
  const href = active ? JOB_PATH : `/codepath/jobs/${id}`;
  return `<a class="job-row" href="${href}" ${active ? "data-route" : ""}>
    <span><strong>${escapeHtml(title)}</strong><small>${escapeHtml(location)}</small></span>
    ${badge ? `<em>${badge}</em>` : ""}<b aria-hidden="true">›</b>
  </a>`;
}

function renderListing() {
  const applied = state.application;
  shell(`
    <main class="job-main">
      <nav class="breadcrumbs" aria-label="Breadcrumb"><a href="/codepath" data-route>CodePath jobs</a><span>/</span><span>Engineering</span></nav>
      <header class="job-hero">
        <div><p class="eyebrow">Engineering · ${escapeHtml(state.job.type)}</p><h1>${escapeHtml(state.job.title)}</h1><p class="job-location">${escapeHtml(state.job.location)}</p></div>
        ${applied ? `<a class="button secondary" href="/my-application" data-route>View application</a>` : `<a id="hero-apply" class="button primary" href="${JOB_PATH}/apply" data-route>Apply for this job</a>`}
      </header>
      <div class="job-layout">
        <article class="job-description">
          <p class="lead">CodePath is reprogramming higher education to create the most diverse generation of engineers, CTOs, and technology founders.</p>
          <h2>About the role</h2>
          <p>As a Senior Software Engineer, you will own meaningful parts of the platform that supports students, instructors, and university partners. You will turn ambiguous learner needs into reliable product systems and help the engineering team raise its technical bar.</p>
          <p>This role is remote and works closely with Product, Curriculum, and Programs. The team values pragmatic delivery, clear communication, and systems that remain understandable as they scale.</p>
          <h2>What you'll do</h2>
          <ul>
            <li>Design, build, and operate full-stack features for CodePath's learning platform.</li>
            <li>Shape architecture for reliable APIs, data workflows, and student-facing experiences.</li>
            <li>Partner with product and design to move from discovery through measurable outcomes.</li>
            <li>Mentor engineers through technical planning, pairing, and code review.</li>
            <li>Improve observability, testing, delivery practices, and platform reliability.</li>
          </ul>
          <h2>What we're looking for</h2>
          <ul>
            <li>Significant professional software engineering experience across backend and web systems.</li>
            <li>Experience with distributed systems, production APIs, cloud infrastructure, and relational data.</li>
            <li>Strong technical judgment and a record of leading projects across teams.</li>
            <li>Care for CodePath's mission and for building equitable pathways into technology.</li>
          </ul>
          <h2>Working at CodePath</h2>
          <p>CodePath is an equal opportunity employer. We evaluate applicants based on the strengths they bring to the work and welcome people with varied backgrounds and paths into technology.</p>
          <div class="job-meta-panel"><div><span>Workplace</span><strong>Remote</strong></div><div><span>Employment type</span><strong>Full-Time</strong></div><div><span>Job ID</span><strong>${JOB_ID}</strong></div></div>
        </article>
        <aside class="apply-aside">
          <div class="apply-panel"><h2>Interested?</h2><p>Use the assigned resume to prepare your application.</p>
          ${applied ? `<a class="button secondary full" href="/my-application" data-route>Application submitted</a>` : `<a class="button primary full" href="${JOB_PATH}/apply" data-route>Apply for this job</a>`}
          <small>Your application remains local in this benchmark replica.</small></div>
        </aside>
      </div>
    </main>`);
  bindRoutes();
}

function initialApplication() {
  return {
    ...state.profile,
    authorized_to_work: true,
    requires_sponsorship: false,
    future_opportunities: true,
    consent: false,
    ...(state.draft?.application || {}),
  };
}

function renderApply() {
  if (state.application) {
    navigate("/my-application", true);
    return;
  }
  const data = initialApplication();
  shell(`
    <main class="application-main">
      <div class="application-heading"><a href="${JOB_PATH}" data-route>← Back to job</a><p class="eyebrow">Application for</p><h1>${escapeHtml(state.job.title)}</h1><p>${escapeHtml(state.job.location)}</p></div>
      <div class="progress" aria-label="Application progress"><div class="complete"><b>1</b><span>Profile</span></div><i></i><div class="current"><b>2</b><span>Application</span></div><i></i><div><b>3</b><span>Review</span></div></div>
      ${state.draft ? `<div class="notice success" id="draft-restored"><strong>Draft restored</strong><span>Your saved application from this browser is ready to continue.</span></div>` : ""}
      <form id="application-form" class="application-form" novalidate>
        <section class="form-section resume-section">
          <div class="section-heading"><div><h2>Resume/CV</h2><p>Required</p></div><span class="status-badge">Parsed</span></div>
          <div class="resume-file"><div class="file-icon">PDF</div><div><strong>${escapeHtml(data.resume.file_name)}</strong><span>Assigned profile · extracted successfully</span></div><a href="/documents/alex-green-resume" target="_blank" rel="noopener">Preview</a></div>
          <div class="extracted-summary"><span>12 skills found</span><span>3 roles found</span><span>3 education entries found</span></div>
        </section>
        <section class="form-section">
          <div class="section-heading"><div><h2>Personal information</h2><p><span class="required-mark">*</span> indicates a required field</p></div></div>
          <div id="form-alert"></div>
          <div class="form-grid two-col">
            ${field("first_name", "First name", data.first_name, true)}
            ${field("last_name", "Last name", data.last_name, true)}
            ${field("preferred_name", "Preferred first name", data.preferred_name)}
            ${field("email", "Email", data.email, true, "email")}
            ${selectField("country", "Country", ["Canada", "United States", "United Kingdom", "Other"], data.country, true)}
            ${field("location", "Location (City)", data.location, true)}
          </div>
        </section>
        <section class="form-section">
          <div class="section-heading"><div><h2>Resume details</h2><p>Review the fields extracted from your assigned resume.</p></div><span class="status-badge muted">Autofilled</span></div>
          <div class="form-grid two-col">
            ${field("current_title", "Current title", data.current_title, true)}
            ${field("current_company", "Current company", data.current_company, true)}
            ${field("highest_degree", "Highest degree", data.highest_degree, true)}
            ${selectField("years_experience", "Software engineering experience", ["0-2", "3-5", "6-9", "10-15", "16-22", "23+"], data.years_experience, true)}
          </div>
        </section>
        <section class="form-section">
          <div class="section-heading"><div><h2>Application questions</h2><p>Please answer every required question.</p></div></div>
          <fieldset class="question-field"><legend>Are you legally authorized to work in Canada? <span class="required-mark">*</span></legend>
            ${radio("authorized_to_work", "true", "Yes", data.authorized_to_work === true)}${radio("authorized_to_work", "false", "No", data.authorized_to_work === false)}<span class="field-error" data-error="authorized_to_work"></span></fieldset>
          <fieldset class="question-field"><legend>Do you now or will you in the future require visa sponsorship for work in Canada? <span class="required-mark">*</span></legend>
            ${radio("requires_sponsorship", "true", "Yes", data.requires_sponsorship === true)}${radio("requires_sponsorship", "false", "No", data.requires_sponsorship === false)}<span class="field-error" data-error="requires_sponsorship"></span></fieldset>
          <label class="checkbox-row"><input type="checkbox" name="future_opportunities" ${data.future_opportunities ? "checked" : ""}><span>Keep my information for future CodePath opportunities.</span></label>
        </section>
        <div class="form-actions"><span id="save-status">Changes are saved in this browser</span><button class="button primary" type="submit">Save and review</button></div>
      </form>
    </main>`);
  bindApplicationForm();
}

function field(name, label, value, required = false, type = "text") {
  return `<div class="field"><label for="${name}">${escapeHtml(label)}${required ? ` <span class="required-mark">*</span>` : ""}</label><input id="${name}" name="${name}" type="${type}" value="${escapeHtml(value)}" ${required ? "required" : ""}><span class="field-error" data-error="${name}"></span></div>`;
}

function selectField(name, label, options, value, required = false) {
  return `<div class="field"><label for="${name}">${escapeHtml(label)}${required ? ` <span class="required-mark">*</span>` : ""}</label><select id="${name}" name="${name}" ${required ? "required" : ""}>${options.map((option) => `<option value="${escapeHtml(option)}" ${option === value ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}</select><span class="field-error" data-error="${name}"></span></div>`;
}

function radio(name, value, label, checked) {
  return `<label class="radio-row"><input type="radio" name="${name}" value="${value}" ${checked ? "checked" : ""}><span>${label}</span></label>`;
}

function collectApplication(form) {
  const yesNo = (name) => form.querySelector(`[name="${name}"]:checked`)?.value === "true";
  return {
    first_name: form.first_name.value.trim(),
    last_name: form.last_name.value.trim(),
    preferred_name: form.preferred_name.value.trim(),
    email: form.email.value.trim(),
    country: form.country.value,
    location: form.location.value.trim(),
    resume: state.profile.resume,
    current_company: form.current_company.value.trim(),
    current_title: form.current_title.value.trim(),
    highest_degree: form.highest_degree.value.trim(),
    years_experience: form.years_experience.value,
    authorized_to_work: yesNo("authorized_to_work"),
    requires_sponsorship: yesNo("requires_sponsorship"),
    future_opportunities: form.future_opportunities.checked,
    consent: false,
  };
}

async function saveDraft(application, step) {
  return api(`/api/drafts/${JOB_ID}`, {
    method: "POST",
    body: JSON.stringify({ job_id: JOB_ID, step, application }),
  });
}

function bindApplicationForm() {
  bindRoutes();
  const form = document.querySelector("#application-form");
  const schedule = () => {
    window.clearTimeout(draftTimer);
    const status = document.querySelector("#save-status");
    status.textContent = "Saving...";
    draftTimer = window.setTimeout(async () => {
      try {
        await saveDraft(collectApplication(form), 2);
        status.textContent = "Saved just now";
        await refreshState();
      } catch (_error) {
        status.textContent = "Complete required fields to save";
      }
    }, 350);
  };
  form.addEventListener("input", schedule);
  form.addEventListener("change", schedule);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    window.clearTimeout(draftTimer);
    clearErrors(form);
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      await saveDraft(collectApplication(form), 3);
      await refreshState();
      navigate(`${JOB_PATH}/review`);
    } catch (error) {
      showFormError(form, error);
      document.querySelector("#form-alert")?.scrollIntoView({ behavior: "smooth", block: "center" });
    } finally {
      button.disabled = false;
    }
  });
}

function clearErrors(form) {
  form.querySelectorAll(".field-error").forEach((node) => { node.textContent = ""; });
  const alert = form.querySelector("#form-alert");
  if (alert) alert.innerHTML = "";
}

function showFormError(form, error) {
  const alert = form.querySelector("#form-alert");
  if (alert) alert.innerHTML = `<div class="notice error"><strong>Check your application</strong><span>${escapeHtml(error.message)}</span></div>`;
  Object.entries(error.payload?.fields || {}).forEach(([name, message]) => {
    const target = form.querySelector(`[data-error="${CSS.escape(name)}"]`);
    if (target) target.textContent = typeof message === "string" ? message : "Check this field.";
  });
}

function reviewRow(label, value) {
  return `<div class="review-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function renderReview() {
  if (state.application) {
    navigate(`${JOB_PATH}/confirmation`, true);
    return;
  }
  if (!state.draft || state.draft.step < 3) {
    navigate(`${JOB_PATH}/apply`, true);
    return;
  }
  const data = state.draft.application;
  shell(`
    <main class="application-main review-main">
      <div class="application-heading"><a href="${JOB_PATH}/apply" data-route>← Edit application</a><p class="eyebrow">Application for</p><h1>${escapeHtml(state.job.title)}</h1><p>${escapeHtml(state.job.location)}</p></div>
      <div class="progress" aria-label="Application progress"><div class="complete"><b>✓</b><span>Profile</span></div><i></i><div class="complete"><b>✓</b><span>Application</span></div><i></i><div class="current"><b>3</b><span>Review</span></div></div>
      <div id="submit-alert"></div>
      <section class="review-section"><div class="section-heading"><h2>Personal information</h2><a href="${JOB_PATH}/apply" data-route>Edit</a></div>
        ${reviewRow("Name", `${data.first_name} ${data.last_name}`)}${reviewRow("Preferred name", data.preferred_name)}${reviewRow("Email", data.email)}${reviewRow("Location", `${data.location}, ${data.country}`)}</section>
      <section class="review-section"><div class="section-heading"><h2>Resume/CV</h2><a href="${JOB_PATH}/apply" data-route>Edit</a></div>
        <div class="resume-file compact"><div class="file-icon">PDF</div><div><strong>${escapeHtml(data.resume.file_name)}</strong><span>Assigned profile · parsed</span></div><a href="/documents/alex-green-resume" target="_blank" rel="noopener">Preview</a></div>
        ${reviewRow("Current role", `${data.current_title} at ${data.current_company}`)}${reviewRow("Experience", `${data.years_experience} years`)}${reviewRow("Education", data.highest_degree)}</section>
      <section class="review-section"><div class="section-heading"><h2>Application questions</h2><a href="${JOB_PATH}/apply" data-route>Edit</a></div>
        ${reviewRow("Authorized to work in Canada", data.authorized_to_work ? "Yes" : "No")}${reviewRow("Requires sponsorship", data.requires_sponsorship ? "Yes" : "No")}${reviewRow("Future opportunities", data.future_opportunities ? "Yes" : "No")}</section>
      <section class="submit-section">
        <label class="checkbox-row consent"><input id="consent" type="checkbox"><span>I certify that the information in this application is accurate and consent to its use for this local CodePath application. <b>*</b></span></label>
        <span id="consent-error" class="field-error"></span>
        <p>Submitting creates only a local benchmark record. No employer, email, identity, or Greenhouse service is contacted.</p>
        <button id="submit-application" class="button primary full" type="button">Submit application</button>
      </section>
    </main>`);
  bindRoutes();
  document.querySelector("#submit-application").addEventListener("click", submitApplication);
}

async function submitApplication() {
  const consent = document.querySelector("#consent");
  const errorNode = document.querySelector("#consent-error");
  const alert = document.querySelector("#submit-alert");
  if (!consent.checked) {
    errorNode.textContent = "Confirm the application certification before submitting.";
    consent.focus();
    return;
  }
  errorNode.textContent = "";
  const button = document.querySelector("#submit-application");
  button.disabled = true;
  alert.innerHTML = "";
  const payload = { ...state.draft.application, consent: true };
  try {
    await saveDraft(payload, 3);
    await api(TERMINAL_PATH, { method: "POST", body: JSON.stringify(payload) });
    await refreshState();
    navigate(`${JOB_PATH}/confirmation`);
  } catch (error) {
    alert.innerHTML = `<div class="notice error"><strong>We couldn't submit your application</strong><span>${escapeHtml(error.message)}</span><button id="retry-submit" class="button secondary" type="button">Retry submission</button></div>`;
    document.querySelector("#retry-submit")?.addEventListener("click", submitApplication);
  } finally {
    button.disabled = false;
  }
}

function renderConfirmation() {
  if (!state.application) {
    navigate(`${JOB_PATH}/apply`, true);
    return;
  }
  const application = state.application;
  shell(`
    <main class="confirmation-main">
      <div class="success-mark" aria-hidden="true">✓</div>
      <p class="eyebrow">Application received locally</p>
      <h1>Thank you for applying, Alex</h1>
      <p>Your application for <strong>${escapeHtml(state.job.title)}</strong> at CodePath has been recorded in this local replica.</p>
      <div class="confirmation-card"><div><span>Status</span><strong>Submitted</strong></div><div><span>Confirmation</span><strong>${escapeHtml(application.confirmationCode)}</strong></div><div><span>Applicant</span><strong>${escapeHtml(application.application.email)}</strong></div></div>
      <div class="notice info"><strong>No external delivery</strong><span>This confirmation represents a local SQLite record only. No real application or email was sent.</span></div>
      <div class="confirmation-actions"><a class="button primary" href="/my-application" data-route>View application status</a><a class="button secondary" href="/codepath" data-route>Return to job board</a></div>
    </main>`);
  bindRoutes();
}

function renderStatus() {
  if (!state.application) {
    shell(`<main class="status-main"><div class="empty-state large"><div class="empty-icon">□</div><h1>No submitted application yet</h1><p>Open the assigned CodePath role to start or continue your application.</p><a class="button primary" href="${JOB_PATH}" data-route>Open Senior Software Engineer</a></div></main>`, "status");
    bindRoutes();
    return;
  }
  const item = state.application;
  shell(`<main class="status-main"><div class="status-heading"><p class="eyebrow">Application status</p><h1>Your CodePath application</h1></div>
    <section class="status-card"><div class="status-card-head"><div><h2>${escapeHtml(state.job.title)}</h2><p>CodePath · ${escapeHtml(state.job.location)}</p></div><span class="status-badge">Submitted</span></div>
    <div class="timeline"><div class="done"><b>✓</b><span><strong>Application submitted</strong><small>${new Date(item.createdAt).toLocaleString()}</small></span></div><div><b>2</b><span><strong>Recruiting review</strong><small>No external recruiting workflow is run by this replica.</small></span></div></div>
    <div class="status-details">${reviewRow("Confirmation", item.confirmationCode)}${reviewRow("Resume", item.application.resume.file_name)}${reviewRow("Email", item.application.email)}</div>
    <a class="button secondary" href="${JOB_PATH}" data-route>View job details</a></section></main>`, "status");
  bindRoutes();
}

function renderPrivacy() {
  shell(`<main class="text-page"><p class="eyebrow">Local privacy boundary</p><h1>Applicant privacy</h1><p>This task-scoped replica stores drafts, application state, and request evidence in a local SQLite database. It does not transmit resume, identity, demographic, email, or employer data to Greenhouse, CodePath, or any third party.</p><h2>What is represented</h2><p>The assigned Alex Green resume is rendered from benchmark profile data so the extraction and review flow is visible and judgeable. It is not uploaded anywhere.</p><a class="button secondary" href="${JOB_PATH}" data-route>Return to job</a></main>`);
  bindRoutes();
}

async function localBoundary(kind, detail) {
  await api("/api/boundary", { method: "POST", body: JSON.stringify({ kind, detail }) });
  shell(`<main class="boundary-main"><img src="/static/assets/codepath-mark.svg" alt="CodePath"><p class="eyebrow">Local-only boundary</p><h1>This service is not contacted</h1><p>${escapeHtml(detail)}</p><p>The action was recorded locally with no external effect.</p><a class="button primary" href="/codepath" data-route>Return to current openings</a></main>`);
  bindRoutes();
}

async function refreshState() {
  state = await api("/api/bootstrap");
  return state;
}

async function renderRoute() {
  const path = window.location.pathname.replace(/\/$/, "") || "/";
  if (!state) {
    try {
      await refreshState();
    } catch (error) {
      app.innerHTML = `<main class="error-page"><img src="/static/assets/codepath-mark.svg" alt="CodePath"><h1>Unable to load jobs</h1><p>${escapeHtml(error.message)}</p><button id="retry-load" class="button primary">Retry</button></main>`;
      document.querySelector("#retry-load").addEventListener("click", () => { state = null; renderRoute(); });
      return;
    }
  }
  if (path === "/" || path === "/company") renderCompany();
  else if (path === "/codepath") renderBoard();
  else if (path === JOB_PATH) {
    const detail = await api(`/api/boards/codepath/jobs/${JOB_ID}`);
    state.job = detail.job;
    renderListing();
  }
  else if (path === `${JOB_PATH}/apply`) renderApply();
  else if (path === `${JOB_PATH}/review`) renderReview();
  else if (path === `${JOB_PATH}/confirmation`) renderConfirmation();
  else if (path === "/my-application") renderStatus();
  else if (path === "/privacy") renderPrivacy();
  else if (path === "/local-boundary") localBoundary("employer", "Employer and MyGreenhouse services remain outside this local replica.");
  else navigate("/codepath", true);
}

window.addEventListener("popstate", renderRoute);
document.addEventListener("DOMContentLoaded", renderRoute);

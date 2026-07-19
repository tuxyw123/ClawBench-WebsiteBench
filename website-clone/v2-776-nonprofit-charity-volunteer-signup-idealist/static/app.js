"use strict";

const main = document.querySelector("#main");
const accountNav = document.querySelector("#account-nav");
const toastRegion = document.querySelector("#toast-region");

const state = {
  bootstrap: null,
  search: null,
  selectedJob: null,
  pendingPath: "/application/dumbarton-arts-education-program-manager-washington-dc",
  lastPassword: "",
  reviewPayload: null,
};

const icons = {
  search: "⌕",
  location: "●",
  bookmark: "☆",
  arrow: "→",
  check: "✓",
  person: "●",
  file: "▤",
  briefcase: "▣",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function pathOnly() {
  return window.location.pathname;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: options.body && !(options.body instanceof URLSearchParams)
      ? { "Content-Type": "application/json", ...(options.headers || {}) }
      : options.headers,
    ...options,
    body: options.body && !(options.body instanceof URLSearchParams) && typeof options.body !== "string"
      ? JSON.stringify(options.body)
      : options.body,
  });
  const payload = await response.json().catch(() => ({ error: "The local service returned an unreadable response." }));
  if (!response.ok) {
    const error = new Error(payload.error || `Request failed (${response.status})`);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function navigate(path, { replace = false } = {}) {
  if (replace) history.replaceState({}, "", path);
  else history.pushState({}, "", path);
  closeMenu();
  renderRoute();
  window.scrollTo({ top: 0, behavior: "instant" });
}

function toast(message, tone = "success") {
  const item = document.createElement("div");
  item.className = `toast ${tone}`;
  item.textContent = message;
  toastRegion.append(item);
  setTimeout(() => item.remove(), 3800);
}

function setBusy(button, busy, label = "Working...") {
  if (!button) return;
  if (busy) {
    button.dataset.original = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `<span class="button-spinner"></span>${escapeHtml(label)}`;
  } else {
    button.disabled = false;
    if (button.dataset.original) button.innerHTML = button.dataset.original;
  }
}

function closeMenu() {
  const menu = document.querySelector("#primary-nav");
  const toggle = document.querySelector("#menu-toggle");
  menu?.classList.remove("open");
  toggle?.setAttribute("aria-expanded", "false");
}

function updateAccountNav() {
  const authenticated = state.bootstrap?.authenticated;
  if (authenticated) {
    accountNav.innerHTML = `
      <a class="applications-link" href="/my-applications" data-link>My applications</a>
      <a class="account-chip" href="/my-account" data-link aria-label="Open Alex Green account"><span>AG</span><b>Alex</b></a>`;
  } else {
    accountNav.innerHTML = `
      <a class="text-link" href="/user/login" data-link>Sign in</a>
      <a class="header-action" href="/user/register" data-link>Create account</a>`;
  }
}

function loading(label = "Loading...") {
  main.innerHTML = `<div class="loading-shell"><span class="spinner"></span><p>${escapeHtml(label)}</p></div>`;
}

function pageError(title, message, retry) {
  main.innerHTML = `
    <section class="status-page constrained">
      <div class="status-symbol">!</div>
      <p class="eyebrow">Local service</p>
      <h1>${escapeHtml(title)}</h1>
      <p>${escapeHtml(message)}</p>
      <div class="button-row">
        ${retry ? '<button class="primary-button" id="retry-action" type="button">Retry</button>' : ""}
        <a class="secondary-button" href="/jobs" data-link>Back to jobs</a>
      </div>
    </section>`;
  if (retry) document.querySelector("#retry-action")?.addEventListener("click", retry);
}

function safetyNote(short = false) {
  return `<div class="safety-note"><span aria-hidden="true">${icons.check}</span><p><strong>${short ? "Local only" : "Private, local application"}</strong>${short ? "" : " No real employer, email service, file host, or identity provider is contacted."}</p></div>`;
}

function jobCard(job, selected = false) {
  return `
    <article class="job-card ${selected ? "selected" : ""}" data-job-key="${escapeHtml(job.key)}">
      <button class="job-card-main" type="button" data-open-job="${escapeHtml(job.key)}" aria-label="Open ${escapeHtml(job.title)} at ${escapeHtml(job.organization)}">
        <span class="org-avatar">${escapeHtml(job.organization.split(/\s+/).slice(0, 2).map((word) => word[0]).join(""))}</span>
        <span class="job-copy">
          <span class="job-title">${escapeHtml(job.title)}</span>
          <span class="organization">${escapeHtml(job.organization)}</span>
          <span class="job-meta">${icons.location} ${escapeHtml(job.location)}</span>
          <span class="tag-row"><span>${escapeHtml(job.workMode)}</span><span>${escapeHtml(job.employment)}</span><span>${escapeHtml(job.sector)}</span></span>
          <span class="job-summary">${escapeHtml(job.summary)}</span>
          <span class="date-line">Published ${escapeHtml(job.published)} · Expires ${escapeHtml(job.expires)}</span>
        </span>
      </button>
      <button class="bookmark-button" type="button" title="Save job" aria-label="Save ${escapeHtml(job.title)}">${icons.bookmark}</button>
      ${job.quickApply ? '<span class="quick-badge">Quick Apply</span>' : ""}
    </article>`;
}

function searchForm(values = {}) {
  return `
    <form class="job-search-form" id="job-search-form" aria-label="Search nonprofit jobs">
      <label class="search-field keyword-field"><span>Keywords</span><div><b aria-hidden="true">${icons.search}</b><input name="keywords" value="${escapeHtml(values.keywords || "")}" placeholder="Job title or organization" autocomplete="off"></div></label>
      <label class="search-field location-field"><span>Location</span><div><b aria-hidden="true">${icons.location}</b><input name="location" value="${escapeHtml(values.location || "")}" placeholder="e.g. Washington, DC" autocomplete="address-level2"></div></label>
      <label class="search-field"><span>Job type</span><select name="employment"><option value="">Any type</option>${["Full Time", "Part Time", "Contract"].map((item) => `<option ${values.employment === item ? "selected" : ""}>${item}</option>`).join("")}</select></label>
      <label class="search-field"><span>Organization type</span><select name="sector"><option value="">Any organization</option>${["Nonprofit", "Consulting"].map((item) => `<option ${values.sector === item ? "selected" : ""}>${item}</option>`).join("")}</select></label>
      <button class="search-button" type="submit"><span aria-hidden="true">${icons.search}</span> Search jobs</button>
    </form>`;
}

async function runSearch(form) {
  const button = form.querySelector("button[type=submit]");
  const values = Object.fromEntries(new FormData(form));
  const params = new URLSearchParams(values);
  setBusy(button, true, "Searching...");
  try {
    state.search = await api(`/api/jobs?${params}`);
    const searchPath = `/jobs?${params}`;
    history.pushState({}, "", searchPath);
    renderJobs();
  } catch (error) {
    const retry = () => {
      navigate("/jobs");
      setTimeout(() => {
        const next = document.querySelector("#job-search-form");
        if (next) {
          Object.entries(values).forEach(([key, value]) => { next.elements[key].value = value; });
          runSearch(next);
        }
      }, 0);
    };
    pageError("Search unavailable", error.message, retry);
  } finally {
    setBusy(button, false);
  }
}

function renderJobs() {
  const params = new URLSearchParams(window.location.search);
  const values = state.search?.query || Object.fromEntries(params);
  const hasSearch = Boolean(state.search);
  main.innerHTML = `
    <section class="search-band">
      <div class="constrained">
        <div class="search-heading">
          <div><p class="eyebrow">Ideas into action</p><h1>Find nonprofit jobs</h1></div>
          <p>Connect with organizations working for stronger communities.</p>
        </div>
        ${searchForm(values)}
      </div>
    </section>
    <section class="jobs-workspace constrained">
      <div class="results-header">
        <div><p class="eyebrow">Opportunities</p><h2>${hasSearch ? `${state.search.count} matching ${state.search.count === 1 ? "job" : "jobs"}` : "Featured nonprofit jobs"}</h2></div>
        ${hasSearch ? '<button class="quiet-button" type="button" id="reset-search">Reset search</button>' : '<span class="updated-label">Updated locally today</span>'}
      </div>
      ${hasSearch ? `
        <div class="active-filters">${Object.entries(values).filter(([, value]) => value).map(([key, value]) => `<span><b>${escapeHtml(key)}:</b> ${escapeHtml(value)}</span>`).join("")}</div>
        ${state.search.count ? `<div class="results-layout"><div class="job-list">${state.search.results.map((job) => jobCard(job)).join("")}</div><aside class="results-aside"><img src="/static/assets/idealist-mark.svg" alt="" width="64" height="64"><h3>Select a job to see the details</h3><p>Compare the role, organization, requirements, and application method.</p>${safetyNote(true)}</aside></div>` : `<div class="empty-state"><div class="empty-illustration">${icons.search}</div><h3>No jobs match all four filters</h3><p>Check your spelling or broaden one filter, then try again.</p><button class="secondary-button" type="button" id="clear-filters">Clear filters</button></div>`}
      ` : `
        <div class="intro-grid">
          <div class="feature-panel youth"><span>WASHINGTON, DC</span><h3>Turn your values into impact</h3><p>Explore program leadership roles with nonprofits serving local and national communities.</p></div>
          <div class="featured-list">${state.bootstrap.jobs.slice(0, 3).map((job) => jobCard(job)).join("")}</div>
        </div>`}
    </section>`;

  document.querySelector("#job-search-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    runSearch(event.currentTarget);
  });
  document.querySelector("#reset-search")?.addEventListener("click", () => { state.search = null; navigate("/jobs"); });
  document.querySelector("#clear-filters")?.addEventListener("click", () => { state.search = null; navigate("/jobs"); });
  bindJobButtons();
}

function bindJobButtons() {
  document.querySelectorAll("[data-open-job]").forEach((button) => button.addEventListener("click", () => openJob(button.dataset.openJob)));
  document.querySelectorAll(".bookmark-button").forEach((button) => button.addEventListener("click", () => {
    if (!state.bootstrap.authenticated) {
      state.pendingPath = pathOnly();
      navigate("/user/login");
      toast("Sign in to save jobs.", "info");
      return;
    }
    button.textContent = "★";
    button.classList.add("saved");
    toast("Job saved locally.");
  }));
}

async function openJob(key) {
  loading("Opening job details...");
  try {
    const payload = await api(`/api/jobs/${encodeURIComponent(key)}`);
    state.selectedJob = payload.job;
    navigate(`/en/nonprofit-job/${key}`, { replace: true });
  } catch (error) {
    pageError("Job unavailable", error.message, () => openJob(key));
  }
}

function renderJobDetail() {
  const key = pathOnly().split("/").pop();
  const job = state.selectedJob?.key === key ? state.selectedJob : state.bootstrap.jobs.find((item) => item.key === key);
  if (!job) {
    pageError("Job not found", "This listing is not available in the local catalog.");
    return;
  }
  if (!state.selectedJob || state.selectedJob.key !== key) {
    api(`/api/jobs/${encodeURIComponent(key)}`).then((payload) => { state.selectedJob = payload.job; }).catch(() => {});
  }
  main.innerHTML = `
    <section class="detail-band"><div class="constrained"><a class="back-link" href="/jobs${window.location.search}" data-link>← Back to search results</a></div></section>
    <section class="job-detail constrained">
      <article class="detail-content">
        <div class="detail-heading">
          <div class="org-avatar large">DA</div>
          <div><p class="eyebrow">${escapeHtml(job.sector)} · ${escapeHtml(job.employment)}</p><h1>${escapeHtml(job.title)}</h1><a href="/local-boundary?for=organization" data-link class="organization-link">${escapeHtml(job.organization)}</a></div>
        </div>
        <div class="detail-facts"><span>${icons.location} ${escapeHtml(job.location)}</span><span>${icons.briefcase} ${escapeHtml(job.workMode)}</span><span>${escapeHtml(job.salary)}</span></div>
        <div class="detail-dates"><span>Published ${escapeHtml(job.published)}</span><span>Applications close ${escapeHtml(job.expires)}</span></div>
        <div class="mobile-apply-slot"><button class="primary-button apply-button" type="button">${job.quickApply ? "Quick Apply" : "Apply"} ${icons.arrow}</button></div>
        <section><h2>About the role</h2>${job.description.map((paragraph) => `<p>${escapeHtml(paragraph)}</p>`).join("")}</section>
        <section><h2>What you'll do</h2><ul>${job.responsibilities.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></section>
        <section><h2>What you'll bring</h2><ul>${job.qualifications.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></section>
        <section class="organization-section"><h2>About Dumbarton Arts & Education</h2><p>This Washington, DC nonprofit expands access to arts-integrated learning, performances, and community education.</p></section>
      </article>
      <aside class="apply-panel">
        <span class="quick-badge">${job.quickApply ? "Quick Apply" : "Application"}</span>
        <h2>Interested in this role?</h2>
        <p>Apply with your Idealist profile, resume, and a tailored cover letter.</p>
        <button class="primary-button apply-button" type="button">${job.quickApply ? "Quick Apply" : "Apply"} ${icons.arrow}</button>
        <button class="secondary-button save-detail" type="button">${icons.bookmark} Save job</button>
        ${safetyNote()}
      </aside>
    </section>`;
  document.querySelectorAll(".apply-button").forEach((button) => button.addEventListener("click", () => startApplication(job.key)));
  document.querySelector(".save-detail")?.addEventListener("click", () => toast("Job saved locally."));
}

function startApplication(key) {
  state.pendingPath = `/application/${key}`;
  sessionStorage.setItem("idealistPendingPath", state.pendingPath);
  if (!state.bootstrap.authenticated) navigate("/user/register");
  else navigate(state.pendingPath);
}

function authAside(mode) {
  return `<aside class="auth-aside"><img src="/static/assets/idealist-mark.svg" alt="" width="76" height="76"><p class="eyebrow">Applicant account</p><h2>${mode === "register" ? "Keep your nonprofit career in one place" : "Welcome back"}</h2><ul><li>${icons.check} Apply with your saved profile</li><li>${icons.check} Keep your resume ready</li><li>${icons.check} Review applications anytime</li></ul>${safetyNote()}</aside>`;
}

function renderRegister() {
  main.innerHTML = `
    <section class="auth-layout constrained">
      ${authAside("register")}
      <div class="auth-form-panel">
        <p class="eyebrow">Create an account</p><h1>Applicant registration</h1>
        <p class="lead">Create your local applicant account to continue with this application.</p>
        <div class="step-strip"><span class="active"><b>1</b> Account</span><span><b>2</b> Sign in</span><span><b>3</b> Apply</span></div>
        <div class="form-alert hidden" id="form-alert" role="alert"></div>
        <form id="register-form" class="stacked-form" novalidate>
          <input type="hidden" name="accountType" value="APPLICANT">
          <div class="field-grid">
            <label><span>First name</span><input required name="firstName" value="Alex" autocomplete="given-name"></label>
            <label><span>Last name</span><input required name="lastName" value="Green" autocomplete="family-name"></label>
          </div>
          <label><span>Email address</span><input required type="email" name="email" value="alex.green.uoft@clawbench.cc" autocomplete="email"></label>
          <label><span>Postal code</span><input required name="postalCode" value="M5S 2H7" autocomplete="postal-code" maxlength="7"></label>
          <label><span>Password</span><input required type="password" name="password" autocomplete="new-password" minlength="10" placeholder="At least 10 characters"><small>Use uppercase, lowercase, and a number.</small></label>
          <label class="check-field"><input required type="checkbox" name="termsAccepted"><span>I agree to the local applicant terms and privacy notice.</span></label>
          <button class="primary-button full" type="submit">Create applicant account ${icons.arrow}</button>
        </form>
        <p class="auth-switch">Already registered? <a href="/user/login" data-link>Sign in</a></p>
      </div>
    </section>`;
  document.querySelector("#register-form")?.addEventListener("submit", handleRegister);
}

function showFormError(message, extra = "") {
  const alert = document.querySelector("#form-alert");
  if (!alert) return;
  alert.classList.remove("hidden");
  alert.innerHTML = `<strong>Check your information</strong><span>${escapeHtml(message)}</span>${extra}`;
  alert.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function handleRegister(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  const values = Object.fromEntries(new FormData(form));
  values.termsAccepted = form.elements.termsAccepted.checked;
  const button = form.querySelector("button[type=submit]");
  setBusy(button, true, "Creating account...");
  try {
    await api("/api/auth/register", { method: "POST", body: values });
    state.lastPassword = values.password;
    toast("Account created. Sign in to continue.");
    navigate("/user/login");
  } catch (error) {
    showFormError(error.message, error.status === 409 ? ' <a href="/user/login" data-link>Sign in instead</a>' : "");
  } finally {
    setBusy(button, false);
  }
}

function renderLogin() {
  main.innerHTML = `
    <section class="auth-layout constrained">
      ${authAside("login")}
      <div class="auth-form-panel compact-auth">
        <p class="eyebrow">My account</p><h1>Sign in</h1><p class="lead">Use your applicant email and password.</p>
        <div class="form-alert hidden" id="form-alert" role="alert"></div>
        <form id="login-form" class="stacked-form" novalidate>
          <label><span>Email address</span><input required type="email" name="email" value="alex.green.uoft@clawbench.cc" autocomplete="email"></label>
          <label><span>Password</span><input required type="password" name="password" value="${escapeHtml(state.lastPassword)}" autocomplete="current-password"></label>
          <div class="form-between"><label class="check-field"><input type="checkbox" checked><span>Keep me signed in locally</span></label><button class="link-button" type="button" data-boundary="support">Forgot password?</button></div>
          <button class="primary-button full" type="submit">Sign in ${icons.arrow}</button>
        </form>
        <div class="provider-separator"><span>or</span></div>
        <button class="provider-button" type="button" data-boundary="support">Sign in with Google <span>Local boundary</span></button>
        <p class="auth-switch">Don't have an account? <a href="/user/register" data-link>Create one</a></p>
      </div>
    </section>`;
  document.querySelector("#login-form")?.addEventListener("submit", handleLogin);
}

async function handleLogin(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  const values = Object.fromEntries(new FormData(form));
  const callbackUrl = sessionStorage.getItem("idealistPendingPath") || state.pendingPath;
  const formBody = new URLSearchParams({
    email: values.email,
    password: values.password,
    callbackUrl,
    csrfToken: crypto.randomUUID().replaceAll("-", ""),
    json: "true",
  });
  const button = form.querySelector("button[type=submit]");
  setBusy(button, true, "Signing in...");
  try {
    const payload = await api("/api/auth/sign-in", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: formBody.toString(),
    });
    state.bootstrap = await api("/api/bootstrap");
    updateAccountNav();
    toast("Signed in as Alex Green.");
    navigate(payload.url || "/jobs");
  } catch (error) {
    showFormError(error.message);
  } finally {
    setBusy(button, false);
  }
}

function applicationPayload(form) {
  const values = Object.fromEntries(new FormData(form));
  return {
    jobKey: state.bootstrap.targetJobKey,
    applicant: {
      firstName: values.firstName,
      lastName: values.lastName,
      email: values.email,
      city: values.city,
      province: values.province,
    },
    resume: { source: "ASSIGNED_PROFILE", fileName: "Alex_Green_Resume.pdf" },
    coverLetter: values.coverLetter,
    accuracyConfirmed: form.elements.accuracyConfirmed.checked,
  };
}

function renderApplication() {
  if (!state.bootstrap.authenticated) {
    state.pendingPath = pathOnly();
    sessionStorage.setItem("idealistPendingPath", state.pendingPath);
    navigate("/user/login", { replace: true });
    return;
  }
  const existing = state.bootstrap.applications.find((item) => item.job_key === state.bootstrap.targetJobKey);
  if (existing?.status === "SUBMITTED_LOCALLY") { renderApplicationComplete(existing); return; }
  if (existing?.status === "PENDING_PROFILE") { renderReview(existing.payload); return; }
  const job = state.bootstrap.jobs.find((item) => item.key === state.bootstrap.targetJobKey);
  const draft = state.bootstrap.draft?.payload;
  const cover = draft?.coverLetter || sessionStorage.getItem("idealistCoverLetter") || "";
  main.innerHTML = `
    <section class="application-header"><div class="constrained"><a href="/en/nonprofit-job/${escapeHtml(job.key)}" data-link>← ${escapeHtml(job.title)}</a><span>Application</span></div></section>
    <section class="application-layout constrained">
      <aside class="application-sidebar">
        <p class="eyebrow">Applying to</p><h2>${escapeHtml(job.title)}</h2><p>${escapeHtml(job.organization)}</p><p class="muted">${escapeHtml(job.location)}</p>
        <ol class="application-steps"><li class="done"><b>${icons.check}</b><span>Account<strong>Signed in</strong></span></li><li class="active"><b>2</b><span>Application<strong>Profile &amp; documents</strong></span></li><li><b>3</b><span>Review<strong>Submit locally</strong></span></li></ol>
        ${safetyNote()}
      </aside>
      <div class="application-form-panel">
        <div class="form-heading"><p class="eyebrow">Quick Apply</p><h1>Your application</h1><p>Review your profile and tailor your cover letter for this role.</p></div>
        <div class="form-alert hidden" id="form-alert" role="alert"></div>
        <form id="application-form" class="application-form" novalidate>
          <fieldset><legend><span>${icons.person}</span> Contact information</legend><div class="field-grid"><label><span>First name</span><input name="firstName" value="Alex" readonly></label><label><span>Last name</span><input name="lastName" value="Green" readonly></label></div><label><span>Email</span><input name="email" value="alex.green.uoft@clawbench.cc" readonly></label><div class="field-grid"><label><span>City</span><input name="city" value="Toronto" readonly></label><label><span>Province</span><input name="province" value="Ontario" readonly></label></div></fieldset>
          <fieldset><legend><span>${icons.file}</span> Resume</legend><label class="resume-option selected"><input type="radio" checked name="resumeChoice" value="assigned"><span class="pdf-icon">PDF</span><span><strong>Alex_Green_Resume.pdf</strong><small>Assigned profile resume · 84 KB · Ready locally</small></span><b>${icons.check}</b></label><p class="field-help">This is a local representation of the assigned resume. No binary file is uploaded.</p></fieldset>
          <fieldset><legend><span>✎</span> Cover letter</legend><label><span>Tell Dumbarton Arts & Education why you're a strong fit <em>Required</em></span><textarea name="coverLetter" required minlength="180" maxlength="5000" rows="11" placeholder="Dear Dumbarton Arts & Education hiring team,...">${escapeHtml(cover)}</textarea><small class="character-count"><span id="cover-count">${cover.length}</span> / 5,000 · minimum 180 characters</small></label></fieldset>
          <label class="check-field accuracy"><input type="checkbox" name="accuracyConfirmed" ${draft?.accuracyConfirmed ? "checked" : ""} required><span>I confirm this profile, resume selection, and cover letter are accurate.</span></label>
          <div class="application-actions"><button class="quiet-button" id="save-later" type="button">Save draft</button><button class="primary-button" type="submit">Review application ${icons.arrow}</button></div>
        </form>
      </div>
    </section>`;
  const textarea = document.querySelector("textarea[name=coverLetter]");
  textarea?.addEventListener("input", () => {
    document.querySelector("#cover-count").textContent = String(textarea.value.length);
    sessionStorage.setItem("idealistCoverLetter", textarea.value);
  });
  document.querySelector("#save-later")?.addEventListener("click", () => toast("Finish all required fields to create a server-side draft.", "info"));
  document.querySelector("#application-form")?.addEventListener("submit", handleReview);
}

async function handleReview(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  const payload = applicationPayload(form);
  const button = form.querySelector("button[type=submit]");
  setBusy(button, true, "Saving review...");
  try {
    await api("/api/applications/draft", { method: "POST", body: payload });
    state.reviewPayload = payload;
    state.bootstrap = await api("/api/bootstrap");
    renderReview(payload);
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (error) {
    if (error.status === 401) {
      state.pendingPath = pathOnly();
      navigate("/user/login");
      toast("Your session expired. Sign in to recover the application.", "info");
    } else showFormError(error.message);
  } finally {
    setBusy(button, false);
  }
}

function renderReview(payload) {
  const job = state.bootstrap.jobs.find((item) => item.key === payload.jobKey);
  main.innerHTML = `
    <section class="application-header"><div class="constrained"><button class="link-button" id="edit-application" type="button">← Edit application</button><span>Review</span></div></section>
    <section class="review-layout constrained">
      <div class="review-main">
        <p class="eyebrow">Final review</p><h1>Review your application</h1><p class="lead">Make sure everything is ready before saving your submission locally.</p>
        <section class="review-section"><div><h2>Contact information</h2><button type="button" class="link-button edit-review">Edit</button></div><dl><dt>Name</dt><dd>Alex Green</dd><dt>Email</dt><dd>${escapeHtml(payload.applicant.email)}</dd><dt>Location</dt><dd>Toronto, Ontario</dd></dl></section>
        <section class="review-section"><div><h2>Resume</h2><button type="button" class="link-button edit-review">Edit</button></div><div class="document-review"><span class="pdf-icon">PDF</span><span><strong>${escapeHtml(payload.resume.fileName)}</strong><small>Assigned profile resume · Local representation</small></span></div></section>
        <section class="review-section"><div><h2>Cover letter</h2><button type="button" class="link-button edit-review">Edit</button></div><div class="cover-review">${escapeHtml(payload.coverLetter)}</div></section>
      </div>
      <aside class="submit-panel"><p class="eyebrow">${escapeHtml(job.organization)}</p><h2>${escapeHtml(job.title)}</h2><p>${escapeHtml(job.location)}</p><hr><p class="submit-confirm"><span>${icons.check}</span> Draft saved and ready</p><button class="primary-button full" id="submit-application" type="button">Submit application</button>${safetyNote()}<p class="fine-print">Submitting creates a local record only. It cannot be withdrawn because nothing is delivered.</p></aside>
    </section>`;
  document.querySelector("#edit-application")?.addEventListener("click", renderApplication);
  document.querySelectorAll(".edit-review").forEach((button) => button.addEventListener("click", renderApplication));
  document.querySelector("#submit-application")?.addEventListener("click", () => submitApplication(payload));
}

async function submitApplication(payload) {
  const button = document.querySelector("#submit-application");
  setBusy(button, true, "Submitting locally...");
  try {
    let staged = state.bootstrap.applications.find((item) => item.job_key === payload.jobKey && item.status === "PENDING_PROFILE");
    if (!staged) {
      const stageResult = await api("/api/applications/submit", { method: "POST", body: payload });
      staged = { id: stageResult.applicationId };
    }
    const result = await api("/data/userdashboard/missing-info", {
      method: "POST",
      body: {
        firstName: "Alex",
        lastName: "Green",
        email: "alex.green.uoft@clawbench.cc",
        location: "Toronto, Ontario, Canada",
        resumeFileName: "Alex_Green_Resume.pdf",
        intent: "COMPLETE_IDEALIST_PROFILE",
      },
    });
    sessionStorage.removeItem("idealistCoverLetter");
    sessionStorage.removeItem("idealistPendingPath");
    state.bootstrap = await api("/api/bootstrap");
    renderApplicationComplete({ id: result.applicationId || staged.id, created_at: new Date().toISOString(), payload });
  } catch (error) {
    if (error.status === 401) {
      state.pendingPath = pathOnly();
      navigate("/user/login");
      toast("Sign in again, then review the recovered draft.", "info");
    } else {
      toast(error.message, "error");
      setBusy(button, false);
    }
  }
}

function renderApplicationComplete(application) {
  const job = state.bootstrap.jobs.find((item) => item.key === state.bootstrap.targetJobKey);
  main.innerHTML = `
    <section class="completion-page constrained">
      <div class="completion-mark">${icons.check}</div><p class="eyebrow">Application saved</p><h1>Your local application is complete</h1><p class="lead">Your application for <strong>${escapeHtml(job.title)}</strong> at <strong>${escapeHtml(job.organization)}</strong> is in My applications.</p>
      <div class="completion-summary"><div><span>Application ID</span><strong>ID-${String(application.id).padStart(5, "0")}</strong></div><div><span>Status</span><strong>Submitted locally</strong></div><div><span>Delivery</span><strong>None</strong></div></div>
      <div class="no-delivery"><strong>No real-world effect</strong><p>This offline replica did not contact Dumbarton Arts & Education, send an email, upload a file, or verify an identity.</p></div>
      <div class="button-row"><a class="primary-button" href="/my-applications" data-link>View My applications</a><a class="secondary-button" href="/jobs" data-link>Back to job search</a></div>
    </section>`;
}

function renderMyApplications() {
  if (!state.bootstrap.authenticated) { navigate("/user/login", { replace: true }); return; }
  const applications = state.bootstrap.applications;
  main.innerHTML = `
    <section class="account-band"><div class="constrained"><p class="eyebrow">Alex Green</p><h1>My applications</h1><p>Review the information and documents saved with your local applications.</p></div></section>
    <section class="account-layout constrained">
      <nav class="account-sidebar" aria-label="Account"><a class="active" href="/my-applications" data-link>Applications <span>${applications.length}</span></a><a href="/my-account" data-link>Profile &amp; resume</a><button type="button" id="logout-button">Sign out</button></nav>
      <div class="applications-panel">
        <div class="panel-heading"><h2>${applications.length} ${applications.length === 1 ? "application" : "applications"}</h2><a href="/jobs" data-link class="secondary-button">Find more jobs</a></div>
        ${applications.length ? applications.map((application) => {
          const job = state.bootstrap.jobs.find((item) => item.key === application.job_key);
          return `<article class="application-card"><div class="org-avatar">DA</div><div><span class="status-pill">${application.status === "SUBMITTED_LOCALLY" ? "Submitted locally" : "Pending profile"}</span><h3>${escapeHtml(job.title)}</h3><p>${escapeHtml(job.organization)} · ${escapeHtml(job.location)}</p><p class="muted">Saved ${new Date(application.created_at).toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" })}</p></div><button type="button" class="secondary-button view-application">View details</button><div class="application-card-details hidden"><dl><dt>Resume</dt><dd>${escapeHtml(application.payload.resume.fileName)}</dd><dt>Cover letter</dt><dd>${escapeHtml(application.payload.coverLetter)}</dd><dt>Delivery</dt><dd>No employer delivery (offline replica)</dd></dl></div></article>`;
        }).join("") : '<div class="empty-state"><h3>No applications yet</h3><p>Applications completed in this local session will appear here.</p></div>'}
      </div>
    </section>`;
  bindAccountActions();
  document.querySelectorAll(".view-application").forEach((button) => button.addEventListener("click", () => {
    const details = button.closest(".application-card").querySelector(".application-card-details");
    details.classList.toggle("hidden");
    button.textContent = details.classList.contains("hidden") ? "View details" : "Hide details";
  }));
}

function renderAccount() {
  if (!state.bootstrap.authenticated) { navigate("/user/login", { replace: true }); return; }
  const resume = state.bootstrap.profileResume;
  main.innerHTML = `
    <section class="account-band"><div class="constrained"><p class="eyebrow">Applicant profile</p><h1>Alex Green</h1><p>${escapeHtml(state.bootstrap.account.email)}</p></div></section>
    <section class="account-layout constrained">
      <nav class="account-sidebar"><a href="/my-applications" data-link>Applications <span>${state.bootstrap.applications.length}</span></a><a class="active" href="/my-account" data-link>Profile &amp; resume</a><button type="button" id="logout-button">Sign out</button></nav>
      <div class="profile-panel"><section><div class="section-heading"><h2>Contact profile</h2><span class="status-pill">${state.bootstrap.account.profile_complete ? "Complete" : "Application ready"}</span></div><dl class="profile-list"><dt>Name</dt><dd>Alex Green</dd><dt>Email</dt><dd>${escapeHtml(state.bootstrap.account.email)}</dd><dt>Location</dt><dd>Toronto, Ontario, Canada</dd><dt>Postal code</dt><dd>${escapeHtml(state.bootstrap.account.postal_code)}</dd><dt>Account type</dt><dd>Applicant</dd></dl></section><section><div class="section-heading"><h2>Resume</h2><span class="status-pill">Ready</span></div><div class="resume-profile"><span class="pdf-icon">PDF</span><div><strong>${escapeHtml(resume.file_name)}</strong><p>${escapeHtml(resume.summary)}</p><small>${escapeHtml(resume.display_size)} · Assigned local profile</small></div></div>${safetyNote()}</section></div>
    </section>`;
  bindAccountActions();
}

function bindAccountActions() {
  document.querySelector("#logout-button")?.addEventListener("click", async () => {
    try {
      await api("/api/auth/logout", { method: "POST", body: { intent: "LOCAL_LOGOUT" } });
      state.bootstrap = await api("/api/bootstrap");
      updateAccountNav();
      navigate("/jobs");
      toast("Signed out. Your local account remains available.", "info");
    } catch (error) { toast(error.message, "error"); }
  });
}

function renderBoundary() {
  const kind = new URLSearchParams(window.location.search).get("for") || "external-employer";
  main.innerHTML = `<section class="status-page constrained"><div class="status-symbol boundary">↗</div><p class="eyebrow">Local boundary</p><h1>This area is not part of the offline task</h1><p>To keep the workflow private and deterministic, this replica does not open employer sites, provider logins, learning services, support email, or other external destinations.</p>${safetyNote()}<a class="primary-button" href="/jobs" data-link>Return to jobs</a></section>`;
  api("/api/boundary", { method: "POST", body: { boundary: ["post-job", "learning", "resources", "support", "external-employer"].includes(kind) ? kind : "external-employer" } }).catch(() => {});
}

function renderNotFound() {
  main.innerHTML = `<section class="status-page constrained"><div class="status-symbol">404</div><p class="eyebrow">Page not found</p><h1>We couldn't find that local page</h1><p>The address may be incomplete, or the page is outside this task-scoped replica.</p><a class="primary-button" href="/jobs" data-link>Search jobs</a></section>`;
}

function renderRoute() {
  updateAccountNav();
  const path = pathOnly();
  if (path === "/" || path === "/jobs") renderJobs();
  else if (path.startsWith("/en/nonprofit-job/")) renderJobDetail();
  else if (path === "/user/register") renderRegister();
  else if (path === "/user/login") renderLogin();
  else if (path.startsWith("/application/")) renderApplication();
  else if (path === "/my-applications") renderMyApplications();
  else if (path === "/my-account") renderAccount();
  else if (path === "/local-boundary") renderBoundary();
  else renderNotFound();
}

document.addEventListener("click", (event) => {
  const link = event.target.closest("a[data-link]");
  if (link && link.origin === window.location.origin) {
    event.preventDefault();
    navigate(link.pathname + link.search);
    return;
  }
  const boundary = event.target.closest("[data-boundary]");
  if (boundary) {
    event.preventDefault();
    navigate(`/local-boundary?for=${encodeURIComponent(boundary.dataset.boundary)}`);
  }
});

document.querySelector("#menu-toggle")?.addEventListener("click", (event) => {
  const menu = document.querySelector("#primary-nav");
  const open = menu.classList.toggle("open");
  event.currentTarget.setAttribute("aria-expanded", String(open));
});

window.addEventListener("popstate", renderRoute);

async function start() {
  try {
    state.bootstrap = await api("/api/bootstrap");
    state.pendingPath = sessionStorage.getItem("idealistPendingPath") || state.pendingPath;
    const params = new URLSearchParams(window.location.search);
    if (pathOnly() === "/jobs" && [...params.keys()].length) {
      state.search = await api(`/api/jobs?${params}`);
    }
    renderRoute();
  } catch (error) {
    pageError("Unable to start the local site", error.message, () => window.location.reload());
  }
}

start();

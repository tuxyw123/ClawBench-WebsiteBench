const LANGUAGE_KEY = "websitebench-language";
const LEGACY_LANGUAGE_KEY = "clawbench-viewer-language";
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

const validLanguage = (value) => (value === "zh" || value === "en" ? value : "en");

function currentLanguage() {
  try {
    return validLanguage(
      window.localStorage.getItem(LANGUAGE_KEY) ||
        window.localStorage.getItem(LEGACY_LANGUAGE_KEY),
    );
  } catch (_) {
    return "en";
  }
}

function applyLanguage(language, persist = false) {
  const selected = validLanguage(language);
  document.documentElement.lang = selected;
  document.querySelectorAll("[data-placeholder-en]").forEach((element) => {
    element.placeholder =
      selected === "zh" ? element.dataset.placeholderZh : element.dataset.placeholderEn;
  });
  document.querySelectorAll("[data-option-en]").forEach((element) => {
    element.textContent =
      selected === "zh" ? element.dataset.optionZh : element.dataset.optionEn;
  });
  document.querySelectorAll("[data-alt-en]").forEach((element) => {
    element.alt = selected === "zh" ? element.dataset.altZh : element.dataset.altEn;
  });
  document.querySelectorAll("[data-aria-label-en]").forEach((element) => {
    element.setAttribute(
      "aria-label",
      selected === "zh" ? element.dataset.ariaLabelZh : element.dataset.ariaLabelEn,
    );
  });
  const toggle = document.querySelector("[data-language-toggle]");
  if (toggle) {
    toggle.setAttribute(
      "aria-label",
      selected === "zh" ? "切换为英文" : "Switch to Chinese",
    );
  }
  if (persist) {
    try {
      window.localStorage.setItem(LANGUAGE_KEY, selected);
      window.localStorage.setItem(LEGACY_LANGUAGE_KEY, selected);
    } catch (_) {
      // The page still switches when storage is unavailable.
    }
  }
}

function setupLanguage() {
  applyLanguage(currentLanguage());
  document.querySelector("[data-language-toggle]")?.addEventListener("click", () => {
    applyLanguage(document.documentElement.lang === "zh" ? "en" : "zh", true);
  });
}

const lines = (value) =>
  String(value || "")
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);

function translated(en, zh) {
  return document.documentElement.lang === "zh" ? zh : en;
}

function setupReviewForm() {
  const form = document.querySelector("#review-form");
  if (!form) return;
  const status = document.querySelector("#review-status");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    status.className = "";
    status.textContent = translated("Saving…", "正在保存…");
    const values = new FormData(form);
    const dimensions = {};
    form.querySelectorAll("[data-review-dimension]").forEach((fieldset) => {
      const name = fieldset.dataset.reviewDimension;
      dimensions[name] = {
        rating: values.get(`${name}-rating`),
        notes: values.get(`${name}-notes`),
        evidence_refs: lines(values.get(`${name}-evidence`)),
      };
    });
    const body = {
      expected_revision: Number(form.dataset.revision),
      artifact_fingerprint: form.dataset.fingerprint,
      review: {
        reviewer: values.get("reviewer"),
        gate: values.get("gate"),
        visibility: values.get("visibility"),
        dimensions,
        notes: values.get("notes"),
        evidence_refs: lines(values.get("evidence_refs")),
      },
    };
    try {
      const response = await fetch(
        `/api/reviews/${encodeURIComponent(form.dataset.itemKey)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
          body: JSON.stringify(body),
        },
      );
      const result = await response.json();
      if (!response.ok) {
        throw new Error(
          result.detail ||
            result.error ||
            translated("Review save failed", "审核保存失败"),
        );
      }
      form.dataset.revision = result.revision;
      status.className = "save-success";
      status.textContent = translated(
        `Saved revision ${result.revision}.`,
        `已保存第 ${result.revision} 版。`,
      );
    } catch (error) {
      status.className = "save-error";
      status.textContent = error.message;
    }
  });
}

setupLanguage();
setupReviewForm();

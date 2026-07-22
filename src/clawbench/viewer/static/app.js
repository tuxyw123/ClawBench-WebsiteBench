const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

function setupTaskAtlas() {
  const rowsContainer = document.querySelector("#task-rows");
  if (!rowsContainer) return;
  const rows = [...rowsContainer.querySelectorAll("[data-task-row]")];
  const search = document.querySelector("#task-search");
  const source = document.querySelector("#source-filter");
  const category = document.querySelector("#category-filter");
  const stage = document.querySelector("#stage-filter");
  const sort = document.querySelector("#task-sort");
  const count = document.querySelector("#visible-count");

  const initial = new URLSearchParams(window.location.search);
  for (const [control, parameter] of [[source, "source"], [category, "category"], [stage, "stage"]]) {
    const value = initial.get(parameter);
    if (control && value && [...control.options].some((option) => option.value === value)) {
      control.value = value;
    }
  }

  const update = () => {
    const query = search.value.trim().toLowerCase();
    for (const row of rows) {
      const visible =
        (!query || row.dataset.search.includes(query)) &&
        (!source.value || row.dataset.source === source.value) &&
        (!category.value || row.dataset.category === category.value) &&
        (!stage.value || row.dataset.stage === stage.value);
      row.hidden = !visible;
    }
    const numeric = (row, key) => Number(row.dataset[key] || -1);
    rows.sort((left, right) => {
      if (sort.value === "readiness") return numeric(right, "missing") - numeric(left, "missing");
      if (sort.value === "official") return numeric(right, "official") - numeric(left, "official");
      if (sort.value === "legacy") return numeric(right, "legacy") - numeric(left, "legacy");
      return left.dataset.name.localeCompare(right.dataset.name);
    });
    rows.forEach((row) => rowsContainer.append(row));
    count.textContent = rows.filter((row) => !row.hidden).length;
  };
  [search, source, category, stage, sort].filter(Boolean).forEach((control) =>
    control.addEventListener(control === search ? "input" : "change", update),
  );
  update();

  const compare = document.querySelector("#compare-selected");
  const checks = [...document.querySelectorAll(".compare-check")];
  checks.forEach((check) =>
    check.addEventListener("change", () => {
      const selected = checks.filter((item) => item.checked);
      if (selected.length > 4) {
        check.checked = false;
        return;
      }
      const current = checks.filter((item) => item.checked);
      compare.disabled = current.length < 2;
      compare.querySelector("span").textContent = current.length;
    }),
  );
  compare.addEventListener("click", () => {
    const keys = checks.filter((item) => item.checked).map((item) => item.value);
    if (keys.length >= 2 && keys.length <= 4) {
      window.location.assign(`/compare?keys=${encodeURIComponent(keys.join(","))}`);
    }
  });
}

const lines = (value) =>
  value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);

function setupReviewForm() {
  const form = document.querySelector("#review-form");
  if (!form) return;
  const status = document.querySelector("#review-status");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    status.className = "";
    status.textContent = "Saving…";
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
      const response = await fetch(`/api/reviews/${encodeURIComponent(form.dataset.itemKey)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify(body),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || result.error || "Review save failed");
      form.dataset.revision = result.revision;
      status.className = "save-success";
      status.textContent = `Saved revision ${result.revision}.`;
    } catch (error) {
      status.className = "save-error";
      status.textContent = error.message;
    }
  });
}

function stopBlink(review) {
  if (review._blinkTimer) window.clearInterval(review._blinkTimer);
  review._blinkTimer = null;
  review.classList.remove("blink-candidate");
}

function setVisualMode(review, mode) {
  stopBlink(review);
  review.dataset.mode = mode;
  review.querySelectorAll("[data-visual-mode]").forEach((button) =>
    button.classList.toggle("active", button.dataset.visualMode === mode),
  );
  if (mode === "blink") {
    review._blinkTimer = window.setInterval(
      () => review.classList.toggle("blink-candidate"),
      650,
    );
  }
}

function setupVisualReview() {
  const picker = document.querySelector("[data-capture-picker]");
  const reviews = [...document.querySelectorAll("[data-capture]")];
  if (picker) {
    picker.addEventListener("change", () => {
      reviews.forEach((review) => {
        const active = review.dataset.capture === picker.value;
        review.classList.toggle("hidden", !active);
        if (!active) stopBlink(review);
      });
    });
  }
  reviews.forEach((review) => {
    let zoom = 1;
    const stage = review.querySelector("[data-visual-stage]");
    const zoomLabel = review.querySelector("[data-zoom-label]");
    review.querySelectorAll("[data-visual-mode]").forEach((button) =>
      button.addEventListener("click", () => setVisualMode(review, button.dataset.visualMode)),
    );
    review.querySelectorAll("[data-zoom]").forEach((button) =>
      button.addEventListener("click", () => {
        zoom = Math.min(2, Math.max(0.5, zoom + (button.dataset.zoom === "in" ? 0.25 : -0.25)));
        stage.style.setProperty("--zoom", zoom);
        zoomLabel.textContent = `${Math.round(zoom * 100)}%`;
      }),
    );
    stage.addEventListener("pointermove", (event) => {
      if (review.dataset.mode !== "split") return;
      const bounds = stage.getBoundingClientRect();
      const position = Math.min(100, Math.max(0, ((event.clientX - bounds.left) / bounds.width) * 100));
      stage.style.setProperty("--split", `${position}%`);
    });
  });
}

function setupComparePicker() {
  const select = document.querySelector(".compare-picker select[multiple]");
  if (!select) return;
  select.addEventListener("change", () => {
    const selected = [...select.selectedOptions];
    if (selected.length > 4) selected.at(-1).selected = false;
  });
}

setupTaskAtlas();
setupReviewForm();
setupVisualReview();
setupComparePicker();

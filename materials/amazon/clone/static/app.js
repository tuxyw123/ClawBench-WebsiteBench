(() => {
  "use strict";

  const normalizedProductSelectionKey = (selection) => JSON.stringify(
    Object.keys(selection)
      .sort()
      .reduce((normalized, label) => {
        normalized[label] = selection[label];
        return normalized;
      }, {})
  );

  const isAvailableProductQuote = (quote) => Boolean(
    quote &&
      typeof quote === "object" &&
      quote.availability === "AVAILABLE" &&
      Number.isInteger(quote.price_minor) &&
      quote.price_minor >= 0 &&
      typeof quote.currency === "string" &&
      quote.currency.length > 0 &&
      quote.selected_options &&
      typeof quote.selected_options === "object" &&
      !Array.isArray(quote.selected_options)
  );

  const availableProductSelections = (quoteMatrix, axisLabels) => {
    if (!Array.isArray(quoteMatrix) || !Array.isArray(axisLabels)) return [];
    const axes = axisLabels.filter(
      (label, index) =>
        typeof label === "string" && label.length > 0 && axisLabels.indexOf(label) === index
    );
    if (axes.length !== axisLabels.length) return [];
    return quoteMatrix
      .filter(isAvailableProductQuote)
      .map((quote) => quote.selected_options)
      .filter((selection) => {
        const labels = Object.keys(selection);
        return (
          labels.length === axes.length &&
          axes.every(
            (label) =>
              Object.prototype.hasOwnProperty.call(selection, label) &&
              typeof selection[label] === "string"
          )
        );
      })
      .map((selection) => ({ ...selection }));
  };

  const productOptionHasCompatibleQuote = (
    quotedSelections,
    currentSelection,
    axisLabels,
    changedLabel,
    changedValue
  ) => {
    if (
      !Array.isArray(quotedSelections) ||
      !currentSelection ||
      typeof currentSelection !== "object" ||
      !Array.isArray(axisLabels) ||
      !axisLabels.includes(changedLabel) ||
      typeof changedValue !== "string"
    ) {
      return false;
    }
    return quotedSelections.some((selection) =>
      axisLabels.every(
        (label) =>
          selection[label] ===
          (label === changedLabel ? changedValue : currentSelection[label])
      )
    );
  };

  const productOptionHasAnyQuote = (
    quotedSelections,
    axisLabels,
    changedLabel,
    changedValue
  ) => Boolean(
    Array.isArray(quotedSelections) &&
      Array.isArray(axisLabels) &&
      axisLabels.includes(changedLabel) &&
      typeof changedValue === "string" &&
      quotedSelections.some((selection) => selection[changedLabel] === changedValue)
  );

  const repairProductSelection = (
    quotedSelections,
    currentSelection,
    axisLabels,
    changedLabel = null,
    changedValue = null
  ) => {
    if (
      !Array.isArray(quotedSelections) ||
      !quotedSelections.length ||
      !currentSelection ||
      typeof currentSelection !== "object" ||
      !Array.isArray(axisLabels)
    ) {
      return null;
    }
    const changingAxis =
      typeof changedLabel === "string" &&
      axisLabels.includes(changedLabel) &&
      typeof changedValue === "string";
    const candidates = changingAxis
      ? quotedSelections.filter((selection) => selection[changedLabel] === changedValue)
      : quotedSelections;
    if (!candidates.length) return null;

    let bestSelection = candidates[0];
    let bestScore = -1;
    candidates.forEach((selection) => {
      const score = axisLabels.reduce(
        (total, label) =>
          total +
          (label !== changedLabel && selection[label] === currentSelection[label] ? 1 : 0),
        0
      );
      if (score > bestScore) {
        bestSelection = selection;
        bestScore = score;
      }
    });
    return { ...bestSelection };
  };

  // These deterministic helpers are exported only when the file is loaded by
  // Node-based contract tests.  Browser execution continues as the same
  // dependency-free script used by the server-rendered PDP.
  if (typeof module === "object" && module?.exports) {
    module.exports = Object.freeze({
      availableProductSelections,
      normalizedProductSelectionKey,
      productOptionHasAnyQuote,
      productOptionHasCompatibleQuote,
      repairProductSelection,
    });
  }
  if (typeof window === "undefined" || typeof document === "undefined") return;

  const explicitMobilePdp = /^\/gp\/aw\/d\/[A-Z0-9]{10}$/.test(window.location.pathname);
  const cartForm = document.getElementById("addToCart");
  if (cartForm) {
    cartForm.action = explicitMobilePdp
      ? cartForm.dataset.mobileAction
      : cartForm.dataset.desktopAction;
  }

  const pdpGrid = document.querySelector(".pdp-grid");
  const facts = document.querySelector(".pdp-facts");
  const gallery = document.querySelector(".pdp-gallery");
  const buyColumn = document.querySelector(".pdp-buy-column");
  const divider = facts?.querySelector(":scope > hr");
  const title = facts?.querySelector(":scope > #productTitle");
  const rating = facts?.querySelector(":scope > .pdp-rating");
  const choice = facts?.querySelector(":scope > .choice-badge");
  const specs = facts?.querySelector(":scope > .pdp-specs");
  const about = facts?.querySelector(":scope > .about");

  const arrangePdp = () => {
    if (!pdpGrid || !facts || !gallery || !buyColumn || !title) return;
    if (explicitMobilePdp) {
      if (rating) facts.insertBefore(rating, title);
      if (divider) facts.insertBefore(gallery, divider);
      else facts.insertBefore(gallery, title.nextSibling);
      if (specs) facts.insertBefore(buyColumn, specs);
      else facts.appendChild(buyColumn);
    } else {
      if (rating && choice) facts.insertBefore(rating, choice);
      else if (rating && divider) facts.insertBefore(rating, divider);
      pdpGrid.insertBefore(gallery, facts);
      pdpGrid.appendChild(buyColumn);
    }
  };
  arrangePdp();

  const autocompleteForm = document.querySelector("[data-search-autocomplete-form]");
  const autocompleteInput = autocompleteForm?.querySelector("#twotabsearchtextbox");
  const autocompleteDepartment = autocompleteForm?.querySelector('select[name="i"]');
  const autocompleteList = autocompleteForm?.querySelector("[data-search-suggestions]");
  const autocompleteStatus = autocompleteForm?.querySelector("[data-search-suggestions-status]");
  const autocompleteEndpoint = autocompleteForm?.dataset.searchSuggestionsEndpoint;
  if (
    autocompleteForm &&
    autocompleteInput &&
    autocompleteList &&
    autocompleteStatus &&
    autocompleteEndpoint
  ) {
    let autocompleteItems = [];
    let autocompleteIndex = -1;
    let autocompleteTimer = null;
    let autocompleteRequest = null;
    let autocompleteSequence = 0;

    const closeAutocomplete = () => {
      autocompleteIndex = -1;
      autocompleteList.hidden = true;
      autocompleteList.replaceChildren();
      autocompleteInput.setAttribute("aria-expanded", "false");
      autocompleteInput.removeAttribute("aria-activedescendant");
      autocompleteList.removeAttribute("aria-busy");
      autocompleteStatus.textContent = "";
    };

    const setAutocompleteIndex = (nextIndex) => {
      if (!autocompleteItems.length) return;
      autocompleteIndex = (nextIndex + autocompleteItems.length) % autocompleteItems.length;
      const options = Array.from(autocompleteList.querySelectorAll('[role="option"]'));
      options.forEach((option, index) => {
        option.setAttribute("aria-selected", index === autocompleteIndex ? "true" : "false");
      });
      const active = options[autocompleteIndex];
      if (active) {
        autocompleteInput.setAttribute("aria-activedescendant", active.id);
        active.scrollIntoView({ block: "nearest" });
      }
    };

    const chooseAutocomplete = (index) => {
      const suggestion = autocompleteItems[index];
      if (!suggestion) return;
      autocompleteInput.value = suggestion.value;
      closeAutocomplete();
      autocompleteForm.requestSubmit();
    };

    const renderAutocomplete = (items) => {
      autocompleteItems = Array.isArray(items)
        ? items.filter((item) => item && typeof item.value === "string").slice(0, 10)
        : [];
      autocompleteIndex = -1;
      autocompleteList.replaceChildren();
      autocompleteInput.removeAttribute("aria-activedescendant");
      if (!autocompleteItems.length) {
        closeAutocomplete();
        return;
      }
      autocompleteItems.forEach((suggestion, index) => {
        const option = document.createElement("button");
        option.type = "button";
        option.className = "nav-search-suggestion";
        option.id = `nav-search-suggestion-${index}`;
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", "false");

        const icon = document.createElement("span");
        icon.className = "nav-search-suggestion-icon";
        icon.setAttribute("aria-hidden", "true");
        const value = document.createElement("span");
        value.className = "nav-search-suggestion-value";
        value.textContent = suggestion.value;
        const kind = document.createElement("span");
        kind.className = "nav-search-suggestion-kind";
        kind.textContent = suggestion.kind === "query" ? "" : suggestion.kind;
        option.append(icon, value, kind);
        option.addEventListener("mousedown", (event) => event.preventDefault());
        option.addEventListener("click", () => chooseAutocomplete(index));
        option.addEventListener("mousemove", () => setAutocompleteIndex(index));
        autocompleteList.appendChild(option);
      });
      autocompleteList.hidden = false;
      autocompleteInput.setAttribute("aria-expanded", "true");
      autocompleteStatus.textContent = `${autocompleteItems.length} search suggestions available.`;
    };

    const requestAutocomplete = async () => {
      const query = autocompleteInput.value.trim();
      if (query.length < 2) {
        autocompleteRequest?.abort();
        autocompleteItems = [];
        closeAutocomplete();
        return;
      }
      autocompleteRequest?.abort();
      autocompleteRequest = new AbortController();
      const sequence = ++autocompleteSequence;
      const url = new URL(autocompleteEndpoint, window.location.origin);
      url.searchParams.set("q", query);
      if (autocompleteDepartment?.value) {
        url.searchParams.set("i", autocompleteDepartment.value);
      }
      autocompleteList.setAttribute("aria-busy", "true");
      try {
        const response = await fetch(url, {
          headers: { Accept: "application/json" },
          signal: autocompleteRequest.signal,
        });
        if (!response.ok) throw new Error("suggestions unavailable");
        const payload = await response.json();
        if (sequence !== autocompleteSequence || autocompleteInput.value.trim() !== query) return;
        autocompleteList.removeAttribute("aria-busy");
        renderAutocomplete(payload.suggestions);
      } catch (error) {
        if (error?.name === "AbortError") return;
        if (sequence === autocompleteSequence) closeAutocomplete();
      }
    };

    const scheduleAutocomplete = () => {
      if (autocompleteTimer !== null) window.clearTimeout(autocompleteTimer);
      autocompleteTimer = window.setTimeout(requestAutocomplete, 120);
    };

    autocompleteInput.addEventListener("input", scheduleAutocomplete);
    autocompleteInput.addEventListener("focus", () => {
      if (autocompleteInput.value.trim().length >= 2) scheduleAutocomplete();
    });
    autocompleteDepartment?.addEventListener("change", scheduleAutocomplete);
    autocompleteInput.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") {
        if (!autocompleteItems.length) {
          scheduleAutocomplete();
          return;
        }
        event.preventDefault();
        setAutocompleteIndex(autocompleteIndex + 1);
      } else if (event.key === "ArrowUp" && autocompleteItems.length) {
        event.preventDefault();
        setAutocompleteIndex(autocompleteIndex - 1);
      } else if (event.key === "Enter" && autocompleteIndex >= 0) {
        event.preventDefault();
        chooseAutocomplete(autocompleteIndex);
      } else if (event.key === "Escape") {
        closeAutocomplete();
      } else if (event.key === "Tab") {
        closeAutocomplete();
      }
    });
    autocompleteForm.addEventListener("submit", closeAutocomplete);
    document.addEventListener("pointerdown", (event) => {
      if (!autocompleteForm.contains(event.target)) closeAutocomplete();
    });
  }

  const searchFilterPanel = document.querySelector("[data-search-filter-panel]");
  const searchFilterTriggers = Array.from(
    document.querySelectorAll("[data-search-filter-toggle]")
  );
  const searchFilterClose = document.querySelector("[data-search-filter-close]");
  const searchFilterMedia = window.matchMedia("(max-width: 700px)");
  const setSearchFiltersOpen = (open) => {
    if (!searchFilterPanel) return;
    searchFilterPanel.classList.toggle("is-open", open);
    searchFilterPanel.setAttribute("aria-hidden", open ? "false" : "true");
    searchFilterTriggers.forEach((trigger) => {
      trigger.setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.body.classList.toggle("search-filters-open", open);
    searchFilterPanel.toggleAttribute("inert", searchFilterMedia.matches && !open);
    if (open) searchFilterClose?.focus();
  };
  searchFilterTriggers.forEach((trigger) => {
    trigger.addEventListener("click", () => {
      setSearchFiltersOpen(!searchFilterPanel?.classList.contains("is-open"));
    });
  });
  searchFilterClose?.addEventListener("click", () => setSearchFiltersOpen(false));
  const syncSearchFilterMode = () => {
    if (!searchFilterPanel) return;
    if (searchFilterMedia.matches) {
      setSearchFiltersOpen(searchFilterPanel.classList.contains("is-open"));
    } else {
      searchFilterPanel.classList.remove("is-open");
      searchFilterPanel.removeAttribute("inert");
      searchFilterPanel.setAttribute("aria-hidden", "false");
      searchFilterTriggers.forEach((trigger) => trigger.setAttribute("aria-expanded", "false"));
      document.body.classList.remove("search-filters-open");
    }
  };
  searchFilterMedia.addEventListener?.("change", syncSearchFilterMode);
  syncSearchFilterMode();
  document.addEventListener("keydown", (event) => {
    const filtersOpen = searchFilterPanel?.classList.contains("is-open");
    if (event.key === "Escape" && filtersOpen) {
      setSearchFiltersOpen(false);
      searchFilterTriggers[0]?.focus();
    }
    if (event.key === "Tab" && filtersOpen && searchFilterPanel) {
      const focusable = Array.from(
        searchFilterPanel.querySelectorAll('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled])')
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });

  document.querySelectorAll('.search-filter-link[role="checkbox"]').forEach((link) => {
    link.addEventListener("keydown", (event) => {
      if (event.key === " ") {
        event.preventDefault();
        link.click();
      }
    });
  });

  document.querySelectorAll("[data-search-sort]").forEach((select) => {
    select.addEventListener("change", () => select.form?.requestSubmit());
  });

  const hero = document.querySelector("[data-home-carousel]");
  if (hero) {
    const slides = Array.from(hero.querySelectorAll("[data-home-slide]"));
    const previous = hero.querySelector("[data-home-previous]");
    const next = hero.querySelector("[data-home-next]");
    let activeSlide = Math.max(0, slides.findIndex((slide) => slide.classList.contains("is-active")));
    const showSlide = (index) => {
      activeSlide = (index + slides.length) % slides.length;
      slides.forEach((slide, slideIndex) => {
        slide.classList.toggle("is-active", slideIndex === activeSlide);
        slide.setAttribute("aria-hidden", slideIndex === activeSlide ? "false" : "true");
      });
    };
    previous?.addEventListener("click", () => showSlide(activeSlide - 1));
    next?.addEventListener("click", () => showSlide(activeSlide + 1));
    hero.addEventListener("keydown", (event) => {
      if (event.key === "ArrowLeft") showSlide(activeSlide - 1);
      if (event.key === "ArrowRight") showSlide(activeSlide + 1);
    });
    showSlide(activeSlide);
  }

  document.querySelectorAll(".home-rail").forEach((rail) => {
    const track = rail.querySelector("[data-home-rail-track]");
    const previous = rail.querySelector("[data-home-rail-previous]");
    const next = rail.querySelector("[data-home-rail-next]");
    if (!track || !previous || !next) return;

    const updateRailButtons = () => {
      previous.disabled = track.scrollLeft <= 2;
      next.disabled = track.scrollLeft + track.clientWidth >= track.scrollWidth - 2;
    };
    const scrollRail = (direction) => {
      const distance = Math.max(240, Math.floor(track.clientWidth * 0.82));
      track.scrollBy({ left: direction * distance, behavior: "smooth" });
    };

    previous.addEventListener("click", () => scrollRail(-1));
    next.addEventListener("click", () => scrollRail(1));
    track.addEventListener("scroll", updateRailButtons, { passive: true });
    window.addEventListener("resize", updateRailButtons, { passive: true });
    updateRailButtons();
  });

  document.querySelectorAll("[data-cart-recommendations]").forEach((carousel) => {
    const viewport = carousel.querySelector("[data-cart-recommendations-viewport]");
    const previous = carousel.querySelector("[data-cart-recommendations-prev]");
    const next = carousel.querySelector("[data-cart-recommendations-next]");
    const pageLabel = carousel.querySelector("[data-cart-recommendations-page]");
    if (!viewport || !previous || !next || !pageLabel) return;

    const pageSize = () => Math.max(1, viewport.clientWidth);
    const updateRecommendations = () => {
      const pages = Math.max(1, Math.ceil(viewport.scrollWidth / pageSize()));
      const page = Math.min(pages, Math.max(1, Math.round(viewport.scrollLeft / pageSize()) + 1));
      previous.disabled = viewport.scrollLeft <= 2;
      next.disabled = viewport.scrollLeft + viewport.clientWidth >= viewport.scrollWidth - 2;
      pageLabel.textContent = `Page ${page} of ${pages}`;
    };
    const scrollRecommendations = (direction) => {
      viewport.scrollBy({ left: direction * pageSize(), behavior: "smooth" });
    };

    previous.addEventListener("click", () => scrollRecommendations(-1));
    next.addEventListener("click", () => scrollRecommendations(1));
    viewport.addEventListener("keydown", (event) => {
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        scrollRecommendations(-1);
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        scrollRecommendations(1);
      }
    });
    viewport.addEventListener("scroll", updateRecommendations, { passive: true });
    window.addEventListener("resize", updateRecommendations, { passive: true });
    updateRecommendations();
  });

  const pdpGallery = document.querySelector(".pdp-gallery[data-gallery-images]");
  const pdpMainImage = document.getElementById("pdp-main-image");
  const pdpThumbnails = Array.from(document.querySelectorAll(".pdp-thumbnail[data-gallery-src]"));
  const galleryPaths = pdpGallery?.dataset.galleryImages?.split("|").filter(Boolean) ?? [];
  const galleryPrevious = document.querySelector(".mobile-gallery-prev");
  const galleryNext = document.querySelector(".mobile-gallery-next");
  const galleryIndexLabel = document.querySelector(".mobile-gallery-index");
  const fullViewDialog = document.getElementById("pdp-full-view-dialog");
  const fullViewImage = fullViewDialog?.querySelector("[data-pdp-full-view-image]");
  const fullViewTriggers = Array.from(document.querySelectorAll("[data-pdp-full-view-open]"));
  const fullViewClose = fullViewDialog?.querySelector("[data-pdp-full-view-close]");
  let galleryIndex = 0;

  const showGalleryImage = (requestedIndex) => {
    if (!pdpMainImage || !galleryPaths.length) return;
    galleryIndex = (requestedIndex + galleryPaths.length) % galleryPaths.length;
    const source = galleryPaths[galleryIndex];
    pdpMainImage.src = source;
    if (fullViewImage) fullViewImage.src = source;
    if (galleryIndexLabel) galleryIndexLabel.textContent = `${galleryIndex + 1} / ${galleryPaths.length}`;
    pdpThumbnails.forEach((item) => {
      if (item.dataset.gallerySrc === source) item.setAttribute("aria-current", "true");
      else item.removeAttribute("aria-current");
    });
  };

  if (pdpMainImage && galleryPaths.length) {
    pdpThumbnails.forEach((thumbnail) => {
      thumbnail.addEventListener("click", () => {
        const index = galleryPaths.indexOf(thumbnail.dataset.gallerySrc ?? "");
        if (index >= 0) showGalleryImage(index);
      });
    });
    galleryPrevious?.addEventListener("click", () => showGalleryImage(galleryIndex - 1));
    galleryNext?.addEventListener("click", () => showGalleryImage(galleryIndex + 1));
    pdpGallery?.addEventListener("keydown", (event) => {
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        showGalleryImage(galleryIndex - 1);
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        showGalleryImage(galleryIndex + 1);
      }
    });
    let pointerStartX = null;
    pdpGallery?.addEventListener("pointerdown", (event) => {
      if (event.pointerType !== "mouse") pointerStartX = event.clientX;
    });
    pdpGallery?.addEventListener("pointerup", (event) => {
      if (pointerStartX === null) return;
      const distance = event.clientX - pointerStartX;
      pointerStartX = null;
      if (Math.abs(distance) < 35) return;
      showGalleryImage(galleryIndex + (distance < 0 ? 1 : -1));
    });
    showGalleryImage(0);
  }

  if (fullViewDialog && fullViewTriggers.length) {
    let lastFullViewTrigger = fullViewTriggers[0];
    fullViewTriggers.forEach((trigger) => {
      trigger.addEventListener("click", () => {
        lastFullViewTrigger = trigger;
        if (pdpMainImage && fullViewImage) {
          fullViewImage.src = pdpMainImage.currentSrc || pdpMainImage.src;
          fullViewImage.alt = pdpMainImage.alt;
        }
        fullViewDialog.showModal();
      });
    });
    fullViewClose?.addEventListener("click", () => fullViewDialog.close());
    fullViewDialog.addEventListener("click", (event) => {
      if (event.target === fullViewDialog) fullViewDialog.close();
    });
    fullViewDialog.addEventListener("close", () => {
      if (lastFullViewTrigger?.isConnected) lastFullViewTrigger.focus({ preventScroll: true });
    });
  }

  const infoDialog = document.getElementById("pdp-secure-transaction-dialog");
  const infoTriggers = Array.from(document.querySelectorAll("[data-pdp-info-open]"));
  const infoClose = infoDialog?.querySelector("[data-pdp-info-close]");
  if (infoDialog && infoTriggers.length) {
    let lastInfoTrigger = infoTriggers[0];
    infoTriggers.forEach((trigger) => {
      trigger.addEventListener("click", () => {
        lastInfoTrigger = trigger;
        infoDialog.showModal();
      });
    });
    infoClose?.addEventListener("click", () => infoDialog.close());
    infoDialog.addEventListener("click", (event) => {
      if (event.target === infoDialog) infoDialog.close();
    });
    infoDialog.addEventListener("close", () => {
      if (lastInfoTrigger?.isConnected) lastInfoTrigger.focus({ preventScroll: true });
    });
  }

  const productQuoteRoot = document.querySelector("[data-product-quote-matrix]");
  const productOptionControls = Array.from(document.querySelectorAll("[data-product-option]"));
  const productOptionSelects = Array.from(
    document.querySelectorAll("[data-product-option-select]")
  );
  const parseQuoteData = (value, fallback) => {
    try {
      const parsed = JSON.parse(value ?? "");
      return parsed && typeof parsed === "object" ? parsed : fallback;
    } catch {
      return fallback;
    }
  };
  const quoteMatrix = productQuoteRoot
    ? parseQuoteData(productQuoteRoot.dataset.productQuoteMatrix, [])
    : [];
  const defaultSelectedOptions = productQuoteRoot
    ? parseQuoteData(productQuoteRoot.dataset.defaultSelectedOptions, {})
    : {};
  const unavailableSelectionCopy =
    productQuoteRoot?.dataset.optionUnavailableCopy || "No verified offer for this selection";
  const normalizedSelectionKey = normalizedProductSelectionKey;
  const productOptionAxes = Object.keys(defaultSelectedOptions);
  const quotedProductSelections = availableProductSelections(
    quoteMatrix,
    productOptionAxes
  );
  const quotedProductSelectionKeys = new Set(
    quotedProductSelections.map(normalizedSelectionKey)
  );
  const quoteBySelection = new Map(
    (Array.isArray(quoteMatrix) ? quoteMatrix : [])
      .filter(
        (quote) =>
          isAvailableProductQuote(quote) &&
          quotedProductSelectionKeys.has(normalizedSelectionKey(quote.selected_options))
      )
      .map((quote) => [normalizedSelectionKey(quote.selected_options), quote])
  );
  const productPriceTargets = Array.from(document.querySelectorAll("[data-product-price]"));
  const productAvailabilityTargets = Array.from(
    document.querySelectorAll("[data-product-availability]")
  );
  const productQuoteStatus = document.querySelector("[data-product-quote-status]");
  const productAddButtons = Array.from(
    document.querySelectorAll("[data-product-add-to-cart]")
  );
  const productCompareButtons = Array.from(
    document.querySelectorAll("[data-product-compare]")
  );
  const productBuyNowButtons = Array.from(
    document.querySelectorAll("[data-product-buy-now]")
  );
  const productCartForm = productAddButtons[0]?.closest("form") ?? cartForm;
  const strictCartForm = Boolean(
    productCartForm?.dataset.desktopAction && productCartForm?.dataset.mobileAction
  );
  let selectedProductOptions = { ...defaultSelectedOptions };

  const writeQuotedPrice = (target, priceMinor, currency) => {
    if (!Number.isInteger(priceMinor) || priceMinor < 0 || typeof currency !== "string") return;
    const whole = Math.floor(priceMinor / 100).toLocaleString("en-US");
    const cents = String(priceMinor % 100).padStart(2, "0");
    const symbol = document.createElement("span");
    symbol.className = "price-symbol";
    symbol.textContent = currency === "USD" ? "$" : `${currency} `;
    const wholePart = document.createElement("span");
    wholePart.textContent = whole;
    const centsPart = document.createElement("sup");
    centsPart.textContent = cents;
    target.replaceChildren(symbol, wholePart, centsPart);
    target.classList.remove("is-selection-unavailable");
    target.dataset.priceMinor = String(priceMinor);
    target.dataset.currency = currency;
  };

  const writeUnavailablePrice = (target) => {
    target.textContent = "—";
    target.classList.add("is-selection-unavailable");
    delete target.dataset.priceMinor;
    delete target.dataset.currency;
  };

  const syncStrictCartForm = (selection) => {
    if (!strictCartForm || !productCartForm) return;
    const isDefaultSelection =
      normalizedSelectionKey(selection) === normalizedSelectionKey(defaultSelectedOptions);
    productCartForm
      .querySelectorAll("[data-dynamic-product-option]")
      .forEach((field) => field.remove());
    if (isDefaultSelection) {
      productCartForm.setAttribute(
        "action",
        explicitMobilePdp
          ? productCartForm.dataset.mobileAction
          : productCartForm.dataset.desktopAction
      );
      return;
    }
    productCartForm.setAttribute(
      "action",
      productCartForm.dataset.genericAction || "/gp/cart/add.html"
    );
    Object.entries(selection).forEach(([label, value]) => {
      const field = document.createElement("input");
      field.type = "hidden";
      field.name = `option.${label}`;
      field.value = value;
      field.dataset.productOptionField = label;
      field.dataset.dynamicProductOption = "true";
      productCartForm.appendChild(field);
    });
  };

  const syncProductOptionControls = (selection) => {
    productOptionControls.forEach((control) => {
      const label = control.dataset.optionLabel ?? "";
      const value = control.dataset.optionValue ?? "";
      const selected = Boolean(label && value && selection[label] === value);
      control.classList.toggle("selected", selected);
      control.setAttribute("aria-pressed", selected ? "true" : "false");
    });
    productOptionSelects.forEach((control) => {
      const label = control.dataset.optionLabel ?? "";
      if (label && typeof selection[label] === "string") {
        control.value = selection[label];
      }
    });
    productOptionAxes.forEach((label) => {
      const value = selection[label];
      if (typeof value !== "string") return;
      document
        .querySelectorAll(`[data-selected-option-label="${CSS.escape(label)}"]`)
        .forEach((output) => {
          output.textContent = value;
        });
      document
        .querySelectorAll(`[data-product-option-field="${CSS.escape(label)}"]`)
        .forEach((field) => {
          field.value = value;
        });
    });
  };

  const syncProductOptionCompatibility = (selection) => {
    productOptionControls.forEach((control) => {
      const label = control.dataset.optionLabel ?? "";
      const value = control.dataset.optionValue ?? "";
      const compatible = productOptionHasCompatibleQuote(
        quotedProductSelections,
        selection,
        productOptionAxes,
        label,
        value
      );
      const selectable = productOptionHasAnyQuote(
        quotedProductSelections,
        productOptionAxes,
        label,
        value
      );
      control.disabled = !selectable;
      control.setAttribute("aria-disabled", selectable ? "false" : "true");
      control.dataset.optionCompatible = compatible ? "true" : "false";
      control.dataset.optionRequiresRepair = selectable && !compatible ? "true" : "false";
    });
    productOptionSelects.forEach((control) => {
      const label = control.dataset.optionLabel ?? "";
      let selectableCount = 0;
      Array.from(control.options).forEach((option) => {
        const compatible = productOptionHasCompatibleQuote(
          quotedProductSelections,
          selection,
          productOptionAxes,
          label,
          option.value
        );
        const selectable = productOptionHasAnyQuote(
          quotedProductSelections,
          productOptionAxes,
          label,
          option.value
        );
        option.disabled = !selectable;
        option.setAttribute("aria-disabled", selectable ? "false" : "true");
        option.dataset.optionCompatible = compatible ? "true" : "false";
        option.dataset.optionRequiresRepair = selectable && !compatible ? "true" : "false";
        if (selectable) selectableCount += 1;
      });
      control.disabled = selectableCount === 0;
      control.setAttribute("aria-disabled", control.disabled ? "true" : "false");
    });
  };

  const applyProductQuote = (selection, { updateImage = true } = {}) => {
    if (!productQuoteRoot) return;
    const quote = quoteBySelection.get(normalizedSelectionKey(selection));
    const quoteAvailable = isAvailableProductQuote(quote);
    productQuoteRoot.dataset.productQuoteAvailable = quoteAvailable ? "true" : "false";
    productQuoteRoot.classList.toggle("has-unverified-selection", !quoteAvailable);
    syncStrictCartForm(selection);

    productAddButtons.forEach((button) => {
      button.disabled = !quoteAvailable;
      button.setAttribute("aria-disabled", quoteAvailable ? "false" : "true");
    });
    productCompareButtons.forEach((button) => {
      button.disabled = !quoteAvailable;
      button.setAttribute("aria-disabled", quoteAvailable ? "false" : "true");
    });
    productBuyNowButtons.forEach((button) => {
      const mayEnable = button.dataset.quoteCanEnable === "true";
      button.disabled = !quoteAvailable || !mayEnable;
      button.setAttribute("aria-disabled", button.disabled ? "true" : "false");
    });

    if (!quoteAvailable) {
      productPriceTargets.forEach(writeUnavailablePrice);
      productAvailabilityTargets.forEach((target) => {
        target.textContent = "Unavailable";
        target.classList.add("is-selection-unavailable");
      });
      if (productQuoteStatus) {
        productQuoteStatus.textContent = unavailableSelectionCopy;
        productQuoteStatus.hidden = false;
      }
      if (productCartForm) delete productCartForm.dataset.transactionSelectionKey;
      return;
    }

    productPriceTargets.forEach((target) => {
      writeQuotedPrice(target, quote.price_minor, quote.currency);
    });
    productAvailabilityTargets.forEach((target) => {
      target.textContent = quote.display_availability;
      target.classList.remove("is-selection-unavailable");
    });
    if (productQuoteStatus) {
      productQuoteStatus.textContent = "";
      productQuoteStatus.hidden = true;
    }
    if (updateImage && pdpMainImage && typeof quote.image_path === "string") {
      pdpMainImage.src = quote.image_path;
    }
    if (productCartForm && quote.transaction_target?.selection_key) {
      productCartForm.dataset.transactionSelectionKey = quote.transaction_target.selection_key;
    }
  };

  const commitProductSelection = (selection, { updateImage = true } = {}) => {
    selectedProductOptions = { ...selection };
    syncProductOptionControls(selectedProductOptions);
    applyProductQuote(selectedProductOptions, { updateImage });
    syncProductOptionCompatibility(selectedProductOptions);
  };

  productOptionControls.forEach((control) => {
    control.addEventListener("click", () => {
      const label = control.dataset.optionLabel ?? "";
      const value = control.dataset.optionValue ?? "";
      if (
        !label ||
        !value ||
        !(label in defaultSelectedOptions) ||
        control.disabled ||
        control.getAttribute("aria-disabled") === "true"
      ) {
        return;
      }
      const repairedSelection = repairProductSelection(
        quotedProductSelections,
        selectedProductOptions,
        productOptionAxes,
        label,
        value
      );
      if (repairedSelection) {
        const capturedOptionImage = control.dataset.optionImage;
        if (pdpMainImage && capturedOptionImage) pdpMainImage.src = capturedOptionImage;
        commitProductSelection(repairedSelection);
      }
    });
  });

  productOptionSelects.forEach((control) => {
    control.addEventListener("change", () => {
      const label = control.dataset.optionLabel ?? "";
      const value = control.value ?? "";
      if (!label || !value || !(label in defaultSelectedOptions)) return;
      const repairedSelection = repairProductSelection(
        quotedProductSelections,
        selectedProductOptions,
        productOptionAxes,
        label,
        value
      );
      if (repairedSelection) {
        commitProductSelection(repairedSelection);
      } else {
        syncProductOptionControls(selectedProductOptions);
        syncProductOptionCompatibility(selectedProductOptions);
      }
    });
  });

  if (productQuoteRoot) {
    const repairedInitialSelection = repairProductSelection(
      quotedProductSelections,
      selectedProductOptions,
      productOptionAxes
    );
    if (repairedInitialSelection) {
      commitProductSelection(repairedInitialSelection, {
        updateImage:
          normalizedSelectionKey(repairedInitialSelection) !==
          normalizedSelectionKey(defaultSelectedOptions),
      });
    } else {
      syncProductOptionControls(selectedProductOptions);
      applyProductQuote(selectedProductOptions, { updateImage: false });
      syncProductOptionCompatibility(selectedProductOptions);
    }
    productCartForm?.addEventListener("submit", (event) => {
      if (productQuoteRoot.dataset.productQuoteAvailable !== "true") {
        event.preventDefault();
      }
    });
  }

  productBuyNowButtons.forEach((button) => {
    button.addEventListener("click", () => {
      if (
        button.disabled ||
        !productCartForm ||
        productQuoteRoot?.dataset.productQuoteAvailable !== "true"
      ) {
        return;
      }
      productCartForm.setAttribute("action", "/gp/buy/now");
      productCartForm.requestSubmit();
    });
  });

  const videoDialog = document.getElementById("pdp-video-dialog");
  const videoTrigger = document.querySelector("[data-video-trigger]");
  const videoClose = document.querySelector("[data-video-close]");
  videoTrigger?.addEventListener("click", () => videoDialog?.showModal());
  videoClose?.addEventListener("click", () => videoDialog?.close());
  videoDialog?.addEventListener("click", (event) => {
    if (event.target === videoDialog) videoDialog.close();
  });

  const allMenuRoot = document.querySelector("[data-all-menu-root]");
  const allMenuPanel = allMenuRoot?.querySelector("[data-all-menu-panel]");
  const allMenuClose = allMenuRoot?.querySelector("[data-all-menu-close]");
  const allMenuOverlay = allMenuRoot?.querySelector("[data-all-menu-overlay]");
  const allMenuTriggers = Array.from(document.querySelectorAll("[data-all-menu-trigger]"));
  if (allMenuRoot && allMenuPanel && allMenuClose && allMenuOverlay && allMenuTriggers.length) {
    let lastAllMenuTrigger = allMenuTriggers[0];
    const focusableElements = () => Array.from(
      allMenuPanel.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])')
    ).filter((element) => !element.hidden && element.getAttribute("aria-hidden") !== "true");
    const setAllMenuOpen = (open, trigger = null) => {
      if (open && trigger) lastAllMenuTrigger = trigger;
      allMenuRoot.classList.toggle("is-open", open);
      allMenuRoot.setAttribute("aria-hidden", open ? "false" : "true");
      document.body.classList.toggle("all-menu-open", open);
      allMenuTriggers.forEach((item) => item.setAttribute("aria-expanded", open ? "true" : "false"));
      if (open) {
        window.requestAnimationFrame(() => allMenuClose.focus({ preventScroll: true }));
      } else if (lastAllMenuTrigger?.isConnected) {
        lastAllMenuTrigger.focus({ preventScroll: true });
      }
    };
    allMenuTriggers.forEach((trigger) => {
      trigger.addEventListener("click", (event) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        setAllMenuOpen(true, trigger);
      });
    });
    allMenuClose.addEventListener("click", () => setAllMenuOpen(false));
    allMenuOverlay.addEventListener("click", () => setAllMenuOpen(false));
    allMenuPanel.addEventListener("click", (event) => {
      if (event.target.closest("a[href]")) setAllMenuOpen(false);
    });
    document.addEventListener("keydown", (event) => {
      if (!allMenuRoot.classList.contains("is-open")) return;
      if (event.key === "Escape") {
        event.preventDefault();
        setAllMenuOpen(false);
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = focusableElements();
      if (!focusable.length) {
        event.preventDefault();
        allMenuPanel.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || !allMenuPanel.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
  }

  const accountMenu = document.querySelector("[data-account-menu]");
  const accountMenuTrigger = accountMenu?.querySelector("[data-account-menu-trigger]");
  const accountMenuPanel = accountMenu?.querySelector("[data-account-menu-panel]");
  if (accountMenu && accountMenuTrigger && accountMenuPanel) {
    let closeTimer = null;
    let suppressFocusOpen = false;
    const setOpen = (open) => {
      if (closeTimer !== null) {
        window.clearTimeout(closeTimer);
        closeTimer = null;
      }
      accountMenu.classList.toggle("is-open", open);
      accountMenuTrigger.setAttribute("aria-expanded", open ? "true" : "false");
      accountMenuPanel.setAttribute("aria-hidden", open ? "false" : "true");
    };
    const scheduleClose = () => {
      if (closeTimer !== null) window.clearTimeout(closeTimer);
      closeTimer = window.setTimeout(() => setOpen(false), 120);
    };

    accountMenu.addEventListener("mouseenter", () => setOpen(true));
    accountMenu.addEventListener("mouseleave", scheduleClose);
    accountMenu.addEventListener("focusin", () => {
      if (!suppressFocusOpen) setOpen(true);
    });
    accountMenu.addEventListener("focusout", () => {
      window.setTimeout(() => {
        if (!accountMenu.contains(document.activeElement)) setOpen(false);
      }, 0);
    });
    accountMenuTrigger.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowDown") return;
      event.preventDefault();
      setOpen(true);
      accountMenuPanel.querySelector("a, button")?.focus();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !accountMenu.classList.contains("is-open")) return;
      event.preventDefault();
      suppressFocusOpen = true;
      setOpen(false);
      accountMenuTrigger.focus({ preventScroll: true });
      suppressFocusOpen = false;
    });
  }

  document.querySelectorAll('a[href="#"][data-static-placeholder]').forEach((link) => {
    link.addEventListener("click", (event) => event.preventDefault());
  });
})();

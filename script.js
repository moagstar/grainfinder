const filmRollIcon = L.divIcon({
  className: "film-roll-marker",
  iconSize: [40, 36],
  iconAnchor: [18, 31],
  popupAnchor: [3, -28],
  html: `
    <svg viewBox="0 0 40 36" role="img" aria-label="Film stockist">
      <path d="M18 12h18v7c-4.2.6-6.8 3.2-7.4 6.8H18z" fill="#30363d"/>
      <path d="M18 25.8h10.6c-.1.7-.1 1.4 0 2H18z" fill="#30363d"/>
      <rect x="21.35" y="15.35" width="2.2" height="2.7" rx="0.4" fill="#ffffff"/>
      <rect x="26.35" y="15.35" width="2.2" height="2.7" rx="0.4" fill="#ffffff"/>
      <rect x="31.35" y="15.35" width="2.2" height="2.7" rx="0.4" fill="#ffffff"/>
      <rect x="21.35" y="22.35" width="2.2" height="2.7" rx="0.4" fill="#ffffff"/>
      <rect x="26.35" y="22.35" width="2.2" height="2.7" rx="0.4" fill="#ffffff"/>
      <rect x="5" y="9" width="15" height="22" rx="1.3" fill="#15191d"/>
      <rect x="8.6" y="13" width="7.2" height="14.2" rx="1.1" fill="#f7c72f"/>
      <path d="M10.4 14h2.1v12.2h-2.1z" fill="#ffd95d" opacity="0.58"/>
      <rect x="17.2" y="12" width="1.7" height="16.2" rx="0.8" fill="#f7c72f" opacity="0.9"/>
      <rect x="8" y="4" width="8.5" height="5" rx="0.8" fill="#15191d"/>
      <path d="M4 9h17M4 31h17" fill="none" stroke="#15191d" stroke-width="3" stroke-linecap="square"/>
    </svg>
  `,
});

const map = L.map("map", {
  scrollWheelZoom: true,
}).setView([52.35591, 4.900164], 12);

L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);

const state = {
  stockists: [],
  markers: new Map(),
  activeId: null,
  mapResults: [],
  isSearchingMap: false,
  mapSearchRequestId: 0,
};

const MAP_SEARCH_MIN_LENGTH = 3;
const MAP_SEARCH_DEBOUNCE_MS = 300;
const STOCKIST_FOCUS_ZOOM = 14;
const CONFIRMATION_TYPES = {
  web: {
    label: "Search",
    modifier: "web",
    emptyTitle: "Not confirmed by web search yet",
    icon: `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <circle cx="11" cy="11" r="6.25"></circle>
        <path d="m16 16 4 4"></path>
      </svg>
    `,
  },
  contact: {
    label: "Contact",
    modifier: "contact",
    emptyTitle: "Not confirmed by phone/email yet",
    icon: `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M4.5 6.5h15v11h-15z"></path>
        <path d="m5 7 7 6 7-6"></path>
      </svg>
    `,
  },
  "in-person": {
    label: "In person",
    modifier: "in-person",
    emptyTitle: "Not confirmed in person yet",
    icon: `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M12 21s6-5.4 6-11a6 6 0 0 0-12 0c0 5.6 6 11 6 11z"></path>
        <circle cx="12" cy="10" r="2"></circle>
      </svg>
    `,
  },
};
const CONFIRMATION_TYPE_ALIASES = {
  "web-search": "web",
  phone: "contact",
  email: "contact",
  "phone-email": "contact",
  "phone/email": "contact",
  inperson: "in-person",
  "in_person": "in-person",
};
const CONFIRMATION_METHODS = ["web", "contact", "in-person"];
let mapSearchTimer = null;
let mapSearchController = null;

const statusEl = document.querySelector("#status-message");
const drawerEl = document.querySelector("#stockist-drawer");
const drawerContentEl = document.querySelector("#drawer-content");
const drawerCloseButton = document.querySelector("#drawer-close");
const searchFormEl = document.querySelector(".map-search");
const searchInputEl = document.querySelector("#stockist-search");
const searchClearButton = document.querySelector("#search-clear");
const searchResultsEl = document.querySelector("#search-results");

init();

async function init() {
  try {
    const response = await fetch("data/stockists.json");
    if (!response.ok) {
      throw new Error(`Could not load stockists.json (${response.status})`);
    }

    const stockists = await response.json();
    state.stockists = stockists.sort((a, b) => a.name.localeCompare(b.name));

    updateSearchPlaceholder();
    addMarkers(state.stockists);
    fitMapToStockists(state.stockists);
    handleSearchInput();
  } catch (error) {
    statusEl.textContent = "Could not load the stockist database.";
    console.error(error);
  }
}

function addMarkers(stockists) {
  stockists.forEach((stockist) => {
    const marker = L.marker([stockist.latitude, stockist.longitude], {
      alt: `${stockist.name} film stockist`,
      icon: filmRollIcon,
      title: stockist.name,
    })
      .addTo(map);

    marker.on("click", (event) => {
      if (event.originalEvent) {
        L.DomEvent.stopPropagation(event.originalEvent);
      }
      openStockistPanel(stockist.id);
    });
    state.markers.set(stockist.id, marker);
  });
}

function openStockistPanel(stockistId) {
  const stockist = state.stockists.find((entry) => entry.id === stockistId);
  if (!stockist) return;

  state.activeId = stockistId;
  drawerContentEl.innerHTML = getDrawerMarkup(stockist);
  drawerEl.classList.add("is-open");
  drawerEl.setAttribute("aria-hidden", "false");
  hideSearchResults();
  focusStockistOnMap(stockist);
}

function closeStockistPanel() {
  state.activeId = null;
  drawerEl.classList.remove("is-open");
  drawerEl.setAttribute("aria-hidden", "true");
  resetMarkerEmphasis();
}

function fitMapToStockists(stockists) {
  if (stockists.length === 0) return;

  const bounds = L.latLngBounds(
    stockists.map((stockist) => [stockist.latitude, stockist.longitude]),
  );
  map.fitBounds(bounds.pad(0.25), { maxZoom: 14 });
}

function handleSearchInput() {
  const query = searchInputEl.value.trim();
  searchClearButton.hidden = query.length === 0;

  if (!query) {
    cancelMapSearch();
    state.mapResults = [];
    state.isSearchingMap = false;
    hideSearchResults();
    return;
  }

  state.mapResults = [];
  state.isSearchingMap = query.length >= MAP_SEARCH_MIN_LENGTH;
  renderSearchResults(query);
  queueMapSearch(query);
}

function updateSearchPlaceholder() {
  const count = state.stockists.length;
  const noun = count === 1 ? "shop" : "shops";
  searchInputEl.placeholder = `Search ${count} ${noun} or places`;
}

function renderSearchResults(query) {
  const normalizedQuery = normalizeSearch(query);
  const stockistMatches = state.stockists
    .filter((stockist) => getSearchText(stockist).includes(normalizedQuery))
    .slice(0, 6);
  const resultGroups = [];

  if (stockistMatches.length > 0) {
    resultGroups.push(`
      <div class="search-section-label">Stockists</div>
      ${stockistMatches.map(getStockistSearchResultMarkup).join("")}
    `);
  }

  if (state.mapResults.length > 0) {
    resultGroups.push(`
      <div class="search-section-label">Map areas</div>
      ${state.mapResults.map(getMapSearchResultMarkup).join("")}
    `);
  } else if (state.isSearchingMap) {
    resultGroups.push('<div class="search-empty">Searching map areas...</div>');
  }

  if (resultGroups.length === 0) {
    const emptyMessage = query.length < MAP_SEARCH_MIN_LENGTH
      ? "No matching stockists"
      : "No matching stockists or map areas";
    resultGroups.push(`<div class="search-empty">${emptyMessage}</div>`);
  }

  searchResultsEl.innerHTML = resultGroups.join("");
  searchResultsEl.hidden = false;
  searchInputEl.setAttribute("aria-expanded", "true");
}

function getStockistSearchResultMarkup(stockist) {
  return `
    <button
      class="search-result"
      type="button"
      role="option"
      data-stockist-id="${escapeAttribute(stockist.id)}"
    >
      <strong>${escapeHtml(stockist.name)}</strong>
      <span>${escapeHtml(stockist.city)}, ${escapeHtml(stockist.country)}</span>
    </button>
  `;
}

function getMapSearchResultMarkup(result, index) {
  return `
    <button
      class="search-result"
      type="button"
      role="option"
      data-map-result-index="${index}"
    >
      <strong>${escapeHtml(result.label)}</strong>
      <span>${escapeHtml(result.meta)}</span>
    </button>
  `;
}

function queueMapSearch(query) {
  cancelMapSearch();

  if (query.length < MAP_SEARCH_MIN_LENGTH) {
    state.isSearchingMap = false;
    return;
  }

  const requestId = state.mapSearchRequestId + 1;
  state.mapSearchRequestId = requestId;
  mapSearchTimer = window.setTimeout(() => {
    searchMapAreas(query, requestId);
  }, MAP_SEARCH_DEBOUNCE_MS);
}

async function searchMapAreas(query, requestId) {
  mapSearchController = new AbortController();

  try {
    const searchUrl = new URL("https://nominatim.openstreetmap.org/search");
    searchUrl.search = new URLSearchParams({
      q: query,
      format: "jsonv2",
      addressdetails: "1",
      limit: "5",
      "accept-language": "en",
    }).toString();

    const response = await fetch(searchUrl, {
      headers: { Accept: "application/json" },
      signal: mapSearchController.signal,
    });

    if (!response.ok) {
      throw new Error(`Map search failed (${response.status})`);
    }

    const results = await response.json();
    if (requestId !== state.mapSearchRequestId) return;

    state.mapResults = results
      .map(getMapResult)
      .filter(Boolean);
  } catch (error) {
    if (error.name !== "AbortError") {
      console.error(error);
      state.mapResults = [];
    }
  } finally {
    if (requestId === state.mapSearchRequestId) {
      state.isSearchingMap = false;
      renderSearchResults(searchInputEl.value.trim());
    }
  }
}

function cancelMapSearch() {
  state.mapSearchRequestId += 1;
  window.clearTimeout(mapSearchTimer);
  mapSearchTimer = null;

  if (mapSearchController) {
    mapSearchController.abort();
    mapSearchController = null;
  }
}

function getMapResult(result) {
  const lat = Number(result.lat);
  const lon = Number(result.lon);

  if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
    return null;
  }

  return {
    label: result.display_name || result.name || "Map area",
    meta: getMapResultMeta(result),
    lat,
    lon,
    boundingBox: getBoundingBox(result.boundingbox),
  };
}

function getMapResultMeta(result) {
  const type = result.type ? result.type.replaceAll("_", " ") : "Map area";
  const country = result.address && result.address.country;

  if (country) {
    return `${toTitleCase(type)} - ${country}`;
  }

  return toTitleCase(type);
}

function getBoundingBox(value) {
  if (!Array.isArray(value) || value.length !== 4) return null;

  const [south, north, west, east] = value.map(Number);
  if (![south, north, west, east].every(Number.isFinite)) return null;

  return [[south, west], [north, east]];
}

function hideSearchResults() {
  searchResultsEl.hidden = true;
  searchResultsEl.innerHTML = "";
  searchInputEl.setAttribute("aria-expanded", "false");
}

function selectSearchResult(stockistId) {
  const stockist = state.stockists.find((entry) => entry.id === stockistId);
  if (!stockist) return;

  cancelMapSearch();
  state.mapResults = [];
  state.isSearchingMap = false;
  searchInputEl.value = stockist.name;
  searchClearButton.hidden = false;
  openStockistPanel(stockistId);
}

function selectMapResult(resultIndex) {
  const result = state.mapResults[Number(resultIndex)];
  if (!result) return;

  searchInputEl.value = result.label;
  searchClearButton.hidden = false;
  hideSearchResults();
  closeStockistPanel();

  if (result.boundingBox) {
    map.fitBounds(result.boundingBox, {
      animate: true,
      maxZoom: 12,
      padding: [48, 48],
    });
    return;
  }

  map.setView([result.lat, result.lon], 12, { animate: true });
}

function focusStockistOnMap(stockist) {
  const marker = state.markers.get(stockist.id);
  if (marker) {
    marker.addTo(map);
    resetMarkerEmphasis();
    marker.setZIndexOffset(1000);
  }

  map.setView(
    [stockist.latitude, stockist.longitude],
    Math.max(map.getZoom(), STOCKIST_FOCUS_ZOOM),
    { animate: true },
  );
}

function resetMarkerEmphasis() {
  state.markers.forEach((marker) => {
    marker.setZIndexOffset(0);
  });
}

function getSearchText(stockist) {
  return normalizeSearch([
    stockist.name,
    stockist.address,
    stockist.city,
    stockist.postalCode,
    stockist.country,
    ...stockist.stocks,
  ].join(" "));
}

function normalizeSearch(value) {
  return String(value)
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim();
}

function toTitleCase(value) {
  return String(value).replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function getDrawerMarkup(stockist) {
  return `
    <div class="drawer-title-row">
      <h2 id="drawer-title">
        <a href="${escapeAttribute(stockist.website)}" rel="noreferrer" target="_blank">
          ${escapeHtml(stockist.name)}
        </a>
      </h2>
      <a
        class="external-link-icon"
        href="${escapeAttribute(stockist.website)}"
        rel="noreferrer"
        target="_blank"
        aria-label="Open ${escapeAttribute(stockist.name)} website"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="M9 5H5.8A2.8 2.8 0 0 0 3 7.8v10.4A2.8 2.8 0 0 0 5.8 21h10.4a2.8 2.8 0 0 0 2.8-2.8V15"></path>
          <path d="M14 3h7v7"></path>
          <path d="M21 3 10 14"></path>
        </svg>
      </a>
      <a
        class="directions-icon-link"
        href="${getDirectionsUrl(stockist)}"
        rel="noreferrer"
        target="_blank"
        aria-label="Directions to ${escapeAttribute(stockist.name)}"
      >
        <svg viewBox="0 0 44 44" aria-hidden="true" focusable="false">
          <path class="maps-pin-green" d="M22 40c6.2-8 14-14.9 14-24A14 14 0 0 0 22 2v12.2a3.8 3.8 0 0 1 0 7.6z"></path>
          <path class="maps-pin-blue" d="M22 40C15.8 32 8 25.1 8 16A14 14 0 0 1 22 2v12.2a3.8 3.8 0 0 0 0 7.6z"></path>
          <path class="maps-pin-yellow" d="M22 2a14 14 0 0 1 12.2 7.1L24.6 18A3.8 3.8 0 0 0 22 14.2z"></path>
          <path class="maps-pin-red" d="M22 2A14 14 0 0 0 9.8 9.1L19.4 18a3.8 3.8 0 0 1 2.6-3.8z"></path>
          <circle class="maps-pin-center" cx="22" cy="18" r="4.4"></circle>
        </svg>
      </a>
    </div>
    <p>${escapeHtml(stockist.address)}, ${escapeHtml(stockist.postalCode)} ${escapeHtml(stockist.city)}, ${escapeHtml(stockist.country)}</p>
    <p>${escapeHtml(stockist.notes)}</p>
    <div class="meta-row">
      ${stockist.stocks.map((stock) => `<span class="tag">${escapeHtml(stock)}</span>`).join("")}
    </div>
    <div class="confirmation-group" aria-label="Confirmation methods">
      <h3>Confirmed by</h3>
      <div class="confirmation-list">
        ${CONFIRMATION_METHODS.map((type) => getConfirmationMethodMarkup(stockist, type)).join("")}
      </div>
    </div>
  `;
}

function getConfirmationMethodMarkup(stockist, type) {
  const method = CONFIRMATION_TYPES[type];
  const confirmation = getConfirmations(stockist).find((entry) => entry.type === type);
  const className = [
    "confirmation-method",
    `confirmation-method--${method.modifier}`,
    confirmation ? "is-confirmed" : "is-disabled",
  ].join(" ");

  return `
    <span
      class="${className}"
      ${confirmation ? "" : 'aria-disabled="true"'}
    >
      <span class="confirmation-icon" aria-hidden="true">${method.icon}</span>
      <span class="confirmation-text">${getConfirmationTextMarkup(confirmation, method)}</span>
    </span>
  `;
}

function getConfirmations(stockist) {
  if (Array.isArray(stockist.confirmations)) {
    return stockist.confirmations
      .map(normalizeConfirmation)
      .filter(Boolean);
  }

  return [normalizeConfirmation({
    type: stockist.confirmationType || "web",
    confirmedBy: stockist.confirmedBy,
    confirmedDate: stockist.confirmedDate,
    sourceUrl: stockist.sourceUrl,
  })].filter(Boolean);
}

function normalizeConfirmation(confirmation) {
  const type = normalizeConfirmationType(confirmation.type || "web");
  if (!CONFIRMATION_TYPES[type]) return null;

  return {
    ...confirmation,
    type,
  };
}

function normalizeConfirmationType(type) {
  const rawType = String(type).toLowerCase();
  return CONFIRMATION_TYPE_ALIASES[rawType] || rawType;
}

function getConfirmationTextMarkup(confirmation, method) {
  if (!confirmation) {
    return escapeHtml(method.emptyTitle);
  }

  const dateText = confirmation.confirmedDate
    ? ` ${formatDate(confirmation.confirmedDate)}`
    : "";

  if (confirmation.type === "in-person") {
    return `Confirmed${dateText} in person`;
  }

  const fallbackSource = confirmation.type === "contact"
    ? "phone/email"
    : CONFIRMATION_TYPES[confirmation.type].label.toLowerCase();
  const source = confirmation.confirmedBy || fallbackSource;

  if (confirmation.type === "web" && confirmation.sourceUrl) {
    return `Confirmed${dateText} via <a href="${escapeAttribute(confirmation.sourceUrl)}" rel="noreferrer" target="_blank">${escapeHtml(source)}</a>`;
  }

  return `Confirmed${dateText} via ${escapeHtml(source)}`;
}

function getDirectionsUrl(stockist) {
  const query = encodeURIComponent(`${stockist.name}, ${stockist.address}, ${stockist.postalCode} ${stockist.city}, ${stockist.country}`);
  return `https://www.google.com/maps/search/?api=1&query=${query}`;
}

function formatDate(value) {
  return new Intl.DateTimeFormat("en", {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(new Date(`${value}T00:00:00`));
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttribute(value) {
  return escapeHtml(value);
}

drawerCloseButton.addEventListener("click", closeStockistPanel);

searchInputEl.addEventListener("input", handleSearchInput);
searchInputEl.addEventListener("focus", () => {
  if (searchInputEl.value.trim()) {
    renderSearchResults(searchInputEl.value.trim());
  }
});

searchClearButton.addEventListener("click", () => {
  searchInputEl.value = "";
  searchClearButton.hidden = true;
  cancelMapSearch();
  state.mapResults = [];
  state.isSearchingMap = false;
  hideSearchResults();
  searchInputEl.focus();
});

searchResultsEl.addEventListener("click", (event) => {
  const resultButton = event.target.closest(".search-result");
  if (!resultButton) return;

  selectResultButton(resultButton);
});

searchFormEl.addEventListener("submit", (event) => {
  event.preventDefault();

  const firstResultButton = searchResultsEl.querySelector(".search-result");
  if (firstResultButton) {
    selectResultButton(firstResultButton);
    return;
  }

  handleSearchInput();
});

map.on("click", () => {
  closeStockistPanel();
  hideSearchResults();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if (!searchResultsEl.hidden) {
      hideSearchResults();
      return;
    }

    closeStockistPanel();
  }
});

function selectResultButton(resultButton) {
  if (resultButton.dataset.stockistId) {
    selectSearchResult(resultButton.dataset.stockistId);
    return;
  }

  if (resultButton.dataset.mapResultIndex) {
    selectMapResult(resultButton.dataset.mapResultIndex);
  }
}

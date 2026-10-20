const root = document.getElementById("app");
const POLL_MS = 5000;
const PAGE_SIZE = 100;
const MAX_ITEMS = 500;
const EMBED_TIMEOUT_MS = 10000;
const ID_PATTERN = /^#\/video\/([0-9a-fA-F-]{36})$/;
let pollTimer = null;
let viewToken = 0;
let pendingOpen = null;

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") {
      node.className = value;
    } else {
      node.setAttribute(key, value);
    }
  }
  for (const child of [].concat(children)) {
    node.append(child);
  }
  return node;
}

function stopPolling() {
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
  viewToken += 1;
}

function render(...nodes) {
  stopPolling();
  root.replaceChildren(...nodes);
}

function newKey() {
  if (window.crypto && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function deviceType() {
  const ua = navigator.userAgent || "";
  if (/android/i.test(ua)) {
    return "ANDROID";
  }
  if (/iphone|ipad|ipod/i.test(ua)) {
    return "IOS";
  }
  return "DESKTOP";
}

function formatDate(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("vi-VN");
}

function setMessage(node, text, isError) {
  node.className = isError ? "error" : "meta";
  node.textContent = text;
}

function sendEvents(events) {
  return apiRequest("POST", "/api/events/batch", { events }).catch(() => {});
}

function statusBadge(status) {
  return el("span", { class: `badge badge-${status.toLowerCase()}` }, STRINGS.status[status] || status);
}

function showError(message) {
  const retry = el("button", { type: "button" }, STRINGS.retry);
  retry.addEventListener("click", route);
  render(el("section", { class: "card" }, [
    el("p", { class: "error", role: "alert" }, message),
    retry,
  ]));
}

function showLogin(message) {
  const email = el("input", { type: "email", id: "email", autocomplete: "username", required: "" });
  const password = el("input", { type: "password", id: "password", autocomplete: "current-password", required: "" });
  const error = el("p", { class: "error", role: "alert" }, message || "");
  const form = el("form", { class: "card" }, [
    el("h1", {}, STRINGS.loginTitle),
    el("label", { for: "email" }, STRINGS.email),
    email,
    el("label", { for: "password" }, STRINGS.password),
    password,
    error,
    el("button", { type: "submit" }, STRINGS.loginButton),
  ]);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.textContent = "";
    try {
      const result = await supabaseClient.auth.signInWithPassword({
        email: email.value.trim(),
        password: password.value,
      });
      if (result.error) {
        error.textContent = STRINGS.loginFailed;
        return;
      }
      await route();
    } catch (exception) {
      error.textContent = STRINGS.networkError;
    }
  });
  render(form);
}

function showConsent() {
  const error = el("p", { class: "error", role: "alert" });
  const accept = el("button", { type: "button" }, STRINGS.consentAccept);
  const decline = el("button", { type: "button", class: "secondary" }, STRINGS.consentDecline);
  accept.addEventListener("click", async () => {
    accept.disabled = true;
    error.textContent = "";
    try {
      const result = await apiRequest("POST", "/api/consent", { consent_version: CONSENT_VERSION });
      if (result.status === 200 || result.status === 201) {
        await route();
        return;
      }
      error.textContent = result.status === 409 ? STRINGS.consentOutOfDate : STRINGS.consentFailed;
    } catch (exception) {
      error.textContent = STRINGS.networkError;
    }
    accept.disabled = false;
  });
  decline.addEventListener("click", async () => {
    await supabaseClient.auth.signOut();
    showLogin(STRINGS.consentDeclined);
  });
  render(el("section", { class: "card" }, [
    el("h1", {}, STRINGS.consentTitle),
    el("p", { class: "notice" }, STRINGS.consentDraftNotice),
    ...STRINGS.consentBody.map((text) => el("p", {}, text)),
    el("p", { class: "meta" }, `${STRINGS.consentVersionLabel}: ${CONSENT_VERSION}`),
    error,
    el("div", { class: "actions" }, [accept, decline]),
  ]));
}

async function fetchAllItems() {
  const items = [];
  for (let offset = 0; offset < MAX_ITEMS; offset += PAGE_SIZE) {
    const result = await apiRequest("GET", `/api/videos?limit=${PAGE_SIZE}&offset=${offset}`);
    if (!result.ok) {
      return { status: result.status, items: null };
    }
    items.push(...result.data.items);
    if (result.data.items.length < PAGE_SIZE) {
      break;
    }
  }
  return { status: 200, items };
}

function matchesQuery(item, query) {
  const platform = STRINGS.platforms[item.platform] || item.platform;
  const haystack = [item.title || "", item.url, (item.hashtags || []).join(" "), platform]
    .join(" ")
    .toLowerCase();
  return haystack.includes(query);
}

function deleteControl(id, onDeleted) {
  const holder = el("div", { class: "delete-control" });
  const ask = el("button", { type: "button", class: "secondary small danger" }, STRINGS.delete);

  function showAsk() {
    holder.replaceChildren(ask);
  }

  ask.addEventListener("click", () => {
    const message = el("p", { class: "meta", role: "status" }, STRINGS.deleteConfirm);
    const yes = el("button", { type: "button", class: "small danger-solid" }, STRINGS.deleteYes);
    const no = el("button", { type: "button", class: "secondary small" }, STRINGS.deleteCancel);
    no.addEventListener("click", showAsk);
    yes.addEventListener("click", async () => {
      yes.disabled = true;
      no.disabled = true;
      try {
        const result = await apiRequest("DELETE", `/api/videos/${id}`, {
          idempotency_key: newKey(),
          device_type: deviceType(),
        });
        if (result.status === 204 || result.status === 404) {
          onDeleted();
          return;
        }
        if (result.status === 401) {
          await route();
          return;
        }
        setMessage(message, STRINGS.genericError, true);
      } catch (exception) {
        setMessage(message, STRINGS.networkError, true);
      }
      yes.disabled = false;
      no.disabled = false;
    });
    holder.replaceChildren(message, el("div", { class: "actions-row" }, [yes, no]));
  });

  showAsk();
  return holder;
}

function showList(participant) {
  let items = [];
  let query = "";
  let lastSignature = null;

  const signOut = el("button", { type: "button", class: "secondary" }, STRINGS.signOut);
  signOut.addEventListener("click", async () => {
    await supabaseClient.auth.signOut();
    showLogin();
  });

  const urlInput = el("input", {
    type: "text",
    inputmode: "url",
    id: "save-url",
    placeholder: STRINGS.savePlaceholder,
    autocomplete: "off",
  });
  const saveButton = el("button", { type: "submit" }, STRINGS.saveButton);
  const saveMessage = el("p", { class: "meta", role: "status" });
  const saveForm = el("form", { class: "card" }, [
    el("label", { for: "save-url" }, STRINGS.saveLabel),
    el("div", { class: "row" }, [urlInput, saveButton]),
    saveMessage,
  ]);

  const searchInput = el("input", {
    type: "search",
    id: "search",
    placeholder: STRINGS.searchPlaceholder,
    autocomplete: "off",
  });
  const listMessage = el("p", { class: "meta", role: "status" });
  const list = el("ul", { class: "items" });

  render(
    el("section", { class: "card" }, [
      el("h1", {}, STRINGS.listTitle),
      el("p", { class: "meta" }, `${STRINGS.studyCodeLabel}: ${participant.study_code}`),
      signOut,
    ]),
    saveForm,
    el("section", { class: "card" }, [
      el("label", { for: "search" }, STRINGS.searchLabel),
      searchInput,
      listMessage,
      list,
    ]),
  );
  const token = viewToken;

  async function retryItem(id, button) {
    button.disabled = true;
    try {
      const result = await apiRequest("POST", `/api/videos/${id}/retry`);
      if (result.status === 401) {
        await route();
        return;
      }
      if (![200, 202, 409].includes(result.status)) {
        setMessage(listMessage, STRINGS.genericError, true);
        button.disabled = false;
        return;
      }
      setMessage(listMessage, "", false);
      await refresh();
    } catch (exception) {
      setMessage(listMessage, STRINGS.networkError, true);
      button.disabled = false;
    }
  }

  function openItem(item) {
    pendingOpen = {
      id: item.id,
      key: newKey(),
      source: query.trim() ? "SEARCH" : "CHRONOLOGICAL_LIST",
    };
    location.hash = `#/video/${item.id}`;
  }

  function itemCard(item) {
    const status = item.processing_status;
    const platform = STRINGS.platforms[item.platform] || item.platform;
    const openButton = el("button", { type: "button", class: "small" }, STRINGS.open);
    openButton.addEventListener("click", () => openItem(item));
    const actions = [openButton];
    if (status === "FAILED") {
      const retry = el("button", { type: "button", class: "secondary small" }, STRINGS.retry);
      retry.addEventListener("click", () => retryItem(item.id, retry));
      actions.push(retry);
    }
    actions.push(deleteControl(item.id, () => refresh()));
    const info = [
      el("p", { class: "item-title" }, item.title || STRINGS.untitled),
      el("p", { class: "meta" }, `${platform} · ${formatDate(item.saved_at)}`),
      statusBadge(status),
      el("div", { class: "actions-row" }, actions),
    ];
    const parts = [];
    if (item.thumbnail_url) {
      parts.push(el("img", {
        class: "thumb",
        src: item.thumbnail_url,
        alt: "",
        loading: "lazy",
        referrerpolicy: "no-referrer",
      }));
    }
    parts.push(el("div", { class: "item-info" }, info));
    return el("li", { class: "item" }, parts);
  }

  function draw() {
    const needle = query.trim().toLowerCase();
    const shown = needle ? items.filter((item) => matchesQuery(item, needle)) : items;
    if (items.length === 0) {
      list.replaceChildren(el("li", { class: "meta" }, STRINGS.emptyList));
    } else if (shown.length === 0) {
      list.replaceChildren(el("li", { class: "meta" }, STRINGS.noResults));
    } else {
      list.replaceChildren(...shown.map(itemCard));
    }
  }

  async function refresh() {
    if (pollTimer !== null) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
    let result;
    try {
      result = await fetchAllItems();
    } catch (exception) {
      if (token === viewToken) {
        setMessage(listMessage, STRINGS.networkError, true);
      }
      return;
    }
    if (token !== viewToken) {
      return;
    }
    if (result.items === null) {
      if (result.status === 401) {
        await route();
        return;
      }
      setMessage(listMessage, STRINGS.genericError, true);
      return;
    }
    setMessage(listMessage, "", false);
    const signature = JSON.stringify(result.items);
    if (signature !== lastSignature) {
      lastSignature = signature;
      items = result.items;
      draw();
    }
    if (items.some((item) => item.processing_status === "PROCESSING")) {
      pollTimer = setTimeout(refresh, POLL_MS);
    }
  }

  searchInput.addEventListener("input", () => {
    query = searchInput.value;
    draw();
  });

  saveForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const url = urlInput.value.trim();
    if (!url) {
      return;
    }
    saveButton.disabled = true;
    setMessage(saveMessage, STRINGS.saving, false);
    try {
      const result = await apiRequest("POST", "/api/videos", {
        url,
        save_source: "PASTE",
        device_type: deviceType(),
        idempotency_key: newKey(),
      });
      const detail = result.data && result.data.detail;
      if (result.status === 201 || result.status === 200) {
        urlInput.value = "";
        setMessage(saveMessage, result.status === 201 ? STRINGS.saved : STRINGS.alreadySaved, false);
        await refresh();
      } else if (result.status === 401) {
        await route();
        return;
      } else if (result.status === 422 && detail && detail.code) {
        setMessage(saveMessage, STRINGS.rejected[detail.code] || STRINGS.genericError, true);
      } else {
        setMessage(saveMessage, STRINGS.genericError, true);
      }
    } catch (exception) {
      setMessage(saveMessage, STRINGS.networkError, true);
    }
    saveButton.disabled = false;
  });

  sendEvents([{
    event_type: "DASHBOARD_VIEWED",
    idempotency_key: newKey(),
    source_block: "CHRONOLOGICAL_LIST",
    device_type: deviceType(),
  }]);
  refresh();
}

function embedUrl(item) {
  try {
    const url = new URL(item.url);
    if (item.platform === "YOUTUBE") {
      const id = url.pathname.startsWith("/shorts/")
        ? url.pathname.split("/")[2]
        : url.searchParams.get("v");
      return id && /^[A-Za-z0-9_-]{11}$/.test(id) ? `https://www.youtube.com/embed/${id}` : null;
    }
    if (item.platform === "TIKTOK") {
      const match = url.pathname.match(/\/video\/(\d+)/);
      return match ? `https://www.tiktok.com/embed/v2/${match[1]}` : null;
    }
  } catch (exception) {
    return null;
  }
  return null;
}

function backButton() {
  const back = el("button", { type: "button", class: "secondary small" }, STRINGS.back);
  back.addEventListener("click", () => {
    location.hash = "#/list";
  });
  return back;
}

function showNotFound() {
  render(el("section", { class: "card" }, [
    el("p", { class: "error", role: "alert" }, STRINGS.notFound),
    backButton(),
  ]));
}

async function showVideo(id) {
  const opened = pendingOpen && pendingOpen.id === id ? pendingOpen : null;
  pendingOpen = null;
  render(el("p", { class: "meta" }, STRINGS.loading));
  const token = viewToken;

  let result;
  try {
    result = await apiRequest("GET", `/api/videos/${id}`);
  } catch (exception) {
    showError(STRINGS.networkError);
    return;
  }
  if (token !== viewToken) {
    return;
  }
  if (result.status === 401) {
    await route();
    return;
  }
  if (result.status === 404 || result.status === 422) {
    showNotFound();
    return;
  }
  if (!result.ok) {
    showError(STRINGS.genericError);
    return;
  }

  const item = result.data;
  const origin = opened ? opened.source : "CHRONOLOGICAL_LIST";
  if (opened) {
    apiRequest("POST", `/api/videos/${id}/open`, {
      idempotency_key: opened.key,
      source_block: origin,
      device_type: deviceType(),
    }).catch(() => {});
  }

  const platform = STRINGS.platforms[item.platform] || item.platform;
  const player = el("div", { class: "player-slot" });
  const embedMessage = el("p", { class: "error", role: "status" });
  const src = embedUrl(item);
  if (src === null) {
    embedMessage.textContent = STRINGS.embedUnsupported;
  } else {
    const frame = el("iframe", {
      class: item.platform === "TIKTOK" ? "player player-tiktok" : "player",
      src,
      title: item.title || STRINGS.untitled,
      allow: "encrypted-media; picture-in-picture; fullscreen",
      allowfullscreen: "",
      referrerpolicy: "strict-origin-when-cross-origin",
    });
    let settled = false;
    const failEmbed = () => {
      if (settled || token !== viewToken) {
        return;
      }
      settled = true;
      embedMessage.textContent = STRINGS.embedFailed;
      sendEvents([{
        event_type: "EMBED_FAILED",
        idempotency_key: newKey(),
        video_id: id,
        device_type: deviceType(),
      }]);
    };
    frame.addEventListener("load", () => {
      settled = true;
    });
    frame.addEventListener("error", failEmbed);
    setTimeout(failEmbed, EMBED_TIMEOUT_MS);
    player.append(frame);
  }

  const actions = [];
  if (typeof item.url === "string" && item.url.startsWith("https://")) {
    const external = el("a", {
      class: "button-link",
      href: item.url,
      target: "_blank",
      rel: "noopener noreferrer",
    }, STRINGS.openOriginal);
    external.addEventListener("click", () => {
      apiRequest("POST", `/api/videos/${id}/open`, {
        idempotency_key: newKey(),
        source_block: origin,
        device_type: deviceType(),
        external: true,
      }).catch(() => {});
    });
    actions.push(external);
  }
  actions.push(deleteControl(id, () => {
    location.hash = "#/list";
  }));

  const details = [
    el("h1", {}, item.title || STRINGS.untitled),
    el("p", { class: "meta" }, `${platform} · ${formatDate(item.saved_at)}`),
    statusBadge(item.processing_status),
  ];
  if (item.summary) {
    details.push(el("p", { class: "meta" }, `${STRINGS.summaryLabel}: ${item.summary}`));
  }

  render(el("section", { class: "card" }, [
    backButton(),
    ...details,
    player,
    embedMessage,
    el("div", { class: "actions-row" }, actions),
  ]));
}

async function route() {
  try {
    const { data } = await supabaseClient.auth.getSession();
    if (!data.session) {
      showLogin();
      return;
    }
    render(el("p", { class: "meta" }, STRINGS.loading));
    const me = await apiRequest("GET", "/api/me");
    if (me.status === 200) {
      const match = location.hash.match(ID_PATTERN);
      if (match) {
        await showVideo(match[1].toLowerCase());
      } else {
        showList(me.data);
      }
    } else if (me.status === 403) {
      showConsent();
    } else if (me.status === 401) {
      await supabaseClient.auth.signOut();
      showLogin(STRINGS.sessionExpired);
    } else {
      showError(STRINGS.genericError);
    }
  } catch (exception) {
    showError(STRINGS.networkError);
  }
}

window.addEventListener("hashchange", route);
route();
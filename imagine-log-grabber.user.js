// ==UserScript==
// @name         Imagine log grabber
// @namespace    imagine-log
// @version      1.4.1
// @description  Save the open conversation, or walk selected saved conversations in this tab
// @updateURL    https://raw.githubusercontent.com/geekahedron/imagine-log/master/imagine-log-grabber.user.js
// @downloadURL  https://raw.githubusercontent.com/geekahedron/imagine-log/master/imagine-log-grabber.user.js
// @match        https://grok.com/imagine/*
// @grant        GM_download
// @connect      assets.grok.com
// @connect      grok.com
// @run-at       document-idle
// ==/UserScript==
(function () {
  const BLURB = /^Grok Imagine by SpaceXAI/;
  const QUEUE = "imagine-log-queue";

  function status(text) {
    const n = document.getElementById("ilg-count");
    if (n) n.textContent = text;
    const list = document.getElementById("ilg-list");
    if (list && text) list.title = text;
  }
  function wait(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }
  function postId(url) {
    const m = String(url).match(/\/(?:imagine\/post|generated)\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i);
    return m ? m[1] : "";
  }
  function promptOf(text) {
    const value = (text || "").replace(/&#10;/g, "\n").replace(/"/g, '"').replace(/&/g, "&").trim();
    return BLURB.test(value) ? "" : value;
  }
  function queue() {
    try { return JSON.parse(localStorage.getItem(QUEUE) || "null"); } catch (err) { return null; }
  }
  function saveQueue(value) {
    if (!value) localStorage.removeItem(QUEUE);
    else localStorage.setItem(QUEUE, JSON.stringify(value));
  }
  function showList(items, current) {
    const list = document.getElementById("ilg-list");
    if (!list) return;
    list.textContent = "";
    items.forEach((item, i) => {
      const row = document.createElement("div");
      row.textContent = (i === current ? "> " : "") + item.id.slice(0, 8);
      list.appendChild(row);
    });
  }
  function saveFile(url, name) {
    return new Promise(resolve => {
      if (typeof GM_download !== "function") { resolve(false); return; }
      GM_download({ url, name, saveAs: false, onload: () => resolve(true), onerror: () => resolve(false) });
    });
  }
  function stillUrl(id) {
    const img = [...document.querySelectorAll("img")].find(el => (el.currentSrc || el.src || "").includes(id) && /assets\.grok\.com/.test(el.currentSrc || el.src || ""));
    if (img) return (img.currentSrc || img.src).split("?")[0];
    const hit = [...document.querySelectorAll("img, video")].map(el => el.currentSrc || el.src || "").find(src => /assets\.grok\.com\/users\/[0-9a-f-]{36}/.test(src));
    const user = ((hit || "").match(/users\/([0-9a-f-]{36})/) || [])[1];
    if (user) return "https://assets.grok.com/users/" + user + "/generated/" + id + "/preview_image.jpg";
    return "https://grok.com/imagine/post/" + id + "/image";
  }
  function mainMedia() {
    const nodes = [...document.querySelectorAll("img, video")].filter(el => !el.closest("[data-filmstrip-item]") && !el.closest("button"));
    const hit = nodes.find(el => /\/generated\/|\/imagine\/post\/[0-9a-f-]+\/image/i.test(el.currentSrc || el.src || ""));
    if (!hit) return null;
    const url = (hit.currentSrc || hit.src).split("?")[0];
    const id = postId(url) || postId(location.href);
    const kind = hit.tagName === "VIDEO" || /generated_video|\.mp4/.test(url) ? "video" : "still";
    return { id, kind, url };
  }
  function selectedConversations() {
    const found = [];
    const seen = new Set();
    document.querySelectorAll("button[aria-pressed='true']").forEach(button => {
      let link = null;
      let node = button;
      while (node && node !== document.body) {
        link = node.querySelector("a[href*='/imagine/post/']");
        if (link) break;
        node = node.parentElement;
      }
      const href = link && link.href;
      const id = postId(href || "");
      if (!id || seen.has(id)) return;
      seen.add(id);
      found.push({ id, url: href.split("?")[0] });
    });
    return found;
  }
  async function grab() {
    const conversation = (location.href.match(/conversation=([0-9a-f-]{36})/i) || [])[1] || postId(location.href) || "page";
    const strip = [...document.querySelectorAll("button[data-filmstrip-item]")];
    const catalog = [];
    const seen = new Set();
    let n = 0;
    async function take(label) {
      status(label);
      await wait(900);
      const here = postId(location.href);
      const video = here && [...document.querySelectorAll("video")].find(el => (el.currentSrc || el.src || "").includes(here));
      const assets = [];
      if (video) assets.push({ id: here, kind: "video", url: (video.currentSrc || video.src).split("?")[0] });
      else if (here) assets.push({ id: here, kind: "still", url: stillUrl(here) });
      const seenAsset = mainMedia();
      if (seenAsset && seenAsset.id && seenAsset.id !== here) assets.push(seenAsset);
      for (const asset of assets) {
        if (!asset || seen.has(asset.id + asset.kind)) continue;
        seen.add(asset.id + asset.kind);
        const file = (asset.kind === "video" ? "grok-video-" : "grok-image-") + asset.id + (asset.kind === "video" ? ".mp4" : ".jpg");
        if (!promptsOnly && await saveFile(asset.url, file)) n += 1;
        catalog.push({
          id: file.replace(/\.[^.]+$/, ""),
          file: "media/" + file,
          kind: asset.kind,
          status: "pass",
          style: "",
          tags: [],
          prompt: promptOf((document.querySelector("meta[name='description']") || {}).content),
          notes: "",
          parent: null,
          conversation,
          date: new Date().toISOString().slice(0, 10),
          stars: 0
        });
      }
    }
    const promptsOnly = localStorage.getItem("imagine-log-prompts") === "1";
    await take("Open item");
    for (let i = 0; i < strip.length; i += 1) {
      strip[i].click();
      await take("Item " + (i + 1) + "/" + strip.length);
    }
    const videoIds = new Set(catalog.filter(row => row.kind === "video").map(row => row.id.replace(/^grok-video-/, "")));
    for (let i = catalog.length - 1; i >= 0; i -= 1) {
      const row = catalog[i];
      if (row.kind === "still" && videoIds.has(row.id.replace(/^grok-image-/, ""))) catalog.splice(i, 1);
    }
    if (!catalog.length) { status("No file opened from this conversation"); return false; }
    const body = "window.CATALOG = " + JSON.stringify(catalog, null, 2) + ";\n";
    await saveFile("data:text/plain;charset=utf-8," + encodeURIComponent(body), "catalog-" + conversation + ".txt");
    status(n + " files, catalog-" + conversation.slice(0, 8));
    return true;
  }
  function startQueue() {
    const items = selectedConversations();
    if (!items.length) { status("No selected conversations"); return; }
    saveQueue({ items, index: 0 });
    showList(items, 0);
    status("Opening 1/" + items.length);
    location.href = items[0].url;
  }
  async function continueQueue() {
    const job = queue();
    if (!job || job.index >= job.items.length) return;
    const here = postId(location.href);
    const item = job.items[job.index];
    if (!here || here !== item.id) return;
    showList(job.items, job.index);
    status("Saving " + (job.index + 1) + "/" + job.items.length);
    await wait(1200);
    await grab();
    job.index += 1;
    saveQueue(job.index >= job.items.length ? null : job);
    if (job.index >= job.items.length) {
      status("Saved " + job.items.length + " conversations");
      location.href = "https://grok.com/imagine/saved";
      return;
    }
    showList(job.items, job.index);
    status("Opening " + (job.index + 1) + "/" + job.items.length);
    location.href = job.items[job.index].url;
  }
  function panel() {
    if (document.getElementById("ilg")) return;
    const box = document.createElement("div");
    box.id = "ilg";
    box.style.cssText = "position:fixed;left:12px;bottom:12px;z-index:99999;background:#1c1a17;color:#e6e4de;border:1px solid #2c313c;border-radius:8px;padding:8px;font:13px Segoe UI,sans-serif;display:flex;gap:6px;align-items:flex-end;cursor:move";
    try {
      const saved = JSON.parse(localStorage.getItem("imagine-log-panel") || "null");
      if (saved) { box.style.left = saved.left; box.style.top = saved.top; box.style.bottom = "auto"; }
    } catch (err) {}
    box.addEventListener("mousedown", (ev) => {
      if (ev.target.closest("button")) return;
      const startX = ev.clientX;
      const startY = ev.clientY;
      const rect = box.getBoundingClientRect();
      function move(e) {
        box.style.left = Math.max(0, rect.left + e.clientX - startX) + "px";
        box.style.top = Math.max(0, rect.top + e.clientY - startY) + "px";
        box.style.bottom = "auto";
      }
      function up() {
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
        localStorage.setItem("imagine-log-panel", JSON.stringify({ left: box.style.left, top: box.style.top }));
      }
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    });
    const list = document.createElement("div");
    list.id = "ilg-list";
    list.style.cssText = "max-height:140px;overflow:auto;font-size:12px";
    const side = document.createElement("div");
    side.style.cssText = "display:flex;flex-direction:column;gap:6px";
    side.innerHTML = "<span id='ilg-count'>Ready</span><label style='font-size:12px'><input id='ilg-prompts' type='checkbox'> prompts only</label>";
    const prompts = side.querySelector("#ilg-prompts");
    prompts.checked = localStorage.getItem("imagine-log-prompts") === "1";
    prompts.onchange = () => localStorage.setItem("imagine-log-prompts", prompts.checked ? "1" : "0");
    const save = document.createElement("button");
    save.textContent = "Save this conversation";
    save.onclick = grab;
    const batch = document.createElement("button");
    batch.textContent = "Save selected";
    batch.onclick = startQueue;
    side.append(save, batch);
    box.append(list, side);
    document.documentElement.appendChild(box);
    const job = queue();
    if (job) showList(job.items, job.index);
  }
  panel();
  continueQueue();
})();

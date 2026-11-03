
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const STORAGE_KEY = "printpal.project.v2";
const QUALITY_DEFAULTS = {
  dither: "auto",
  imageDither: "floyd",
  threshold: 160,
  brightness: 0,
  contrast: 0,
  supersample: 2,
  invert: false,
  rotate180: false,
  feedLines: 4,
  maxHeight: 16000,
  density: 3,
};

const PRESETS = {
  H1: { size: 30, bold: true, font: "Bahnschrift", letterSpacing: 0.2 },
  H2: { size: 22, bold: true, font: "Segoe UI", letterSpacing: 0 },
  Body: { size: 17, bold: false, font: "Segoe UI", letterSpacing: 0 },
  Mono: { size: 14, bold: false, font: "Consolas", letterSpacing: 0 },
  Caption: { size: 12, bold: false, font: "Segoe UI", letterSpacing: 0 },
};

const LABEL_DEFAULTS = {
  kind: "list",
  list: {
    text: "Pantry\nFreezer\nSpices\nCables",
    font: "Bahnschrift",
    size: 64,
    bold: true,
    gap: 18,
  },
  shelf: {
    title: "ORGANIC PEANUT BUTTER",
    footnote: "12.99 · AISLE 4",
    font: "Bahnschrift",
    barcode: true,
    barcodeData: "",
    maxSize: 64,
    footnoteSize: 22,
  },
};

const state = {
  doc: null,
  documentDoc: null,
  mode: "document",
  labels: null,
  options: { ...QUALITY_DEFAULTS },
  fonts: [],
  settings: {},
  ports: [],
  devices: [],
  connection: { connected: false },
  printer: {},
  job: null,
  lastLogId: 0,
  projectName: "Untitled note",
  zoom: 1,
  selectedId: null,
  lastJobActive: false,
  previewSeq: 0,
  previewAbort: null,
  previewURL: null,
  previewErrorShown: false,
  pendingImageBlock: null,
  settingsTimer: null,
  saveTimer: null,
  previewTimer: null,
  uploads: 0,
};


function uid() {
  if (crypto && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return "b" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}

function clamp(value, low, high) {
  return Math.min(high, Math.max(low, Number(value)));
}

function int(value, fallback = 0) {
  const number = parseInt(value, 10);
  return Number.isFinite(number) ? number : fallback;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function button(label, className, onClick, title) {
  const node = el("button", className, label);
  node.type = "button";
  if (title) node.title = title;
  node.addEventListener("click", onClick);
  return node;
}

function option(value, label, selected) {
  const node = el("option", "", label);
  node.value = value;
  if (selected) node.selected = true;
  return node;
}

function fmtTime(ms) {
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function fmtDate() {
  return new Date().toLocaleDateString([], { day: "2-digit", month: "short", year: "numeric" });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function api(path, { method = "GET", body, signal } = {}) {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
    cache: "no-store",
  });
  const type = response.headers.get("content-type") || "";
  const payload = type.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const message = payload && typeof payload === "object" ? payload.error : payload;
    throw new Error(message || `Request failed (${response.status})`);
  }
  return payload;
}

function toast(message, kind = "info", title = "") {
  const node = el("div", `toast ${kind}`);
  if (title) node.append(el("strong", "", title));
  node.append(document.createTextNode(message));
  $("#toasts").append(node);
  setTimeout(() => {
    node.style.transition = "opacity 180ms ease, transform 180ms ease";
    node.style.opacity = "0";
    node.style.transform = "translateY(6px)";
    setTimeout(() => node.remove(), 220);
  }, kind === "error" ? 6500 : 3800);
}

function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = el("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function blobToDataURL(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

function fileToDataURL(file) {
  return blobToDataURL(file);
}


function textBlock(text = "", overrides = {}) {
  return {
    id: uid(),
    type: "text",
    text,
    font: "Segoe UI",
    size: 17,
    bold: false,
    italic: false,
    align: "left",
    lineSpacing: 1.18,
    letterSpacing: 0,
    uppercase: false,
    fitWidth: false,
    minSize: 12,
    spaceBefore: 0,
    spaceAfter: 6,
    ...overrides,
  };
}

function imageBlock(overrides = {}) {
  return {
    id: uid(),
    type: "image",
    imageId: null,
    name: "",
    fit: "width",
    maxHeight: 520,
    rotate: 0,
    align: "center",
    border: false,
    spaceBefore: 4,
    spaceAfter: 8,
    ...overrides,
  };
}

function qrBlock(overrides = {}) {
  return {
    id: uid(),
    type: "qr",
    data: "https://walkprint.net",
    size: 160,
    level: "M",
    align: "center",
    spaceBefore: 4,
    spaceAfter: 8,
    ...overrides,
  };
}

function dividerBlock(overrides = {}) {
  return { id: uid(), type: "divider", style: "solid", thickness: 2, spaceBefore: 8, spaceAfter: 8, ...overrides };
}

function spacerBlock(overrides = {}) {
  return { id: uid(), type: "spacer", height: 14, spaceBefore: 0, spaceAfter: 0, ...overrides };
}

function shelfBlock(overrides = {}) {
  return {
    id: uid(),
    type: "shelf",
    title: "",
    footnote: "",
    font: "Bahnschrift",
    bold: true,
    barcode: false,
    barcodeData: "",
    barcodeHeight: 32,
    maxLines: 2,
    maxSize: 64,
    minSize: 18,
    footnoteSize: 22,
    lineSpacing: 1.02,
    letterSpacing: 0,
    spaceBefore: 0,
    spaceAfter: 0,
    ...overrides,
  };
}

function normalizeBlock(raw) {
  const type = ["text", "image", "qr", "divider", "spacer", "shelf"].includes(raw.type)
    ? raw.type
    : "text";
  const base = {
    id: typeof raw.id === "string" ? raw.id : uid(),
    type,
    spaceBefore: clamp(int(raw.spaceBefore, 0), 0, 400),
    spaceAfter: clamp(int(raw.spaceAfter, 6), 0, 400),
  };
  if (type === "text") {
    return {
      ...textBlock(String(raw.text ?? ""), {
        font: typeof raw.font === "string" ? raw.font : "Segoe UI",
        size: clamp(int(raw.size, 17), 6, 160),
        bold: Boolean(raw.bold),
        italic: Boolean(raw.italic),
        align: ["left", "center", "right"].includes(raw.align) ? raw.align : "left",
        lineSpacing: clamp(Number(raw.lineSpacing) || 1.18, 0.7, 3),
        letterSpacing: clamp(Number(raw.letterSpacing) || 0, -2, 20),
        uppercase: Boolean(raw.uppercase),
        fitWidth: Boolean(raw.fitWidth),
        minSize: clamp(int(raw.minSize, 12), 6, 160),
      }),
      ...base,
    };
  }
  if (type === "shelf") {
    return {
      ...shelfBlock({
        title: String(raw.title ?? ""),
        footnote: String(raw.footnote ?? ""),
        font: typeof raw.font === "string" ? raw.font : "Bahnschrift",
        bold: raw.bold !== false,
        barcode: Boolean(raw.barcode),
        barcodeData: String(raw.barcodeData ?? ""),
        barcodeHeight: clamp(int(raw.barcodeHeight, 32), 4, 64),
        maxLines: clamp(int(raw.maxLines, 2), 1, 4),
        maxSize: clamp(int(raw.maxSize, 64), 8, 200),
        minSize: clamp(int(raw.minSize, 18), 8, 160),
        footnoteSize: clamp(int(raw.footnoteSize, 22), 8, 60),
      }),
      ...base,
    };
  }
  if (type === "image") {
    return {
      ...imageBlock({
        imageId: typeof raw.imageId === "string" ? raw.imageId : null,
        name: typeof raw.name === "string" ? raw.name : "",
        fit: ["width", "contain", "cover", "actual"].includes(raw.fit) ? raw.fit : "width",
        maxHeight: clamp(int(raw.maxHeight, 520), 16, 4000),
        rotate: [0, 90, 180, 270].includes(int(raw.rotate, 0)) ? int(raw.rotate, 0) : 0,
        align: ["left", "center", "right"].includes(raw.align) ? raw.align : "center",
        border: Boolean(raw.border),
      }),
      ...base,
    };
  }
  if (type === "qr") {
    return {
      ...qrBlock({
        data: String(raw.data ?? ""),
        size: clamp(int(raw.size, 160), 48, 800),
        level: ["L", "M", "Q", "H"].includes(raw.level) ? raw.level : "M",
        align: ["left", "center", "right"].includes(raw.align) ? raw.align : "center",
      }),
      ...base,
    };
  }
  if (type === "divider") {
    return {
      ...dividerBlock({
        style: ["solid", "dashed", "dotted", "double"].includes(raw.style) ? raw.style : "solid",
        thickness: clamp(int(raw.thickness, 2), 1, 16),
      }),
      ...base,
    };
  }
  return { ...spacerBlock({ height: clamp(int(raw.height, 14), 1, 800) }), ...base };
}

function normalizeDoc(raw) {
  const doc = raw && typeof raw === "object" ? raw : {};
  const blocks = Array.isArray(doc.blocks) ? doc.blocks.map(normalizeBlock) : [];
  return {
    width: clamp(int(doc.width, 384), 96, 832),
    margin: clamp(int(doc.margin, 10), 0, 80),
    blocks: blocks.length ? blocks : [textBlock()],
  };
}

function normalizeLabels(raw) {
  const source = raw && typeof raw === "object" ? raw : LABEL_DEFAULTS;
  const list = source.list && typeof source.list === "object" ? source.list : LABEL_DEFAULTS.list;
  const shelf = source.shelf && typeof source.shelf === "object" ? source.shelf : LABEL_DEFAULTS.shelf;
  return {
    kind: source.kind === "shelf" ? "shelf" : "list",
    list: {
      text: typeof list.text === "string" ? list.text : LABEL_DEFAULTS.list.text,
      font: typeof list.font === "string" ? list.font : LABEL_DEFAULTS.list.font,
      size: clamp(int(list.size, 64), 16, 120),
      bold: list.bold !== false,
      gap: clamp(int(list.gap, 18), 0, 200),
    },
    shelf: {
      title: typeof shelf.title === "string" ? shelf.title : LABEL_DEFAULTS.shelf.title,
      footnote: typeof shelf.footnote === "string" ? shelf.footnote : LABEL_DEFAULTS.shelf.footnote,
      font: typeof shelf.font === "string" ? shelf.font : LABEL_DEFAULTS.shelf.font,
      barcode: Boolean(shelf.barcode),
      barcodeData: typeof shelf.barcodeData === "string" ? shelf.barcodeData : "",
      maxSize: clamp(int(shelf.maxSize, 64), 16, 140),
      footnoteSize: clamp(int(shelf.footnoteSize, 22), 10, 40),
    },
  };
}

function normalizeOptions(raw) {
  const source = raw && typeof raw === "object" ? raw : {};
  return {
    ...QUALITY_DEFAULTS,
    dither: ["auto", "floyd", "atkinson", "bayer8", "bayer4", "threshold"].includes(source.dither)
      ? source.dither
      : "auto",
    imageDither: ["floyd", "atkinson", "bayer8", "bayer4", "threshold"].includes(source.imageDither)
      ? source.imageDither
      : "floyd",
    threshold: clamp(int(source.threshold, 160), 1, 254),
    brightness: clamp(int(source.brightness, 0), -100, 100),
    contrast: clamp(int(source.contrast, 0), -100, 100),
    supersample: int(source.supersample, 2) >= 2 ? 2 : 1,
    invert: Boolean(source.invert),
    rotate180: Boolean(source.rotate180),
    feedLines: clamp(int(source.feedLines, 4), 1, 12),
  };
}

function welcomeDoc() {
  return {
    width: 384,
    margin: 12,
    blocks: [
      textBlock("Welcome to PrintPal", {
        font: "Bahnschrift",
        size: 30,
        bold: true,
        letterSpacing: 0.2,
        spaceAfter: 3,
      }),
      textBlock("A Windows print studio for YHK / CCHW mini thermal printers.", {
        size: 14,
        spaceAfter: 12,
      }),
      dividerBlock({ thickness: 2, spaceAfter: 12 }),
      textBlock(
        "Type on the left, drop a photo anywhere, and the preview on the right is exactly what the print head will burn.",
        { size: 16, lineSpacing: 1.3, spaceAfter: 12 }
      ),
      textBlock("TIP  Press Connect to find YHK-0A73 over Bluetooth LE. No phone app needed.", {
        font: "Consolas",
        size: 12,
        lineSpacing: 1.35,
        spaceAfter: 10,
      }),
      dividerBlock({ style: "dashed", thickness: 2, spaceAfter: 8 }),
      textBlock("PrintPal · 384 dots · 58 mm", { size: 11, align: "center" }),
    ],
  };
}

function templates() {
  const today = fmtDate();
  return {
    blank: () => ({ width: 384, margin: 10, blocks: [textBlock("")] }),
    note: () => ({
      width: 384,
      margin: 12,
      blocks: [
        textBlock("Study notes", { font: "Bahnschrift", size: 30, bold: true, spaceAfter: 2 }),
        textBlock(`Subject · ${today}`, { size: 12, spaceAfter: 10 }),
        dividerBlock({ thickness: 2, spaceAfter: 10 }),
        textBlock("KEY IDEA", { size: 12, bold: true, letterSpacing: 2, spaceAfter: 4 }),
        textBlock("Write the one thing you want to remember here.", { size: 17, lineSpacing: 1.3, spaceAfter: 12 }),
        textBlock("NOTES", { size: 12, bold: true, letterSpacing: 2, spaceBefore: 2, spaceAfter: 4 }),
        textBlock("•  First point\n•  Second point\n•  Third point", { size: 15, lineSpacing: 1.45, spaceAfter: 12 }),
        dividerBlock({ style: "dashed", spaceAfter: 6 }),
        textBlock("Review on: ____________", { size: 12 }),
      ],
    }),
    todo: () => ({
      width: 384,
      margin: 12,
      blocks: [
        textBlock("To-do", { font: "Bahnschrift", size: 28, bold: true, spaceAfter: 2 }),
        textBlock(today, { size: 11, spaceAfter: 10 }),
        dividerBlock({ thickness: 2, spaceAfter: 12 }),
        textBlock("[ ]  Morning\n[ ]  Deep work block\n[ ]  Walk outside\n[ ]  Read 20 pages\n[ ]  Tidy the desk", {
          font: "Consolas",
          size: 15,
          lineSpacing: 1.6,
          spaceAfter: 12,
        }),
        dividerBlock({ style: "dotted", spaceAfter: 8 }),
        textBlock("Notes", { size: 11, bold: true, letterSpacing: 1.5, spaceAfter: 4 }),
        textBlock(" ", { size: 14 }),
      ],
    }),
    journal: () => ({
      width: 384,
      margin: 12,
      blocks: [
        textBlock(today.toUpperCase(), { font: "Consolas", size: 11, letterSpacing: 1, spaceAfter: 4 }),
        textBlock("A small record of the day", { font: "Georgia", size: 24, bold: true, spaceAfter: 10 }),
        dividerBlock({ thickness: 2, spaceAfter: 12 }),
        textBlock("What happened?", { size: 11, bold: true, letterSpacing: 1.5, spaceAfter: 4 }),
        textBlock("", { size: 15, lineSpacing: 1.6, spaceAfter: 14 }),
        textBlock("What am I grateful for?", { size: 11, bold: true, letterSpacing: 1.5, spaceAfter: 4 }),
        textBlock("", { size: 15, lineSpacing: 1.6, spaceAfter: 14 }),
        dividerBlock({ style: "dashed", spaceAfter: 6 }),
        textBlock("PrintPal journal", { size: 10, align: "center" }),
      ],
    }),
    photo: () => ({
      width: 384,
      margin: 10,
      blocks: [
        textBlock("A moment worth keeping", { font: "Bahnschrift", size: 24, bold: true, spaceAfter: 8 }),
        imageBlock({ fit: "width", spaceBefore: 2, spaceAfter: 8 }),
        textBlock("Add a caption — where, when, who.", {
          font: "Georgia",
          size: 14,
          italic: true,
          align: "center",
          spaceAfter: 10,
        }),
        textBlock(today, { size: 10, align: "center" }),
      ],
    }),
    receipt: () => ({
      width: 384,
      margin: 14,
      blocks: [
        textBlock("PRINTPAL CAFE", { font: "Consolas", size: 22, bold: true, align: "center", spaceAfter: 2 }),
        textBlock("somewhere offline", { font: "Consolas", size: 11, align: "center", spaceAfter: 8 }),
        dividerBlock({ style: "dashed", spaceAfter: 8 }),
        textBlock("1x  Flat white          3.50\n1x  Cardamom bun        4.00\n2x  Sparkling water     5.00\n\nSUBTOTAL               12.50\nCARD                   12.50", {
          font: "Consolas",
          size: 13,
          lineSpacing: 1.55,
          spaceAfter: 8,
        }),
        dividerBlock({ style: "dashed", spaceAfter: 8 }),
        textBlock(`TOTAL                  12.50\n${today}  10:42`, {
          font: "Consolas",
          size: 13,
          bold: true,
          lineSpacing: 1.5,
          spaceAfter: 10,
        }),
        textBlock("Thank you. Tip jar is by the door.", {
          font: "Consolas",
          size: 11,
          align: "center",
          spaceAfter: 8,
        }),
        qrBlock({ size: 120, spaceAfter: 0 }),
      ],
    }),
    qrcard: () => ({
      width: 384,
      margin: 14,
      blocks: [
        textBlock("Scan me", { font: "Bahnschrift", size: 26, bold: true, align: "center", spaceAfter: 4 }),
        textBlock("Point your camera here", { size: 12, align: "center", spaceAfter: 10 }),
        qrBlock({ data: "https://walkprint.net", size: 210, spaceAfter: 8 }),
        dividerBlock({ style: "dotted", spaceAfter: 8 }),
        textBlock("made with PrintPal", { font: "Consolas", size: 10, align: "center" }),
      ],
    }),
  };
}


function fontOptions(selected) {
  const families = state.fonts.length ? state.fonts.map((entry) => entry.name) : ["Segoe UI", "Consolas", "Arial"];
  if (!families.includes("Segoe UI")) families.unshift("Segoe UI");
  if (!families.includes("Consolas")) families.push("Consolas");
  return families
    .map((name) => `<option value="${name.replace(/"/g, "&quot;")}"${name === selected ? " selected" : ""}>${name}</option>`)
    .join("");
}

function autoGrow(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(340, Math.max(56, textarea.scrollHeight + 2))}px`;
}

function styleRow(block, rerender) {
  const row = el("div", "style-row");
  const font = el("select");
  font.innerHTML = fontOptions(block.font);
  font.title = "Font";
  font.addEventListener("change", () => {
    block.font = font.value;
    touch();
  });

  const size = el("input");
  size.type = "number";
  size.min = "6";
  size.max = "160";
  size.value = block.size;
  size.title = "Size in dots";
  size.addEventListener("input", () => {
    block.size = clamp(int(size.value, block.size), 6, 160);
    touch();
  });

  const bold = button("B", `toggle${block.bold ? " on" : ""}`, () => {
    block.bold = !block.bold;
    bold.classList.toggle("on", block.bold);
    touch();
  }, "Bold");
  bold.style.fontWeight = "800";

  const italic = button("I", `toggle italic${block.italic ? " on" : ""}`, () => {
    block.italic = !block.italic;
    italic.classList.toggle("on", block.italic);
    touch();
  }, "Italic");

  const align = el("div", "seg");
  for (const value of ["left", "center", "right"]) {
    const letter = value === "left" ? "L" : value === "center" ? "C" : "R";
    const item = button(letter, block.align === value ? "active" : "", () => {
      block.align = value;
      $$("button", align).forEach((node) => node.classList.toggle("active", node === item));
      touch();
    }, `${value} align`);
    align.append(item);
  }

  const presets = el("div", "presets");
  for (const [name, preset] of Object.entries(PRESETS)) {
    presets.append(
      button(name, "", () => {
        Object.assign(block, preset);
        renderBlocks();
        touch();
      }, `Apply ${name} style`)
    );
  }

  row.append(font, size, bold, italic, align, presets);
  return row;
}

function spacingDetails(block, extraFields) {
  const details = el("details", "block-details");
  details.append(el("summary", "", "Spacing & details"));
  const grid = el("div", "details-grid");
  if (extraFields) extraFields(grid);
  const before = el("input");
  before.type = "number";
  before.min = "0";
  before.max = "400";
  before.value = block.spaceBefore;
  before.addEventListener("input", () => {
    block.spaceBefore = clamp(int(before.value, 0), 0, 400);
    touch();
  });
  const after = el("input");
  after.type = "number";
  after.min = "0";
  after.max = "400";
  after.value = block.spaceAfter;
  after.addEventListener("input", () => {
    block.spaceAfter = clamp(int(after.value, 0), 0, 400);
    touch();
  });
  grid.append(field("Space before", before), field("Space after", after));
  details.append(grid);
  return details;
}

function field(label, input) {
  const wrapper = el("label", "field");
  wrapper.append(el("span", "", label), input);
  return wrapper;
}

function actionButtons(block, index) {
  const actions = el("div", "block-actions");
  actions.append(
    button("↑", "icon-btn", (event) => {
      event.stopPropagation();
      moveBlock(block.id, -1);
    }, "Move up"),
    button("↓", "icon-btn", (event) => {
      event.stopPropagation();
      moveBlock(block.id, 1);
    }, "Move down"),
    button("⧉", "icon-btn", (event) => {
      event.stopPropagation();
      duplicateBlock(block.id);
    }, "Duplicate"),
    button("×", "icon-btn danger", (event) => {
      event.stopPropagation();
      removeBlock(block.id);
    }, "Delete block")
  );
  return actions;
}

function makeCard(block, index) {
  const card = el("article", `block${state.selectedId === block.id ? " selected" : ""}`);
  card.dataset.id = block.id;

  const grip = el("div", "block-grip", "⠿");
  grip.draggable = true;
  grip.title = "Drag to reorder";
  grip.addEventListener("dragstart", (event) => {
    card.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", block.id);
    state.dragId = block.id;
  });
  grip.addEventListener("dragend", () => {
    card.classList.remove("dragging");
    state.dragId = null;
    $$(".block").forEach((node) => node.classList.remove("drop-before", "drop-after"));
  });

  const body = el("div", "block-body");
  const top = el("div", "block-top");
  top.append(el("span", "tag", block.type.toUpperCase()), actionButtons(block, index));
  body.append(top);

  if (block.type === "text") {
    body.append(textCard(block));
  } else if (block.type === "image") {
    body.append(imageCard(block));
  } else if (block.type === "qr") {
    body.append(qrCard(block));
  } else if (block.type === "divider") {
    body.append(dividerCard(block));
  } else if (block.type === "shelf") {
    body.append(shelfCard(block));
  } else {
    body.append(spacerCard(block));
  }

  card.append(grip, body);

  card.addEventListener("click", () => {
    state.selectedId = block.id;
    $$(".block").forEach((node) => node.classList.toggle("selected", node.dataset.id === block.id));
  });

  card.addEventListener("dragover", (event) => {
    if (!state.dragId || state.dragId === block.id) return;
    event.preventDefault();
    const rect = card.getBoundingClientRect();
    const before = event.clientY < rect.top + rect.height / 2;
    card.classList.toggle("drop-before", before);
    card.classList.toggle("drop-after", !before);
  });
  card.addEventListener("dragleave", () => card.classList.remove("drop-before", "drop-after"));
  card.addEventListener("drop", (event) => {
    if (!state.dragId || state.dragId === block.id) return;
    event.preventDefault();
    const rect = card.getBoundingClientRect();
    const before = event.clientY < rect.top + rect.height / 2;
    reorderBlocks(state.dragId, block.id, before);
  });

  return card;
}

function textCard(block) {
  const fragment = document.createDocumentFragment();
  const textarea = el("textarea", "text-input");
  textarea.value = block.text;
  textarea.placeholder = "Write something to print…";
  textarea.spellcheck = true;
  textarea.addEventListener("input", () => {
    block.text = textarea.value;
    autoGrow(textarea);
    touch();
  });
  textarea.addEventListener("focus", () => {
    state.selectedId = block.id;
  });
  requestAnimationFrame(() => autoGrow(textarea));
  fragment.append(textarea, styleRow(block));

  fragment.append(
    spacingDetails(block, (grid) => {
      const line = el("input");
      line.type = "number";
      line.step = "0.05";
      line.min = "0.7";
      line.max = "3";
      line.value = block.lineSpacing;
      line.addEventListener("input", () => {
        block.lineSpacing = clamp(Number(line.value) || 1.18, 0.7, 3);
        touch();
      });

      const letter = el("input");
      letter.type = "number";
      letter.step = "0.5";
      letter.min = "-2";
      letter.max = "20";
      letter.value = block.letterSpacing;
      letter.addEventListener("input", () => {
        block.letterSpacing = clamp(Number(letter.value) || 0, -2, 20);
        touch();
      });

      const uppercase = el("input");
      uppercase.type = "checkbox";
      uppercase.checked = block.uppercase;
      uppercase.addEventListener("change", () => {
        block.uppercase = uppercase.checked;
        touch();
      });
      const check = el("label", "check");
      check.append(uppercase, el("span", "", "Uppercase"));

      grid.append(field("Line height", line), field("Letter space", letter), field("", check));
    })
  );
  return fragment;
}

function imageCard(block) {
  const fragment = document.createDocumentFragment();
  const row = el("div", "image-row");

  let thumb;
  if (block.imageId) {
    thumb = el("img", "thumb");
    thumb.src = `/api/image/${block.imageId}`;
    thumb.alt = block.name || "image";
    thumb.addEventListener("error", () => {
      const missing = el("div", "thumb missing", "Image file missing");
      thumb.replaceWith(missing);
    });
  } else {
    thumb = el("div", "thumb missing", "Drop an image here");
  }

  const thumbWrap = el("div");
  thumbWrap.append(thumb);
  thumbWrap.style.flex = "0 0 82px";
  thumbWrap.addEventListener("dragover", (event) => {
    event.preventDefault();
    event.stopPropagation();
    thumb.classList.add("dropzone");
  });
  thumbWrap.addEventListener("dragleave", () => thumb.classList.remove("dropzone"));
  thumbWrap.addEventListener("drop", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    thumb.classList.remove("dropzone");
    const file = [...(event.dataTransfer.files || [])].find((item) => item.type.startsWith("image/"));
    if (file) await uploadFile(file, block.id);
  });

  const controls = el("div", "image-controls");

  const fit = el("select");
  for (const [value, label] of [
    ["width", "Fit width"],
    ["contain", "Contain"],
    ["cover", "Cover crop"],
    ["actual", "Actual size"],
  ]) {
    fit.append(option(value, label, block.fit === value));
  }
  fit.addEventListener("change", () => {
    block.fit = fit.value;
    height.disabled = !["contain", "cover"].includes(block.fit);
    touch();
  });

  const height = el("input");
  height.type = "number";
  height.min = "16";
  height.max = "4000";
  height.value = block.maxHeight;
  height.disabled = !["contain", "cover"].includes(block.fit);
  height.addEventListener("input", () => {
    block.maxHeight = clamp(int(height.value, 520), 16, 4000);
    touch();
  });

  const rotate = el("select");
  for (const value of [0, 90, 180, 270]) rotate.append(option(String(value), `${value}°`, block.rotate === value));
  rotate.addEventListener("change", () => {
    block.rotate = int(rotate.value, 0);
    touch();
  });

  const align = el("div", "seg");
  for (const value of ["left", "center", "right"]) {
    const letter = value === "left" ? "L" : value === "center" ? "C" : "R";
    const item = button(letter, block.align === value ? "active" : "", () => {
      block.align = value;
      $$("button", align).forEach((node) => node.classList.toggle("active", node === item));
      touch();
    });
    align.append(item);
  }

  const full = el("div", "full");
  full.append(field("Align", align));
  controls.append(field("Fit", fit), field("Max height", height), field("Rotate", rotate), full);

  const actions = el("div", "image-actions");
  actions.append(
    button("Replace image", "btn ghost", () => {
      state.pendingImageBlock = block.id;
      $("#image-input").click();
    }),
    button(block.border ? "Remove border" : "Add border", "btn ghost", () => {
      block.border = !block.border;
      renderBlocks();
      touch();
    })
  );

  const inner = el("div");
  inner.style.flex = "1";
  inner.style.minWidth = "0";
  inner.append(controls, actions);
  row.append(thumbWrap, inner);
  fragment.append(row);
  if (block.name) {
    const name = el("div", "printer-meta", block.name);
    name.style.marginTop = "7px";
    fragment.append(name);
  }
  fragment.append(spacingDetails(block));
  return fragment;
}

function qrCard(block) {
  const fragment = document.createDocumentFragment();
  const row = el("div", "qr-row");
  const fields = el("div", "qr-fields");

  const data = el("input");
  data.value = block.data;
  data.placeholder = "Text or URL";
  data.className = "full";
  data.addEventListener("input", () => {
    block.data = data.value;
    touch();
  });

  const size = el("input");
  size.type = "number";
  size.min = "48";
  size.max = "800";
  size.value = block.size;
  size.addEventListener("input", () => {
    block.size = clamp(int(size.value, 160), 48, 800);
    touch();
  });

  const level = el("select");
  for (const value of ["L", "M", "Q", "H"]) level.append(option(value, `${value} correction`, block.level === value));
  level.addEventListener("change", () => {
    block.level = level.value;
    touch();
  });

  const align = el("div", "seg full");
  for (const value of ["left", "center", "right"]) {
    const letter = value === "left" ? "L" : value === "center" ? "C" : "R";
    const item = button(letter, block.align === value ? "active" : "", () => {
      block.align = value;
      $$("button", align).forEach((node) => node.classList.toggle("active", node === item));
      touch();
    });
    align.append(item);
  }
  const alignField = field("Align", align);
  alignField.className = "field full";

  fields.append(field("Content", data), field("Size", size), field("Level", level), alignField);
  row.append(fields);
  fragment.append(row);
  fragment.append(spacingDetails(block));
  return fragment;
}

function dividerCard(block) {
  const fragment = document.createDocumentFragment();
  const grid = el("div", "details-grid");
  const style = el("select");
  for (const [value, label] of [
    ["solid", "Solid"],
    ["dashed", "Dashed"],
    ["dotted", "Dotted"],
    ["double", "Double"],
  ]) {
    style.append(option(value, label, block.style === value));
  }
  style.addEventListener("change", () => {
    block.style = style.value;
    touch();
  });
  const thickness = el("input");
  thickness.type = "number";
  thickness.min = "1";
  thickness.max = "16";
  thickness.value = block.thickness;
  thickness.addEventListener("input", () => {
    block.thickness = clamp(int(thickness.value, 2), 1, 16);
    touch();
  });
  grid.append(field("Style", style), field("Thickness", thickness));
  fragment.append(grid, spacingDetails(block));
  return fragment;
}

function spacerCard(block) {
  const grid = el("div", "details-grid");
  const height = el("input");
  height.type = "number";
  height.min = "1";
  height.max = "800";
  height.value = block.height;
  height.addEventListener("input", () => {
    block.height = clamp(int(height.value, 14), 1, 800);
    touch();
  });
  grid.append(field("Height (dots)", height));
  const fragment = document.createDocumentFragment();
  fragment.append(grid, spacingDetails(block));
  return fragment;
}

function shelfCard(block) {
  const fragment = document.createDocumentFragment();
  const title = el("input");
  title.value = block.title;
  title.placeholder = "Item name";
  title.addEventListener("input", () => {
    block.title = title.value;
    touch();
  });

  const footnote = el("input");
  footnote.value = block.footnote;
  footnote.placeholder = "Footnote (all caps)";
  footnote.addEventListener("input", () => {
    block.footnote = footnote.value;
    touch();
  });

  const barcode = el("label", "switch compact");
  const toggle = el("input");
  toggle.type = "checkbox";
  toggle.checked = block.barcode;
  toggle.addEventListener("change", () => {
    block.barcode = toggle.checked;
    touch();
  });
  barcode.append(toggle, el("span", "switch-track"), el("span", "", "Barcode strip"));

  const grid = el("div", "label-grid");
  grid.append(field("Item name", title), field("Footnote", footnote));
  fragment.append(grid, barcode, spacingDetails(block));
  return fragment;
}

function renderBlocks() {
  const container = $("#blocks");
  container.replaceChildren();
  state.doc.blocks.forEach((block, index) => container.append(makeCard(block, index)));
  $("#drop-hint").hidden = state.doc.blocks.length > 0;
  $("#paper").style.width = `${Math.round(state.doc.width * state.zoom)}px`;
  updateEmptyPreview();
}

function hasLabelContent() {
  if (!state.labels) return false;
  if (state.labels.kind === "shelf") {
    return Boolean(state.labels.shelf.title.trim() || state.labels.shelf.footnote.trim());
  }
  return state.labels.list.text.split(/\r?\n/).some((line) => line.trim().length > 0);
}

function updateEmptyPreview() {
  if (state.mode === "labels") {
    $("#paper-empty").hidden = hasLabelContent();
    return;
  }
  const hasContent = state.doc.blocks.some((block) => {
    if (block.type === "text") return block.text.trim().length > 0;
    if (block.type === "image") return Boolean(block.imageId);
    if (block.type === "qr") return block.data.trim().length > 0;
    if (block.type === "shelf") return Boolean(block.title?.trim() || block.footnote?.trim());
    return true;
  });
  $("#paper-empty").hidden = hasContent;
}


function labelLines() {
  return state.labels.list.text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function labelDoc() {
  const width = state.doc ? clamp(int(state.doc.width, 384), 96, 832) : 384;
  const margin = state.doc ? clamp(int(state.doc.margin, 12), 0, 80) : 12;
  const labels = state.labels;
  const blocks = [];

  if (labels.kind === "shelf") {
    const shelf = labels.shelf;
    blocks.push(
      shelfBlock({
        title: shelf.title.trim(),
        footnote: shelf.footnote.trim(),
        font: shelf.font,
        barcode: shelf.barcode,
        barcodeData: shelf.barcodeData.trim(),
        barcodeHeight: 32,
        maxLines: 2,
        maxSize: shelf.maxSize,
        minSize: 18,
        footnoteSize: shelf.footnoteSize,
      })
    );
  } else {
    const lines = labelLines();
    lines.forEach((line, index) => {
      blocks.push(
        textBlock(line, {
          font: labels.list.font,
          size: labels.list.size,
          bold: labels.list.bold,
          align: "left",
          fitWidth: true,
          minSize: 10,
          lineSpacing: 1.12,
          spaceBefore: index === 0 ? 0 : labels.list.gap,
          spaceAfter: 0,
        })
      );
    });
    if (!blocks.length) blocks.push(textBlock(""));
  }
  return { width, margin, blocks };
}

function updateLabelSummary() {
  const node = $("#label-list-summary");
  if (!node || !state.labels) return;
  const count = labelLines().length;
  if (count) {
    node.textContent = `${count} label${count === 1 ? "" : "s"} · one continuous strip`;
    node.classList.remove("warn");
  } else {
    node.textContent = "No labels yet — type one label per line";
    node.classList.add("warn");
  }
}

function populateLabelFonts() {
  for (const [id, value] of [
    ["label-list-font", state.labels.list.font],
    ["shelf-font", state.labels.shelf.font],
  ]) {
    const select = $(`#${id}`);
    if (!select) continue;
    select.innerHTML = fontOptions(value);
    select.value = value;
    if (!select.value) select.value = "Segoe UI";
  }
}

function renderLabelControls({ syncValues = true } = {}) {
  const labels = state.labels;
  $$("#label-kind button").forEach((node) =>
    node.classList.toggle("active", node.dataset.kind === labels.kind)
  );
  $("#panel-list").hidden = labels.kind !== "list";
  $("#panel-shelf").hidden = labels.kind !== "shelf";
  populateLabelFonts();
  if (syncValues) {
    $("#label-list-text").value = labels.list.text;
    $("#label-list-size").value = labels.list.size;
    $("#label-list-gap").value = labels.list.gap;
    $("#label-list-bold").checked = labels.list.bold;
    $("#shelf-title").value = labels.shelf.title;
    $("#shelf-footnote").value = labels.shelf.footnote;
    $("#shelf-barcode").checked = labels.shelf.barcode;
    $("#shelf-barcode-data").value = labels.shelf.barcodeData;
    $("#shelf-max-size").value = labels.shelf.maxSize;
    $("#shelf-footnote-size").value = labels.shelf.footnoteSize;
    autoGrow($("#label-list-text"));
  }
  updateLabelSummary();
}

function showPreviewWarnings(warnings) {
  const box = $("#preview-warnings");
  box.replaceChildren();
  if (!Array.isArray(warnings) || !warnings.length) {
    box.hidden = true;
    return;
  }
  box.append(el("strong", "", "Heads up"));
  for (const message of warnings) box.append(el("div", "", message));
  box.hidden = false;
}

function applyMode({ preview = true } = {}) {
  const labelsMode = state.mode === "labels";
  $$("#mode-switch button").forEach((node) =>
    node.classList.toggle("active", node.dataset.mode === state.mode)
  );
  $("#doc-tools").hidden = labelsMode;
  $("#template-tools").hidden = labelsMode;
  $("#label-maker").hidden = !labelsMode;
  $("#blocks").hidden = labelsMode;
  $("#drop-hint").hidden = labelsMode || state.doc.blocks.length > 0;

  const shelf = labelsMode && state.labels.kind === "shelf";
  $("#btn-print").textContent = labelsMode ? (shelf ? "Print tag" : "Print labels") : "Print page";
  $("#print-note").textContent = labelsMode
    ? "Ctrl+P to print · labels feed as one continuous strip"
    : "Ctrl+P to print · the preview is exactly what the head burns";

  if (labelsMode) {
    state.doc = labelDoc();
    renderLabelControls();
    $("#paper-empty").textContent = "Type a label to see the print preview";
  } else {
    state.doc = state.documentDoc || state.doc;
    $("#paper-empty").textContent = "Add a block to see the print preview";
    renderBlocks();
  }
  $("#paper").style.width = `${Math.round(state.doc.width * state.zoom)}px`;
  updateEmptyPreview();
  if (preview) schedulePreview(true);
}

function setMode(mode) {
  if (!["document", "labels"].includes(mode) || mode === state.mode) return;
  if (state.mode === "document") state.documentDoc = state.doc;
  state.mode = mode;
  applyMode();
  touch();
  if (mode === "labels") {
    const focusArea = state.labels.kind === "list" ? $("#label-list-text") : $("#shelf-title");
    setTimeout(() => focusArea?.focus(), 40);
  }
}


function addBlock(type) {
  const block =
    type === "image"
      ? imageBlock()
      : type === "qr"
        ? qrBlock()
        : type === "divider"
          ? dividerBlock()
          : type === "spacer"
            ? spacerBlock()
            : textBlock("");
  state.doc.blocks.push(block);
  state.selectedId = block.id;
  renderBlocks();
  touch();
  if (type === "image") {
    state.pendingImageBlock = block.id;
    $("#image-input").click();
  } else if (type === "text") {
    const textarea = $(`.block[data-id="${block.id}"] textarea`);
    if (textarea) {
      textarea.focus();
      textarea.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }
}

function removeBlock(id) {
  const index = state.doc.blocks.findIndex((block) => block.id === id);
  if (index < 0) return;
  state.doc.blocks.splice(index, 1);
  if (!state.doc.blocks.length) state.doc.blocks.push(textBlock(""));
  renderBlocks();
  touch();
}

function moveBlock(id, delta) {
  const index = state.doc.blocks.findIndex((block) => block.id === id);
  const target = index + delta;
  if (index < 0 || target < 0 || target >= state.doc.blocks.length) return;
  const [block] = state.doc.blocks.splice(index, 1);
  state.doc.blocks.splice(target, 0, block);
  renderBlocks();
  touch();
}

function duplicateBlock(id) {
  const index = state.doc.blocks.findIndex((block) => block.id === id);
  if (index < 0) return;
  const copy = normalizeBlock({ ...state.doc.blocks[index], id: uid() });
  state.doc.blocks.splice(index + 1, 0, copy);
  renderBlocks();
  touch();
}

function reorderBlocks(sourceId, targetId, before) {
  const from = state.doc.blocks.findIndex((block) => block.id === sourceId);
  if (from < 0) return;
  const [moved] = state.doc.blocks.splice(from, 1);
  let to = state.doc.blocks.findIndex((block) => block.id === targetId);
  if (to < 0) {
    state.doc.blocks.push(moved);
  } else {
    if (!before) to += 1;
    state.doc.blocks.splice(to, 0, moved);
  }
  renderBlocks();
  touch();
}


function setSaved(text) {
  $("#saved-state").textContent = text;
}

function persistLocal() {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        version: 3,
        name: state.projectName,
        mode: state.mode,
        doc: state.documentDoc || state.doc,
        labels: state.labels,
        options: state.options,
      })
    );
    setSaved("saved locally");
  } catch (error) {
    setSaved("could not save locally");
  }
}

function touch() {
  if (state.mode === "labels") {
    state.doc = labelDoc();
    updateLabelSummary();
  } else {
    state.documentDoc = state.doc;
  }
  setSaved("saving…");
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(persistLocal, 320);
  schedulePreview();
  updateEmptyPreview();
}

function restoreLocal() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return false;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return false;
    state.projectName = typeof parsed.name === "string" && parsed.name.trim() ? parsed.name : "Untitled note";
    state.mode = parsed.mode === "labels" ? "labels" : "document";
    state.labels = normalizeLabels(parsed.labels || LABEL_DEFAULTS);
    state.documentDoc = normalizeDoc(parsed.doc);
    state.doc = state.documentDoc;
    if (state.mode === "labels") state.doc = labelDoc();
    state.options = normalizeOptions({ ...state.settings.render, ...(parsed.options || {}) });
    return true;
  } catch (error) {
    return false;
  }
}

function persistSettings(patch) {
  clearTimeout(state.settingsTimer);
  state.settingsTimer = setTimeout(() => {
    api("/api/settings", { method: "POST", body: { patch } }).catch(() => {});
  }, 280);
}


function schedulePreview(immediate = false) {
  clearTimeout(state.previewTimer);
  state.previewTimer = setTimeout(refreshPreview, immediate ? 0 : 170);
}

async function refreshPreview() {
  const seq = ++state.previewSeq;
  if (state.previewAbort) state.previewAbort.abort();
  const controller = new AbortController();
  state.previewAbort = controller;
  $("#paper-loading").classList.add("show");

  try {
    const response = await fetch("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ doc: state.doc, options: state.options }),
      signal: controller.signal,
      cache: "no-store",
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || "Preview failed");
    }
    if (seq !== state.previewSeq) return;
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const image = $("#preview-img");
    image.src = url;
    image.hidden = false;
    if (state.previewURL) URL.revokeObjectURL(state.previewURL);
    state.previewURL = url;

    const width = response.headers.get("X-Paper-Width") || state.doc.width;
    const height = response.headers.get("X-Paper-Height") || "0";
    const mm = response.headers.get("X-Paper-Mm") || "0";
    const coverage = response.headers.get("X-Coverage") || "0";
    const truncated = response.headers.get("X-Truncated") === "1";
    $("#preview-stats").textContent =
      `${mm} mm · ${width}×${height} px · ${(Number(coverage) * 100).toFixed(1)}% ink` +
      (truncated ? " · truncated at max height" : "");
    let warnings = [];
    try {
      warnings = JSON.parse(decodeURIComponent(response.headers.get("X-Warnings") || "[]"));
    } catch (error) {
      warnings = [];
    }
    showPreviewWarnings(warnings);
    $("#paper-empty").hidden = Number(height) > 1;
    state.previewErrorShown = false;
  } catch (error) {
    if (error.name === "AbortError") return;
    if (!state.previewErrorShown) {
      state.previewErrorShown = true;
      toast(error.message, "error", "Preview failed");
    }
  } finally {
    if (seq === state.previewSeq) $("#paper-loading").classList.remove("show");
  }
}


async function uploadFile(file, targetBlockId = null) {
  if (!file) return;
  const isImage = file.type.startsWith("image/") || /\.(png|jpe?g|gif|webp|bmp)$/i.test(file.name);
  if (!isImage) {
    if (/\.(txt|md|markdown|csv)$/i.test(file.name)) {
      const text = await file.text();
      const block = textBlock(text, { font: "Consolas", size: 13, lineSpacing: 1.35 });
      state.doc.blocks.push(block);
      renderBlocks();
      touch();
      toast(`Added ${file.name}`);
      return;
    }
    toast(`${file.name} is not an image`, "warn");
    return;
  }
  if (file.size > 30 * 1024 * 1024) {
    toast("Image is larger than 30 MB", "error");
    return;
  }

  state.uploads += 1;
  toast(`Uploading ${file.name}…`);
  try {
    const data = await fileToDataURL(file);
    const response = await api("/api/upload", { method: "POST", body: { filename: file.name, data } });
    const image = response.image;
    if (targetBlockId) {
      const block = state.doc.blocks.find((item) => item.id === targetBlockId);
      if (block) {
        block.imageId = image.id;
        block.name = image.name;
      }
    } else {
      state.doc.blocks.push(imageBlock({ imageId: image.id, name: image.name }));
    }
    renderBlocks();
    touch();
    toast(`Added ${file.name}`, "success");
  } catch (error) {
    toast(error.message, "error", "Upload failed");
  } finally {
    state.uploads -= 1;
  }
}


async function makeSamplePhoto() {
  const canvas = document.createElement("canvas");
  canvas.width = 768;
  canvas.height = 520;
  const ctx = canvas.getContext("2d");
  const sky = ctx.createLinearGradient(0, 0, 0, 520);
  sky.addColorStop(0, "#d9d0b7");
  sky.addColorStop(0.55, "#b9b9a4");
  sky.addColorStop(1, "#6f746c");
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, 768, 520);

  ctx.fillStyle = "#f4e6c4";
  ctx.beginPath();
  ctx.arc(560, 140, 62, 0, Math.PI * 2);
  ctx.fill();

  ctx.fillStyle = "#5e665f";
  ctx.beginPath();
  ctx.moveTo(0, 420);
  ctx.lineTo(180, 250);
  ctx.lineTo(300, 370);
  ctx.lineTo(420, 225);
  ctx.lineTo(560, 390);
  ctx.lineTo(680, 290);
  ctx.lineTo(768, 400);
  ctx.lineTo(768, 520);
  ctx.lineTo(0, 520);
  ctx.closePath();
  ctx.fill();

  ctx.fillStyle = "#3c423e";
  ctx.beginPath();
  ctx.moveTo(0, 480);
  ctx.lineTo(150, 390);
  ctx.lineTo(320, 470);
  ctx.lineTo(520, 380);
  ctx.lineTo(768, 470);
  ctx.lineTo(768, 520);
  ctx.lineTo(0, 520);
  ctx.closePath();
  ctx.fill();

  ctx.strokeStyle = "rgba(255,255,255,0.35)";
  ctx.lineWidth = 2;
  for (let i = 0; i < 40; i += 1) {
    ctx.beginPath();
    ctx.arc(560, 140, 66 + i * 0.8, Math.PI * 0.9, Math.PI * 1.9);
    ctx.stroke();
  }

  return new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
}

async function applyTemplate(key) {
  if (!key) return;
  if (state.doc.blocks.some((block) => block.type === "text" && block.text.trim()) && !confirm("Replace the current document with this template?")) {
    $("#template-select").value = "";
    return;
  }
  const make = templates()[key];
  if (!make) return;

  if (key === "photo") {
    state.doc = normalizeDoc(make());
    state.projectName = "Photo card";
    $("#project-name").value = state.projectName;
    renderBlocks();
    touch();
    toast("Preparing a sample photo…");
    try {
      const blob = await makeSamplePhoto();
      const data = await blobToDataURL(blob);
      const response = await api("/api/upload", { method: "POST", body: { filename: "sample-photo.png", data } });
      const block = state.doc.blocks.find((item) => item.type === "image");
      if (block) {
        block.imageId = response.image.id;
        block.name = response.image.name;
      }
      renderBlocks();
      touch();
    } catch (error) {
      toast(error.message, "warn", "Sample photo unavailable");
    }
  } else {
    state.doc = normalizeDoc(make());
    state.projectName = key === "blank" ? "Untitled note" : $("#template-select").selectedOptions[0].textContent;
    $("#project-name").value = state.projectName;
    renderBlocks();
    touch();
  }
  $("#template-select").value = "";
  document.title = `${state.projectName} — PrintPal`;
  schedulePreview(true);
}


function connected() {
  return Boolean(state.connection && state.connection.connected);
}

function jobActive() {
  return Boolean(state.job && state.job.active);
}

async function printDocument({ test = false } = {}) {
  if (!connected()) {
    toast("Connect a printer first", "warn");
    openModal();
    return;
  }
  if (jobActive()) return;
  if (!test && state.mode === "labels" && !hasLabelContent()) {
    toast("Type a label first", "warn");
    return;
  }
  const copies = test ? 1 : clamp(int($("#copies").value, 1), 1, 20);
  $("#btn-print").classList.add("loading");
  $("#btn-print-top").classList.add("loading");
  try {
    const response = test
      ? await api("/api/test", { method: "POST", body: { options: state.options } })
      : await api("/api/print", {
          method: "POST",
          body: { doc: state.doc, options: state.options, copies },
        });
    toast(test ? "Test page sent to printer" : "Print job sent to printer", "success");
    if (response.job) updateJob(response.job);
    setTimeout(pollOnce, 250);
  } catch (error) {
    toast(error.message, "error", "Print failed");
  } finally {
    $("#btn-print").classList.remove("loading");
    $("#btn-print-top").classList.remove("loading");
  }
}

async function printCalibration() {
  if (!connected()) {
    toast("Connect a printer first", "warn");
    openModal();
    return;
  }
  if (jobActive()) return;
  $("#btn-calibration").classList.add("loading");
  try {
    const response = await api("/api/calibrate", {
      method: "POST",
      body: { options: state.options },
    });
    toast("Calibration strip sent to printer", "success");
    if (response.job) updateJob(response.job);
    setTimeout(pollOnce, 250);
  } catch (error) {
    toast(error.message, "error", "Calibration failed");
  } finally {
    $("#btn-calibration").classList.remove("loading");
  }
}

async function feedPaper() {
  if (!connected()) return;
  try {
    const response = await api("/api/feed", { method: "POST", body: { lines: state.options.feedLines } });
    toast(`Feeding ${state.options.feedLines} lines`);
    if (response.job) updateJob(response.job);
  } catch (error) {
    toast(error.message, "error", "Feed failed");
  }
}

function updateJob(job) {
  if (!job) return;
  state.job = job;
  const active = Boolean(job.active);
  const wasActive = state.lastJobActive;
  state.lastJobActive = active;

  $("#job").hidden = !active && !job.error;
  $("#job-msg").textContent = job.message || (active ? "Working…" : "Ready");
  $("#job-bar").style.width = `${clamp(job.percent || 0, 0, 100)}%`;
  $("#btn-cancel").hidden = !active;

  if (job.phase === "error" && job.error) {
    $("#job-msg").textContent = job.error;
    if (wasActive) toast(job.error, "error", "Print failed");
    setTimeout(() => {
      if (!state.job.active) $("#job").hidden = true;
    }, 5000);
  }
  if (!active && wasActive) {
    if (job.phase === "done") toast(job.message || "Print complete", "success");
    if (job.phase === "cancelled") toast("Print cancelled", "warn");
    api("/api/status", { method: "POST" })
      .then((response) => {
        state.printer = response.printer || {};
        updatePrinterUI();
      })
      .catch(() => {});
    setTimeout(() => {
      if (!state.job.active) $("#job").hidden = true;
    }, 2600);
  }

  updateButtons();
}


function updateButtons() {
  const ready = connected();
  const busy = jobActive();
  $("#btn-test").disabled = !ready || busy;
  $("#btn-calibration").disabled = !ready || busy;
  $("#btn-feed").disabled = !ready || busy;
  $("#btn-modal-test").disabled = !ready || busy;
  $("#btn-print").disabled = !ready || busy;
  $("#btn-print-top").disabled = !ready || busy;

  const device = state.connection.kind === "serial" ? state.connection.port : state.connection.name;
  if (ready && device) {
    $("#device-pill-text").textContent = device;
  } else if (ready) {
    $("#device-pill-text").textContent = "Printer";
  } else {
    $("#device-pill-text").textContent = "No printer";
  }
  $("#device-pill").classList.toggle("connected", ready);
  $("#conn-dot").className = `dot ${busy && ready ? "busy" : ready ? "connected" : state.job?.phase === "error" ? "error" : ""}`;
  $("#btn-connect").textContent = ready ? "Manage" : "Connect";
  $("#btn-disconnect").disabled = !ready;
  $("#modal-status").textContent = ready
    ? `Connected · ${state.connection.protocol || "escpos"} · ${device || ""}`
    : "Not connected";
}

function updateConnectionUI(connection, printer) {
  state.connection = connection || { connected: false };
  if (printer !== undefined) state.printer = printer || {};
  updatePrinterUI();
  updateButtons();
  updateDeviceDump();
}

function updatePrinterUI() {
  const conn = state.connection || {};
  const isConnected = Boolean(conn.connected);
  const status = (state.printer && state.printer.status) || {};
  const name = conn.name || (conn.port ? `Serial ${conn.port}` : "No printer connected");
  $("#printer-name").textContent = isConnected ? name : "No printer connected";
  if (isConnected) {
    const bits = [];
    if (conn.kind === "ble") bits.push(conn.address || "");
    else bits.push(conn.port || "");
    bits.push((conn.protocol || "escpos") === "cat" ? "AE30 cat protocol" : "ESC/POS raster");
    if (conn.mtu) bits.push(`MTU ${conn.mtu}`);
    $("#printer-meta").textContent = bits.filter(Boolean).join(" · ");
    $("#protocol-pill").hidden = false;
    $("#protocol-pill").textContent = (conn.protocol || "escpos").toUpperCase();
  } else {
    $("#printer-meta").textContent = "YHK / CCHW · Bluetooth LE or COM port";
    $("#protocol-pill").hidden = true;
  }

  const voltage = status.voltage_mv;
  if (isConnected && voltage) {
    const percent = Number(status.battery ?? 0);
    $("#battery").hidden = false;
    $("#battery-label").textContent = `${percent}%`;
    $("#battery-fill").style.width = `${percent}%`;
    const parts = [`${(voltage / 1000).toFixed(2)} V`];
    if (state.printer.serial) parts.push(state.printer.serial);
    $("#battery-sub").textContent = parts.join(" · ");
  } else {
    $("#battery").hidden = true;
  }

  if (isConnected) {
    state.connection.name = name;
  }
  updateButtons();
}

function updateDeviceDump() {
  const conn = state.connection || {};
  const printer = state.printer || {};
  if (!conn.connected) {
    $("#device-dump").textContent = "No device information.";
    return;
  }
  const lines = [];
  lines.push(`Protocol : ${conn.protocol || "escpos"}`);
  if (conn.kind === "ble") {
    lines.push(`Address  : ${conn.address}`);
    if (conn.mtu) lines.push(`MTU      : ${conn.mtu} bytes`);
  } else {
    lines.push(`Port     : ${conn.port}`);
  }
  if (conn.write_uuid) lines.push(`Write    : ${conn.write_uuid}`);
  if (conn.notify_uuid) lines.push(`Notify   : ${conn.notify_uuid}`);
  const status = printer.status || {};
  if (printer.serial) lines.push(`Serial   : ${printer.serial}`);
  if (printer.model) lines.push(`Public ID: ${printer.model}`);
  if (status.HV || status.SV) lines.push(`Firmware : HV=${status.HV || "?"}  SV=${status.SV || "?"}`);
  if (status.voltage_mv) lines.push(`Battery  : ${(status.voltage_mv / 1000).toFixed(2)} V (${status.battery ?? "?"}%)`);
  if (status.dots) lines.push(`Head     : ${status.dots} dots`);
  if (Array.isArray(conn.services) && conn.services.length) {
    lines.push("");
    lines.push("GATT services:");
    for (const service of conn.services) {
      lines.push(`  ${service.uuid}`);
      for (const char of service.characteristics || []) {
        lines.push(`    ${char.uuid}  [${(char.properties || []).join(", ")}]`);
      }
    }
  }
  $("#device-dump").textContent = lines.join("\n");
  if (conn.address) $("#adv-address").value = conn.address;
}

function openModal() {
  $("#connect-modal").hidden = false;
  updateDeviceDump();
  refreshPorts();
  if (!state.devices.length) scanDevices();
}

function closeModal() {
  $("#connect-modal").hidden = true;
}

async function scanDevices() {
  $("#btn-scan").classList.add("loading");
  $("#btn-scan").textContent = "Scanning…";
  $("#scan-status").textContent = "Looking for YHK / CCHW printers…";
  try {
    const response = await api("/api/scan", { method: "POST", body: { timeout: 7 } });
    state.devices = response.devices || [];
    renderDevices();
  } catch (error) {
    $("#scan-status").textContent = "Scan failed";
    toast(error.message, "error", "Bluetooth scan failed");
  } finally {
    $("#btn-scan").classList.remove("loading");
    $("#btn-scan").textContent = "Scan for printers";
  }
}

function renderDevices() {
  const list = $("#ble-list");
  list.replaceChildren();
  if (!state.devices.length) {
    list.append(el("p", "empty-list", "No Bluetooth devices found. Power the printer on and press Scan again."));
    $("#scan-status").textContent = "No devices found";
    return;
  }
  const likely = state.devices.filter((device) => device.likely).length;
  $("#scan-status").textContent = `${state.devices.length} device(s) · ${likely} likely printer(s)`;

  for (const device of state.devices) {
    const row = el("button", "device");
    row.type = "button";
    const main = el("div", "dev-main");
    const name = el("div", "dev-name");
    name.append(el("span", "", device.name));
    if (device.likely) name.append(el("span", "badge", "printer"));
    const meta = el("div", "dev-meta", `${device.address} · ${device.rssi ?? "?"} dBm`);
    main.append(name, meta);

    const bars = el("div", "rssi");
    const level = device.rssi > -60 ? 4 : device.rssi > -70 ? 3 : device.rssi > -80 ? 2 : 1;
    for (let i = 0; i < 4; i += 1) bars.append(el("i", i < level ? "on" : ""));

    row.append(main, bars, el("span", "dev-action", "Connect"));
    row.addEventListener("click", () => connectBle(device));
    list.append(row);
  }
}

async function refreshPorts() {
  try {
    const response = await api("/api/ports");
    state.ports = response.ports || [];
    renderPorts();
  } catch (error) {
  }
}

function renderPorts() {
  const list = $("#port-list");
  list.replaceChildren();
  if (!state.ports.length) {
    list.append(el("p", "empty-list", "No COM ports detected. Pair the printer in Windows Settings first."));
    $("#port-status").textContent = "No ports";
    return;
  }
  const bluetooth = state.ports.filter((port) => port.bluetooth).length;
  $("#port-status").textContent = `${state.ports.length} port(s) · ${bluetooth} Bluetooth`;
  for (const port of state.ports) {
    const row = el("button", "device");
    row.type = "button";
    const main = el("div", "dev-main");
    const name = el("div", "dev-name");
    name.append(el("span", "", port.port));
    if (port.bluetooth) name.append(el("span", "badge blue", "bluetooth"));
    main.append(name, el("div", "dev-meta", port.description || port.hwid || ""));
    row.append(main, el("span", "dev-action", "Connect"));
    row.addEventListener("click", () => connectSerial(port.port));
    list.append(row);
  }
}

async function connectBle(device) {
  await connect({ kind: "ble", address: device.address, name: device.name }, `Connecting to ${device.name}…`);
}

async function connectSerial(port) {
  await connect(
    { kind: "serial", port, baud: clamp(int($("#baud").value, 115200), 1200, 921600) },
    `Opening ${port}…`
  );
}

async function connect(payload, message) {
  $("#modal-status").textContent = message;
  $("#btn-scan").classList.add("loading");
  $("#btn-ports").classList.add("loading");
  try {
    const response = await api("/api/connect", { method: "POST", body: payload });
    updateConnectionUI(response.connection, response.printer);
    toast(`Connected to ${response.connection.name || payload.port}`, "success");
    closeModal();
    refreshPreview();
  } catch (error) {
    $("#modal-status").textContent = error.message;
    toast(error.message, "error", "Connection failed");
  } finally {
    $("#btn-scan").classList.remove("loading");
    $("#btn-ports").classList.remove("loading");
  }
}

async function disconnectPrinter() {
  try {
    const response = await api("/api/disconnect", { method: "POST" });
    updateConnectionUI(response.connection, {});
    toast("Printer disconnected");
  } catch (error) {
    toast(error.message, "error");
  }
}


async function pollOnce() {
  try {
    const data = await api(`/api/state?after=${state.lastLogId}`);
    if (data.connection) updateConnectionUI(data.connection, data.printer);
    if (data.job) updateJob(data.job);
    if (data.logs && data.logs.length) appendLogs(data.logs);
    if (data.settings) state.settings = data.settings;
  } catch (error) {
  }
}

async function pollLoop() {
  await pollOnce();
  setTimeout(pollLoop, connected() ? 800 : 1400);
}


function appendLogs(entries) {
  const log = $("#log");
  const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
  for (const entry of entries) {
    if (entry.id <= state.lastLogId) continue;
    state.lastLogId = Math.max(state.lastLogId, entry.id);
    const line = el("div", `log-line ${entry.level || ""}`);
    line.append(el("span", "log-time", fmtTime(entry.ts)));
    line.append(el("span", "log-msg", entry.message));
    log.append(line);
  }
  while (log.children.length > 260) log.firstElementChild.remove();
  if (stick) log.scrollTop = log.scrollHeight;
}


function optionsFromControls() {
  return {
    ...state.options,
    dither: $("#opt-dither").value,
    threshold: clamp(int($("#opt-threshold").value, 160), 1, 254),
    brightness: clamp(int($("#opt-brightness").value, 0), -100, 100),
    contrast: clamp(int($("#opt-contrast").value, 0), -100, 100),
    supersample: int($("#opt-supersample").value, 2),
    invert: $("#opt-invert").checked,
    rotate180: $("#opt-rotate").checked,
    feedLines: clamp(int($("#opt-feed").value, 4), 1, 12),
  };
}

function applyDocSize() {
  state.doc.width = clamp(int($("#opt-width").value, 384), 96, 832);
  state.doc.margin = clamp(int($("#opt-margin").value, 10), 0, 80);
  $("#paper").style.width = `${Math.round(state.doc.width * state.zoom)}px`;
}

function applyQualityControls() {
  const options = state.options;
  $("#opt-dither").value = options.dither;
  $("#opt-threshold").value = options.threshold;
  $("#out-threshold").value = options.threshold;
  $("#opt-brightness").value = options.brightness;
  $("#out-brightness").value = options.brightness;
  $("#opt-contrast").value = options.contrast;
  $("#out-contrast").value = options.contrast;
  $("#opt-supersample").value = String(options.supersample);
  $("#opt-invert").checked = Boolean(options.invert);
  $("#opt-rotate").checked = Boolean(options.rotate180);
  $("#opt-feed").value = options.feedLines;
  $("#opt-width").value = String(state.doc.width);
  $("#opt-margin").value = state.doc.margin;
  $("#paper").style.width = `${Math.round(state.doc.width * state.zoom)}px`;
}

function qualityChanged({ next = false } = {}) {
  state.options = optionsFromControls();
  applyDocSize();
  persistSettings({ render: state.options });
  schedulePreview(next);
  updateEmptyPreview();
}


async function saveProject() {
  try {
    const images = {};
    for (const block of (state.documentDoc || state.doc).blocks) {
      if (block.type !== "image" || !block.imageId || images[block.imageId]) continue;
      const response = await fetch(`/api/image/${block.imageId}`);
      if (!response.ok) continue;
      const blob = await response.blob();
      images[block.imageId] = await blobToDataURL(blob);
    }
    const payload = {
      app: "PrintPal",
      version: 3,
      name: state.projectName,
      mode: state.mode,
      doc: state.documentDoc || state.doc,
      labels: state.labels,
      options: state.options,
      images,
    };
    download(
      new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }),
      `${state.projectName.replace(/[^\w\-]+/g, "-").toLowerCase() || "printpal"}.printpal.json`
    );
    toast("Project saved", "success");
  } catch (error) {
    toast(error.message, "error", "Could not save project");
  }
}

async function openProject(file) {
  if (!file) return;
  try {
    const text = await file.text();
    const payload = JSON.parse(text);
    if (!payload || typeof payload !== "object" || !payload.doc) throw new Error("Not a PrintPal project");

    const idMap = new Map();
    const images = payload.images || {};
    const entries = Object.entries(images);
    if (entries.length) toast(`Restoring ${entries.length} image(s)…`);
    for (const [oldId, dataUrl] of entries) {
      const response = await api("/api/upload", {
        method: "POST",
        body: { filename: `restored-${oldId}.png`, data: dataUrl },
      });
      idMap.set(oldId, response.image.id);
    }

    state.documentDoc = normalizeDoc(payload.doc);
    for (const block of state.documentDoc.blocks) {
      if (block.type === "image" && block.imageId && idMap.has(block.imageId)) {
        block.imageId = idMap.get(block.imageId);
      }
    }
    state.labels = normalizeLabels(payload.labels || LABEL_DEFAULTS);
    state.mode = payload.mode === "labels" ? "labels" : "document";
    state.doc = state.documentDoc;
    state.options = normalizeOptions({ ...state.settings.render, ...(payload.options || {}) });
    state.projectName = payload.name || file.name.replace(/\.printpal\.json$|\.json$/i, "");
    $("#project-name").value = state.projectName;
    document.title = `${state.projectName} — PrintPal`;
    applyQualityControls();
    applyMode({ preview: false });
    touch();
    schedulePreview(true);
    toast("Project opened", "success");
  } catch (error) {
    toast(error.message, "error", "Could not open project");
  }
}

function newProject() {
  if (!confirm("Start a new document? The current one is saved in this browser until you replace it.")) return;
  state.mode = "document";
  state.documentDoc = welcomeDoc();
  state.doc = state.documentDoc;
  state.labels = normalizeLabels(LABEL_DEFAULTS);
  state.options = { ...QUALITY_DEFAULTS };
  state.projectName = "Untitled note";
  $("#project-name").value = state.projectName;
  document.title = "Untitled note — PrintPal";
  applyQualityControls();
  applyMode({ preview: false });
  touch();
  schedulePreview(true);
}


function wireEvents() {
  $("#project-name").addEventListener("input", (event) => {
    state.projectName = event.target.value || "Untitled note";
    document.title = `${state.projectName} — PrintPal`;
    touch();
  });

  $$("[data-add]").forEach((node) =>
    node.addEventListener("click", () => addBlock(node.dataset.add))
  );

  $("#btn-template").addEventListener("click", () => {
    const key = $("#template-select").value;
    if (key) applyTemplate(key);
  });

  $$("#mode-switch button").forEach((node) =>
    node.addEventListener("click", () => setMode(node.dataset.mode))
  );

  $$("#label-kind button").forEach((node) =>
    node.addEventListener("click", () => {
      state.labels.kind = node.dataset.kind === "shelf" ? "shelf" : "list";
      applyMode();
      touch();
    })
  );

  function labelField(selector, apply, { numeric = false } = {}) {
    const node = $(selector);
    if (!node) return;
    const event = node.tagName === "SELECT" || node.type === "checkbox" ? "change" : "input";
    node.addEventListener(event, () => {
      if (node.type === "checkbox") apply(node.checked);
      else if (numeric) apply(clamp(int(node.value, 0), 0, 100000));
      else apply(node.value);
      touch();
    });
  }

  labelField("#label-list-text", (value) => {
    state.labels.list.text = value;
    autoGrow($("#label-list-text"));
  });
  labelField("#label-list-font", (value) => {
    state.labels.list.font = value;
  });
  labelField("#label-list-size", (value) => {
    state.labels.list.size = clamp(int(value, 64), 16, 120);
  }, { numeric: true });
  labelField("#label-list-gap", (value) => {
    state.labels.list.gap = clamp(int(value, 18), 0, 200);
  }, { numeric: true });
  labelField("#label-list-bold", (value) => {
    state.labels.list.bold = value;
  });
  labelField("#shelf-title", (value) => {
    state.labels.shelf.title = value;
  });
  labelField("#shelf-footnote", (value) => {
    state.labels.shelf.footnote = value;
  });
  labelField("#shelf-font", (value) => {
    state.labels.shelf.font = value;
  });
  labelField("#shelf-max-size", (value) => {
    state.labels.shelf.maxSize = clamp(int(value, 64), 16, 140);
  }, { numeric: true });
  labelField("#shelf-footnote-size", (value) => {
    state.labels.shelf.footnoteSize = clamp(int(value, 22), 10, 40);
  }, { numeric: true });
  labelField("#shelf-barcode", (value) => {
    state.labels.shelf.barcode = value;
  });
  labelField("#shelf-barcode-data", (value) => {
    state.labels.shelf.barcodeData = value;
  });

  $("#btn-new").addEventListener("click", newProject);
  $("#btn-save").addEventListener("click", saveProject);
  $("#btn-open").addEventListener("click", () => $("#project-input").click());
  $("#project-input").addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    await openProject(file);
  });

  $("#btn-export").addEventListener("click", async () => {
    try {
      const response = await fetch("/api/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc: state.doc, options: state.options }),
      });
      if (!response.ok) throw new Error("Preview failed");
      download(await response.blob(), `${state.projectName.replace(/[^\w\-]+/g, "-").toLowerCase() || "printpal"}.png`);
      toast("PNG exported", "success");
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $("#btn-print").addEventListener("click", () => printDocument());
  $("#btn-print-top").addEventListener("click", () => printDocument());
  $("#btn-test").addEventListener("click", () => printDocument({ test: true }));
  $("#btn-calibration").addEventListener("click", printCalibration);
  $("#btn-modal-test").addEventListener("click", async () => {
    await printDocument({ test: true });
    closeModal();
  });
  $("#btn-feed").addEventListener("click", feedPaper);
  $("#btn-cancel").addEventListener("click", async () => {
    try {
      await api("/api/cancel", { method: "POST" });
      toast("Cancelling…");
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $("#device-pill").addEventListener("click", openModal);
  $("#btn-connect").addEventListener("click", openModal);
  $("#btn-close-modal").addEventListener("click", closeModal);
  $("#connect-modal").addEventListener("click", (event) => {
    if (event.target === $("#connect-modal")) closeModal();
  });

  $$("#connect-tabs button").forEach((tab) =>
    tab.addEventListener("click", () => {
      $$("#connect-tabs button").forEach((node) => node.classList.toggle("active", node === tab));
      $("#tab-ble").hidden = tab.dataset.tab !== "ble";
      $("#tab-serial").hidden = tab.dataset.tab !== "serial";
      if (tab.dataset.tab === "serial") refreshPorts();
    })
  );

  $("#btn-scan").addEventListener("click", scanDevices);
  $("#btn-ports").addEventListener("click", refreshPorts);
  $("#btn-disconnect").addEventListener("click", disconnectPrinter);
  $("#btn-manual-connect").addEventListener("click", () => {
    const address = $("#adv-address").value.trim();
    if (!address) {
      toast("Enter a Bluetooth address first", "warn");
      return;
    }
    connect({ kind: "ble", address, name: "Manual device" }, `Connecting to ${address}…`);
  });

  $("#adv-protocol").addEventListener("change", (event) => {
    persistSettings({ protocol: event.target.value });
    toast("Protocol preference saved — reconnect to apply");
  });
  $("#adv-write-response").addEventListener("change", (event) => {
    persistSettings({ ble: { write_with_response: event.target.checked } });
  });
  $("#btn-raw").addEventListener("click", async () => {
    try {
      const response = await api("/api/raw", { method: "POST", body: { hex: $("#adv-hex").value } });
      toast(`Sent raw bytes`, "success");
      if (response.job) updateJob(response.job);
    } catch (error) {
      toast(error.message, "error", "Raw send failed");
    }
  });

  $("#auto-connect").addEventListener("change", (event) => {
    persistSettings({ auto_connect: event.target.checked });
  });

  ["opt-dither", "opt-supersample"].forEach((id) =>
    $(`#${id}`).addEventListener("change", () => qualityChanged())
  );
  ["opt-threshold", "opt-brightness", "opt-contrast"].forEach((id) => {
    const output = $(`#${id.replace("opt-", "out-")}`);
    $(`#${id}`).addEventListener("input", () => {
      if (output) output.value = $(`#${id}`).value;
      qualityChanged();
    });
  });
  $("#opt-width").addEventListener("change", () => {
    applyDocSize();
    touch();
  });
  $("#opt-margin").addEventListener("input", () => {
    applyDocSize();
    touch();
  });
  $("#opt-feed").addEventListener("change", () => qualityChanged());
  $("#opt-rotate").addEventListener("change", () => qualityChanged());
  $("#opt-invert").addEventListener("change", () => qualityChanged());

  $("#btn-reset-quality").addEventListener("click", () => {
    state.options = { ...QUALITY_DEFAULTS };
    state.doc.width = 384;
    state.doc.margin = 10;
    applyQualityControls();
    persistSettings({ render: state.options });
    touch();
    toast("Print quality reset");
  });

  $$("#zoom-seg button").forEach((node) =>
    node.addEventListener("click", () => {
      state.zoom = Number(node.dataset.zoom) || 1;
      $$("#zoom-seg button").forEach((item) => item.classList.toggle("active", item === node));
      $("#paper").style.width = `${Math.round(state.doc.width * state.zoom)}px`;
    })
  );

  $("#image-input").addEventListener("change", async (event) => {
    const files = [...(event.target.files || [])];
    const target = state.pendingImageBlock;
    state.pendingImageBlock = null;
    event.target.value = "";
    for (const file of files) await uploadFile(file, target);
  });

  $("#btn-clear-log").addEventListener("click", () => {
    $("#log").replaceChildren();
  });

  const editor = $("#editor");
  let dragDepth = 0;
  window.addEventListener("dragover", (event) => event.preventDefault());
  window.addEventListener("drop", (event) => event.preventDefault());
  editor.addEventListener("dragenter", (event) => {
    event.preventDefault();
    dragDepth += 1;
    editor.classList.add("drag-over");
  });
  editor.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) editor.classList.remove("drag-over");
  });
  editor.addEventListener("dragover", (event) => event.preventDefault());
  editor.addEventListener("drop", async (event) => {
    event.preventDefault();
    dragDepth = 0;
    editor.classList.remove("drag-over");
    const files = [...(event.dataTransfer?.files || [])];
    if (!files.length) return;
    for (const file of files) await uploadFile(file);
  });

  window.addEventListener("paste", async (event) => {
    const items = [...(event.clipboardData?.items || [])];
    const image = items.find((item) => item.type.startsWith("image/"));
    if (!image) return;
    const file = image.getAsFile();
    if (file) {
      event.preventDefault();
      await uploadFile(file);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (!$("#connect-modal").hidden) closeModal();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "p") {
      event.preventDefault();
      printDocument();
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      saveProject();
    }
  });
}


async function boot() {
  wireEvents();
  try {
    const bootstrap = await api("/api/bootstrap");
    state.fonts = Array.isArray(bootstrap.fonts) ? bootstrap.fonts : [];
    state.settings = bootstrap.settings || {};
    state.ports = bootstrap.ports || [];
    state.options = normalizeOptions({ ...QUALITY_DEFAULTS, ...(state.settings.render || {}) });
    $("#auto-connect").checked = state.settings.auto_connect !== false;
    $("#adv-protocol").value = state.settings.protocol || "auto";
    $("#adv-write-response").checked = Boolean(state.settings.ble?.write_with_response);

    const restored = restoreLocal();
    if (!restored) {
      state.mode = "document";
      state.labels = normalizeLabels(LABEL_DEFAULTS);
      state.documentDoc = welcomeDoc();
      state.doc = state.documentDoc;
      state.options = normalizeOptions({ ...state.options, ...QUALITY_DEFAULTS });
    }
    $("#project-name").value = state.projectName;
    document.title = `${state.projectName} — PrintPal`;
    applyQualityControls();
    applyMode({ preview: false });
    if (bootstrap.connection) updateConnectionUI(bootstrap.connection, bootstrap.printer);
    if (bootstrap.job) updateJob(bootstrap.job);
    if (bootstrap.logs) appendLogs(bootstrap.logs);
    state.lastLogId = Math.max(state.lastLogId, ...(bootstrap.logs || []).map((entry) => entry.id), 0);
    if (state.connection.connected) state.lastJobActive = false;
    renderPorts();
    renderDevices();
    schedulePreview(true);
    pollLoop();
  } catch (error) {
    toast(`Could not start PrintPal: ${error.message}`, "error");
  }
}

boot();

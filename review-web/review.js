"use strict";

const $ = id => document.getElementById(id);
const localMode = document.querySelector('meta[name="review-local"]') !== null;
const sideTitle = {ch: "CH · Hán Nôm", vi: "VI · Quốc ngữ"};
const positionKey = "crop-review-position";
const view = {
  directory: null, books: [], book: "", pages: [], page: 0, rows: [], row: 0, sources: {}, zoom: 100,
  loadVersion: 0, renderVersion: 0,
};
let recentFolder = null;
let useFilePicker = typeof window.showDirectoryPicker !== "function";
const drafts = new Map();
let saving = false;
let showDownloads = false;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function option(text, value) {
  const node = element("option", "", text);
  node.value = value;
  return node;
}

function previousPosition() {
  try {
    return JSON.parse(localStorage.getItem(positionKey));
  } catch (error) {
    return {error: error.message};
  }
}

function rememberPosition() {
  const page = view.pages[view.page]?.label;
  try {
    localStorage.setItem(positionKey, JSON.stringify({folder: view.directory.name, book: view.book, page}));
  } catch {
    // Có thể tiếp tục xem ngay cả khi trình duyệt không lưu vị trí.
  }
}

function nextPending() {
  for (let offset = 1; offset < view.pages.length; offset++) {
    const index = (view.page + offset) % view.pages.length;
    if (sides.some(side => view.pages[index][side]?.checked === 0)) return index;
  }
  return -1;
}

function setMessage(text, error = false) {
  $("message").textContent = text;
  $("message").hidden = !text;
  $("message").classList.toggle("issue", error);
}

function setNotice(text) {
  $("notice").textContent = text;
  $("notice").hidden = !text;
}

function setNavigation() {
  $("page").disabled = !view.pages.length;
  $("prev").disabled = !view.pages.length || view.page === 0;
  $("next").disabled = view.page >= view.pages.length - 1;
  $("pending").disabled = nextPending() < 0;
}

async function readImage(page) {
  try {
    return await page?.image?.getFile();
  } catch {
    return null;
  }
}

async function openSource(file) {
  if (!file) return null;
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    return image;
  } catch {
    return null;
  } finally {
    URL.revokeObjectURL(url);
  }
}

function cropBounds(box, image) {
  if (!box?.points || !image) return null;
  const xs = box.points.map(([x]) => x);
  const ys = box.points.map(([, y]) => y);
  const x = Math.max(0, Math.floor(Math.min(...xs)));
  const y = Math.max(0, Math.floor(Math.min(...ys)));
  const width = Math.min(image.naturalWidth, Math.ceil(Math.max(...xs))) - x;
  const height = Math.min(image.naturalHeight, Math.ceil(Math.max(...ys))) - y;
  return Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0
    ? {x, y, width, height} : null;
}

function drawCrop(image, bounds) {
  const {x, y, width, height} = bounds;
  const canvas = document.createElement("canvas");
  const scale = Math.min(1, 2400 / Math.max(width, height));
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  canvas.getContext("2d").drawImage(image, x, y, width, height, 0, 0, canvas.width, canvas.height);
  return canvas;
}

function cropMessage(side, box) {
  if (!box) return "—";
  if (box.error) return box.error;
  if (!view.sources[side]) return "Không tìm thấy hoặc không đọc được ảnh gốc.";
  return "Box nằm ngoài ảnh hoặc có diện tích bằng 0.";
}

function editableText(box) {
  return box.text.trim() === "NULL" ? "" : box.text.replace(/\r\n?/g, "\n");
}

function updateEditorCount(input) {
  const isNom = input.dataset.side === "ch";
  const units = isNom ? /[\p{L}\p{N}]/gu : /[\p{L}\p{N}][\p{L}\p{M}\p{N}]*/gu;
  const count = input.value.trim() === "NULL" ? 0 : (input.value.match(units)?.length ?? 0);
  input.nextElementSibling.textContent = `${count} ${isNom ? "chữ" : "tiếng"}`;
}

function ocrEditor(side, index, box) {
  const editor = element("div", "ocr-editor");
  const input = element("textarea", "text");
  input.rows = 1;
  input.dataset.side = side;
  input.dataset.row = String(index);
  input.placeholder = "Nhập chữ";
  input.setAttribute("aria-label", `Chữ dòng ${index + 1}, ${sideTitle[side]}`);
  if (side === "ch") input.lang = "zh-Hant";
  editor.hidden = $("toggle-text").getAttribute("aria-pressed") === "false";
  input.value = drafts.get(box)?.text ?? editableText(box);
  input.classList.toggle("edited", drafts.has(box));
  input.readOnly = saving;
  const page = view.pages[view.page][side];
  input.onfocus = () => selectRow(index, false);
  input.oninput = () => {
    updateEditorCount(input);
    if (input.value === editableText(box)) drafts.delete(box);
    else drafts.set(box, {side, page, box, text: input.value});
    refreshEdits();
  };
  editor.append(input, element("div", "editor-count"));
  updateEditorCount(input);
  return editor;
}

function makeCell(side, index, box, bounds, referenceWidth) {
  const cell = element("div", "cell");
  const number = element("span", "line-number", box ? String(index + 1).padStart(2, "0") : "");
  const content = element("div");
  if (bounds) {
    const button = element("button", "crop");
    button.type = "button";
    button.setAttribute("aria-label", `Phóng to dòng ${index + 1}, ${sideTitle[side]}`);
    const canvas = drawCrop(view.sources[side], bounds);
    canvas.style.width = `min(${bounds.width}px, ${100 * bounds.width / referenceWidth}%)`;
    button.append(canvas);
    button.onclick = () => {
      selectRow(index);
      openDetail();
    };
    content.append(button);
  } else {
    content.append(element("p", "meta crop-placeholder", cropMessage(side, box)));
  }
  if (box && !box.error) content.append(ocrEditor(side, index, box));
  cell.append(number, content);
  return cell;
}

function pairedBoxes(pair, alignment) {
  const ch = pair.ch?.boxes || [];
  const vi = pair.vi?.boxes || [];
  const indexes = alignment || Array.from({length: Math.max(ch.length, vi.length)}, (_, i) => [i, i]);
  return indexes.map(([left, right]) => [ch[left], vi[right]]);
}

function renderRows() {
  const bounds = view.rows.map(row => row.map((box, i) => cropBounds(box, view.sources[sides[i]])));
  const referenceWidth = Math.max(1, ...bounds.flat().map(b => b?.width || 0));
  const rows = document.createDocumentFragment();
  for (const [index, boxes] of view.rows.entries()) {
    const row = element("div", "pair grid");
    for (const [i, side] of sides.entries()) {
      row.append(makeCell(side, index, boxes[i], bounds[index][i], referenceWidth));
    }
    rows.append(row);
  }
  $("rows").replaceChildren(rows);
  setMessage(view.rows.length ? "" : "Trang này chưa có box để đối chiếu.");
  selectRow(0, false);
}

function statusText(page) {
  if (!page) return "Chưa có nhãn";
  if (page.error) return page.error;
  const checked = page.checked === 1 ? "Đã duyệt" : page.checked === 0 ? "Chưa duyệt" : "Chưa có trạng thái";
  return `${page.boxes.length} box · ${checked}`;
}

async function showPage(index) {
  if (index < 0 || index >= view.pages.length) return;
  const version = ++view.renderVersion;
  view.page = index;
  view.row = 0;
  view.rows = [];
  view.sources = {};
  $("page-image").disabled = true;
  $("rows").replaceChildren();
  $("scroller").scrollTop = 0;
  $("page").value = String(index);
  setNavigation();
  rememberPosition();

  const pair = view.pages[index];
  for (const side of sides) {
    const page = pair[side];
    const pageNumber = page?.number;
    const prefix = pageNumber != null && pair.ch?.number !== pair.vi?.number
      && pair.ch && pair.vi ? `Trang ${String(pageNumber).padStart(3, "0")} · ` : "";
    $(side + "-meta").textContent = prefix + statusText(page);
    $(side + "-meta").title = page?.name || "";
  }
  setNotice("");
  setMessage("Đang tải ảnh trang…");
  updatePosition();
  const sameImage = pair.ch?.image && pair.ch.image === pair.vi?.image;
  const leftRead = readImage(pair.ch);
  const results = await Promise.all([leftRead, sameImage ? leftRead : readImage(pair.vi)]);
  if (version !== view.renderVersion) return;
  const files = results.map(result => result?.error ? null : result);
  // Dóng lần đầu khi mở trang, để việc chọn cả thư mục không phải đọc nội dung mọi ảnh.
  const leftSource = openSource(files[0]);
  const rightSource = sameImage ? leftSource : openSource(files[1]);
  const [sources, alignment] = await Promise.all([Promise.all([leftSource, rightSource]), alignPage(pair, files)]);
  if (version !== view.renderVersion) return;
  const [chCount, viCount] = sides.map(side => pair[side]?.boxes.length || 0);
  const hasGaps = alignment?.some(row => row.includes(null));
  const notes = sides.flatMap((side, i) => pair[side] && !sources[i]
    ? [`${side.toUpperCase()} · ${results[i]?.error || "Thiếu ảnh trang."}`] : []);
  if (pair.ch && pair.vi && (hasGaps || chCount !== viCount)) {
    notes.push(`Có dòng thiếu box (CH ${chCount}, VI ${viCount}). ${alignment
      ? "Đã dóng theo vị trí trên ảnh." : "Đang ghép theo thứ tự đọc."}`);
  }
  setNotice(notes.join(" "));
  view.sources = {ch: sources[0], vi: sources[1]};
  $("page-image").disabled = !sources.some(Boolean);
  view.rows = pairedBoxes(pair, alignment);
  renderRows();
}

function clearPage() {
  ++view.renderVersion;
  view.pages = [];
  view.page = 0;
  view.rows = [];
  view.sources = {};
  $("page-image").disabled = true;
  $("page").replaceChildren(option("—", ""));
  $("rows").replaceChildren();
  for (const side of sides) {
    $(side + "-meta").textContent = "";
    $(side + "-meta").title = "";
  }
  setNavigation();
  setNotice("");
  updatePosition();
}

async function loadBook(book, targetPage) {
  const version = ++view.loadVersion;
  view.book = book.name;
  showDownloads = !localMode && typeof view.directory?.requestPermission !== "function";
  $("book").value = book.name;
  clearPage();
  setMessage("Đang đọc Label.txt…");
  try {
    const pages = await pagesForBook(book);
    if (version !== view.loadVersion) return;
    view.pages = pages;
    if (!pages.length) {
      setMessage("Sách này chưa có bounding box đã lưu.");
      rememberPosition();
      return;
    }
    $("page").replaceChildren(...pages.map((page, index) => option(page.label, index)));
    await showPage(Math.max(0, pages.findIndex(page => page.label === targetPage)));
  } catch (error) {
    if (version === view.loadVersion) setMessage("Không tải được dữ liệu. " + error.message, true);
  }
}

function updatePosition() {
  const page = view.pages[view.page];
  const rows = $("rows").children.length;
  $("position").textContent = page
    ? `Trang ${page.label} · ${view.page + 1}/${view.pages.length}${rows ? ` · Dòng ${view.row + 1}/${rows}` : ""}`
    : "";
}

function selectRow(index, scroll = true) {
  const rows = $("rows").children;
  if (rows.length) {
    rows[view.row]?.classList.remove("current");
    view.row = Math.max(0, Math.min(rows.length - 1, index));
    rows[view.row].classList.add("current");
    if (scroll) rows[view.row].scrollIntoView({block: "nearest"});
  }
  updatePosition();
}

function setZoom(value) {
  view.zoom = Math.max(75, Math.min(300, value));
  $("detail-pair").style.setProperty("--zoom", view.zoom + "%");
  $("zoom-fit").textContent = view.zoom + "%";
  $("zoom-in").disabled = view.zoom === 300;
  $("zoom-out").disabled = view.zoom === 75;
}

function openDetail() {
  const pair = view.pages[view.page];
  if (!pair || !$("rows").children.length) return;
  $("detail").dataset.mode = "line";
  $("toggle-boxes").hidden = true;
  $("detail-title").textContent = `Trang ${pair.label} · Dòng ${view.row + 1}`;
  $("detail-pair").replaceChildren(...sides.map((side, i) => {
    const box = view.rows[view.row][i];
    const section = element("section");
    section.append(element("h2", "side", sideTitle[side]));
    const bounds = cropBounds(box, view.sources[side]);
    if (bounds) {
      const frame = element("div", "detail-image");
      frame.append(drawCrop(view.sources[side], bounds));
      section.append(frame);
    } else {
      section.append(element("p", "meta crop-placeholder", cropMessage(side, box)));
    }
    if (box && !box.error) section.append(ocrEditor(side, view.row, box));
    return section;
  }));
  showDetail();
}

function showDetail() {
  if (!$("detail").open) {
    setZoom(100);
    $("detail").showModal();
  }
}

function openPageImage(showBoxes = false) {
  const pair = view.pages[view.page];
  $("detail").dataset.mode = "page";
  $("detail-title").textContent = `Ảnh toàn trang · ${pair.label}`;
  const toggle = $("toggle-boxes");
  toggle.hidden = false;
  toggle.setAttribute("aria-pressed", String(showBoxes));
  toggle.textContent = showBoxes ? "Ẩn bbox" : "Hiện bbox";
  $("detail-pair").replaceChildren(...sides.filter(side => view.sources[side]).map(side => {
    const image = view.sources[side];
    const section = element("section");
    section.append(element("h2", "side", `${sideTitle[side]} · ${pair[side].name}`));
    const frame = element("div", "detail-image");
    const canvas = document.createElement("canvas");
    const scale = Math.min(1, 2400 / Math.max(image.naturalWidth, image.naturalHeight));
    canvas.width = Math.round(image.naturalWidth * scale);
    canvas.height = Math.round(image.naturalHeight * scale);
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", `Ảnh toàn trang · ${sideTitle[side]}`);
    const context = canvas.getContext("2d");
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    context.scale(canvas.width / image.naturalWidth, canvas.height / image.naturalHeight);
    for (const box of showBoxes ? pair[side].boxes : []) {
      if (!box.points) continue;
      const selected = box === view.rows[view.row]?.[sides.indexOf(side)];
      context.beginPath();
      context.moveTo(...box.points[0]);
      for (const point of box.points.slice(1)) context.lineTo(...point);
      context.closePath();
      context.lineWidth = (selected ? 7 : 3) / scale;
      context.strokeStyle = selected ? "#ff9825" : "#1dd6a0";
      context.stroke();
    }
    frame.append(canvas);
    section.append(frame);
    return section;
  }));
  showDetail();
}

function toggleText() {
  const visible = $("toggle-text").getAttribute("aria-pressed") === "true";
  $("toggle-text").setAttribute("aria-pressed", String(!visible));
  document.querySelectorAll(".ocr-editor").forEach(editor => { editor.hidden = visible; });
}

function editStatus(message, error = false) {
  for (const id of ["save-status", "detail-save-status"]) {
    const status = $(id);
    status.hidden = !message;
    status.textContent = message;
    status.classList.toggle("issue", error);
  }
}

function refreshEdits(message, error = false) {
  const cannotSave = saving || drafts.size === 0;
  $("save").disabled = cannotSave;
  $("detail-save").disabled = cannotSave;
  for (const side of sides) {
    const button = $("download-" + side);
    button.hidden = !showDownloads || ![...drafts.values()].some(edit => edit.side === side);
    button.disabled = saving;
  }
  for (const input of document.querySelectorAll("textarea.text")) {
    const box = view.rows[Number(input.dataset.row)]?.[sides.indexOf(input.dataset.side)];
    if (!box) continue;
    const draft = drafts.get(box);
    const text = draft?.text ?? editableText(box);
    if (input.value !== text) {
      input.value = text;
      updateEditorCount(input);
    }
    input.classList.toggle("edited", Boolean(draft));
    input.readOnly = saving;
  }
  editStatus(message ?? (drafts.size ? `${drafts.size} ô chưa lưu` : ""), error);
}

function canLeaveEdits() {
  if (saving) return false;
  if (drafts.size && !confirm("Có chữ chưa lưu. Bỏ các chỉnh sửa này?")) return false;
  drafts.clear();
  refreshEdits();
  return true;
}

function labelHandle(side) {
  return view.books.find(book => book.name === view.book)?.folders[side]?.get("label.txt");
}

async function saveEdits() {
  if (!drafts.size || saving) return;
  const editedSides = sides.filter(side => [...drafts.values()].some(edit => edit.side === side));
  if (!localMode && editedSides.some(side => typeof labelHandle(side)?.createWritable !== "function")) {
    showDownloads = true;
    refreshEdits("Mở bằng Mo-web-doi-chieu.bat để lưu trực tiếp vào Label.txt.", true);
    return;
  }
  saving = true;
  refreshEdits("Đang lưu chữ…");
  const saved = [];
  const errors = [];
  try {
    if (!localMode && await view.directory.requestPermission({mode: "readwrite"}) !== "granted") {
      showDownloads = true;
      errors.push("Chưa có quyền ghi. Mở bằng Mo-web-doi-chieu.bat để lưu trực tiếp.");
    } else {
      for (const side of editedSides) {
        const edits = [...drafts.values()].filter(edit => edit.side === side);
        try {
          await saveLabelText(labelHandle(side), edits);
          for (const {box, text} of edits) {
            box.text = text;
            drafts.delete(box);
          }
          saved.push(side.toUpperCase());
        } catch (error) {
          if (!localMode && (error.name === "NotAllowedError" || error.name === "SecurityError")) {
            showDownloads = true;
          }
          errors.push(`${side.toUpperCase()} · ${error.message}`);
        }
      }
    }
  } catch (error) {
    showDownloads = !localMode;
    errors.push(error.message);
  } finally {
    saving = false;
    const status = saved.length ? [`Đã lưu ${saved.join(" và ")} vào Label.txt.`, ...errors] : errors;
    refreshEdits(status.join(" "), errors.length > 0);
  }
}

async function downloadEdits(side) {
  if (saving) return;
  const edits = [...drafts.values()].filter(edit => edit.side === side);
  if (!edits.length) return;
  saving = true;
  refreshEdits("Đang tạo tệp nhãn…");
  let message;
  let failed = false;
  try {
    const text = await updatedLabelText(await labelHandle(side).getFile(), edits);
    const url = URL.createObjectURL(new Blob([text], {type: "text/plain;charset=utf-8"}));
    const link = element("a");
    link.href = url;
    link.download = `Label-${side.toUpperCase()}.txt`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    message = `Đã tải ${link.download}. Đổi tên thành Label.txt rồi chép vào ${side}/ của sách đang xem.`;
  } catch (error) {
    message = error.message;
    failed = true;
  } finally {
    saving = false;
    refreshEdits(message, failed);
  }
}

async function loadDirectory(directory) {
  const version = ++view.loadVersion;
  const previous = previousPosition();
  const restore = previous?.folder === directory.name ? previous : null;
  clearPage();
  view.directory = directory;
  view.books = [];
  $("book").disabled = true;
  $("book").replaceChildren(option("—", ""));
  $("reload").disabled = true;
  setMessage("Đang tìm các thư mục ch/ và vi/…");
  try {
    const books = await discoverBooks(directory);
    if (version !== view.loadVersion) return;
    view.books = books;
    $("reload").disabled = false;
    $("book").replaceChildren(...books.map(book => option(
      book.name.normalize("NFC") + (!book.folders.ch ? " · VI" : !book.folders.vi ? " · CH" : ""),
      book.name
    )));
    $("book").disabled = !books.length;
    if (books.length) {
      const selected = books.find(book => book.name === restore?.book) || books[0];
      await loadBook(selected, restore?.book === selected.name ? restore.page : undefined);
    } else {
      $("book").append(option("Chưa có sách", ""));
      setMessage("Chưa tìm thấy ch/Label.txt hoặc vi/Label.txt trong các thư mục sách.");
    }
  } catch (error) {
    if (version === view.loadVersion) setMessage("Không tải được danh sách sách. " + error.message, true);
  }
}

async function folderStorage(mode, directory) {
  try {
    const db = await new Promise((resolve, reject) => {
      const request = indexedDB.open("crop-review-folders", 1);
      request.onupgradeneeded = () => request.result.createObjectStore("folders");
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    try {
      return await new Promise((resolve, reject) => {
        const tx = db.transaction("folders", mode);
        const store = tx.objectStore("folders");
        const request = directory ? store.put(directory, "recent") : store.get("recent");
        tx.oncomplete = () => resolve(request.result);
        tx.onabort = () => reject(tx.error);
      });
    } finally {
      db.close();
    }
  } catch {
    return null;
  }
}

async function openFolder() {
  if (saving || localMode) return;
  if (useFilePicker) {
    $("folder-files").click();
    return;
  }
  let directory;
  try {
    directory = await window.showDirectoryPicker({id: "crop-review", mode: "read", ...(recentFolder ? {startIn: recentFolder} : {})});
  } catch (error) {
    if (error.name !== "AbortError") {
      useFilePicker = true;
      setMessage("Bấm Chọn thư mục để mở bằng hộp thoại chọn tệp của trình duyệt.", true);
    }
    return;
  }
  if (!canLeaveEdits()) return;
  recentFolder = directory;
  $("reopen-folder").hidden = true;
  void folderStorage("readwrite", directory);
  await loadDirectory(directory);
}

async function reopenFolder() {
  if (!recentFolder || saving) return;
  try {
    if (await recentFolder.requestPermission({mode: "read"}) === "granted") {
      if (!canLeaveEdits()) return;
      $("reopen-folder").hidden = true;
      await loadDirectory(recentFolder);
    }
  } catch {
    setMessage("Bấm Chọn thư mục để cấp lại quyền đọc.", true);
  }
}

async function restoreFolder() {
  if (useFilePicker) return;
  const directory = await folderStorage("readonly");
  if (!directory || view.directory) return;
  recentFolder = directory;
  try {
    if (await directory.queryPermission({mode: "read"}) === "granted") {
      if (!view.directory) await loadDirectory(directory);
      return;
    }
  } catch {
    // Người dùng vẫn có thể chọn lại thư mục.
    return;
  }
  if (!view.directory) $("reopen-folder").hidden = false;
}

function reload() {
  if (saving) return;
  if (localMode) {
    if (canLeaveEdits()) return loadLocalLibrary();
    return;
  }
  if (!view.directory) return;
  if (view.directory === recentFolder) {
    if (canLeaveEdits()) return loadDirectory(view.directory);
    return;
  }
  $("folder-files").click();
}

function pickPage() {
  const select = $("page");
  if (select.disabled) return;
  select.focus();
  try {
    select.showPicker?.();
  } catch {
    // Một số trình duyệt chỉ cho mở danh sách bằng bàn phím sau khi focus.
  }
}

$("open-folder").onclick = openFolder;
$("reopen-folder").onclick = reopenFolder;
$("folder-files").onchange = event => {
  const input = event.target;
  if (input.files.length && canLeaveEdits()) {
    loadDirectory(directoryFromFiles(input.files));
    $("reopen-folder").hidden = true;
  }
  input.value = "";
};
$("book").onchange = () => {
  const book = view.books.find(item => item.name === $("book").value);
  if (book && canLeaveEdits()) loadBook(book);
  else $("book").value = view.book;
};
$("page").onchange = () => showPage(Number($("page").value));
$("prev").onclick = () => showPage(view.page - 1);
$("next").onclick = () => showPage(view.page + 1);
$("pending").onclick = () => showPage(nextPending());
$("page-image").onclick = () => openPageImage();
$("toggle-boxes").onclick = () => openPageImage($("toggle-boxes").getAttribute("aria-pressed") !== "true");
$("toggle-text").onclick = toggleText;
$("reload").onclick = reload;
$("help-open").onclick = () => $("help").showModal();
$("zoom-in").onclick = () => setZoom(view.zoom + 25);
$("zoom-out").onclick = () => setZoom(view.zoom - 25);
$("zoom-fit").onclick = () => setZoom(100);
$("save").onclick = saveEdits;
$("detail-save").onclick = saveEdits;
for (const side of sides) $("download-" + side).onclick = () => downloadEdits(side);

window.addEventListener("beforeunload", event => {
  if (drafts.size) {
    event.preventDefault();
    event.returnValue = "";
  }
});

document.addEventListener("keydown", event => {
  if (!event.isComposing && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
    event.preventDefault();
    if (!event.repeat) void saveEdits();
    return;
  }
  if (event.ctrlKey || event.altKey || event.metaKey || event.isComposing || $("help").open) return;
  if (event.target.closest("select, input, textarea, [contenteditable]")) return;
  const key = event.key.toLowerCase();
  if (key === "enter" && event.target.closest("button")) return;
  const moveRow = step => {
    selectRow(view.row + step);
    if ($("detail").open) {
      if ($("detail").dataset.mode === "page") {
        if ($("toggle-boxes").getAttribute("aria-pressed") === "true") openPageImage(true);
      }
      else openDetail();
    }
  };
  const actions = $("detail").open ? {
    arrowup: () => moveRow(-1), arrowdown: () => moveRow(1),
    "+": () => setZoom(view.zoom + 25), "=": () => setZoom(view.zoom + 25),
    "-": () => setZoom(view.zoom - 25), "0": () => setZoom(100), t: toggleText,
  } : {
    arrowleft: () => showPage(view.page - 1), arrowright: () => showPage(view.page + 1),
    arrowup: () => moveRow(-1), arrowdown: () => moveRow(1), enter: openDetail,
    g: pickPage, n: () => showPage(nextPending()), o: openFolder,
    t: toggleText, r: reload, "?": () => $("help").showModal(),
  };
  if (Object.hasOwn(actions, key)) {
    event.preventDefault();
    actions[key]();
  }
});

async function loadLocalLibrary() {
  setMessage("Đang tìm các thư mục ch/ và vi/…");
  try {
    await loadDirectory(await localReviewDirectory());
  } catch (error) {
    $("reload").disabled = false;
    setMessage("Không tải được danh sách sách. " + error.message, true);
  }
}

if (localMode) {
  $("folder-controls").hidden = true;
  $("shortcut-folder").hidden = true;
  $("shortcut-folder-key").hidden = true;
  void loadLocalLibrary();
} else {
  void restoreFolder();
}

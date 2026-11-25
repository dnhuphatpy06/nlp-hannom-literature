"use strict";

const sides = ["ch", "vi"];
const normalizedName = name => name.normalize("NFC").toLowerCase();
const nameOrder = new Intl.Collator("vi", {numeric: true}).compare;
const imageExtension = /\.(?:jpe?g|png|webp|bmp)$/i;

async function entriesByName(directory) {
  const entries = new Map();
  for await (const entry of directory.values()) entries.set(normalizedName(entry.name), entry);
  return entries;
}

async function bookIn(directory, entries) {
  const folders = {};
  for (const side of sides) {
    const folder = entries.get(side);
    if (folder?.kind !== "directory") continue;
    const files = await entriesByName(folder);
    if (files.get("label.txt")?.kind === "file") folders[side] = files;
  }
  if (!Object.keys(folders).length) return null;
  const cache = entries.get("cache");
  const pages = cache?.kind === "directory" ? (await entriesByName(cache)).get("pages") : null;
  const cachedImages = pages?.kind === "directory" ? await entriesByName(pages) : new Map();
  return {name: directory.name, folders, cachedImages};
}

async function discoverBooks(root) {
  const entries = await entriesByName(root);
  const single = await bookIn(root, entries);
  if (single) return [single];
  const books = [];
  for (const [name, entry] of entries) {
    if (entry.kind !== "directory" || name === "cache" || name.startsWith(".")) continue;
    const book = await bookIn(entry, await entriesByName(entry));
    if (book) books.push(book);
  }
  return books.sort((a, b) => Object.keys(b.folders).length - Object.keys(a.folders).length
    || nameOrder(a.name, b.name));
}

function directoryTree(rootName) {
  function directory(name) {
    const children = new Map();
    return {kind: "directory", name, children, values: () => children.values()};
  }
  const root = directory(rootName);
  return {
    root,
    add(path, file) {
      let parent = root;
      for (const name of path.slice(0, -1)) {
        if (!parent.children.has(name)) parent.children.set(name, directory(name));
        parent = parent.children.get(name);
      }
      parent.children.set(file.name, file);
    },
  };
}

// Cho trình duyệt dùng ô chọn thư mục thay cho File System Access API.
function directoryFromFiles(files) {
  const tree = directoryTree(files[0].webkitRelativePath.split("/")[0]);
  for (const file of files) {
    const path = file.webkitRelativePath.split("/").slice(1);
    const image = imageExtension.test(file.name);
    const sideFile = [2, 3].includes(path.length) && sides.includes(normalizedName(path.at(-2)))
      && (image || ["label.txt", "filestate.txt"].includes(normalizedName(file.name)));
    const cacheImage = [3, 4].includes(path.length) && image
      && normalizedName(path.at(-3)) === "cache" && normalizedName(path.at(-2)) === "pages";
    if (!sideFile && !cacheImage) continue;
    tree.add(path, {kind: "file", name: file.name, getFile: async () => file});
  }
  return tree.root;
}

async function reviewRequest(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const result = await response.json().catch(() => null);
    throw new Error(result?.error || `Không đọc được dữ liệu web (${response.status}).`);
  }
  return response;
}

async function localReviewDirectory() {
  const {name, paths, token} = await (await reviewRequest("./api/library")).json();
  const tree = directoryTree(name);
  for (const path of paths) {
    const parts = path.split("/");
    const filename = parts.at(-1);
    const url = "./api/file?" + new URLSearchParams({path});
    const file = {
      kind: "file",
      name: filename,
      getFile: async () => (await reviewRequest(url)).blob(),
    };
    if (normalizedName(filename) === "label.txt") {
      file.saveEdits = async edits => {
        const response = await reviewRequest(url);
        const text = await updatedLabelText(await response.blob(), edits);
        await reviewRequest(url, {
          method: "PUT",
          headers: {
            "Content-Type": "text/plain; charset=utf-8",
            "If-Match": response.headers.get("ETag"),
            "X-Review-Token": token,
          },
          body: text,
        });
      };
    }
    tree.add(parts, file);
  }
  return tree.root;
}

async function tabRecords(handle) {
  const entries = new Map();
  if (handle?.kind !== "file") return entries;
  const text = await (await handle.getFile()).text();
  for (const line of text.split(/\r?\n/)) {
    const tab = line.indexOf("\t");
    if (tab <= 0) continue;
    const name = recordName(line.slice(0, tab));
    if (name) entries.set(normalizedName(name), {name, value: line.slice(tab + 1)});
  }
  return entries;
}

function recordName(key) {
  return key.replace(/^\uFEFF/, "").replaceAll("\\", "/").split("/").at(-1);
}

function decodeBoxes(payload) {
  const boxes = JSON.parse(payload);
  if (!Array.isArray(boxes)) throw new Error("Nhãn trang phải là một mảng box.");
  return boxes.map((box, index) => {
    const points = box?.points;
    if (!Array.isArray(points) || points.length !== 4 || !points.every(
      point => Array.isArray(point) && point.length === 2
        && point.every(coord => Number.isFinite(coord) && Math.abs(coord) < 2 ** 53)
    )) return {error: "Tọa độ box không hợp lệ."};
    return {index, points, text: typeof box.transcription === "string" ? box.transcription : ""};
  });
}

async function updatedLabelText(file, edits) {
  const text = new TextDecoder("utf-8", {ignoreBOM: true}).decode(await file.arrayBuffer());
  const parts = text.split(/(\r?\n)/);
  const positions = new Map();
  for (let i = 0; i < parts.length; i += 2) {
    const tab = parts[i].indexOf("\t");
    if (tab > 0) positions.set(normalizedName(recordName(parts[i].slice(0, tab))), i);
  }
  const changed = new Map();
  for (const {page, box, text: correction} of edits) {
    const index = positions.get(normalizedName(page.name));
    const line = parts[index];
    const conflict = () => new Error(`Nhãn của ${page.name} đã thay đổi. Hãy kiểm tra file trên đĩa trước khi lưu lại.`);
    if (line === undefined) throw conflict();
    const tab = line.indexOf("\t");
    if (!changed.has(index)) {
      let boxes;
      try {
        boxes = JSON.parse(line.slice(tab + 1));
      } catch {
        throw conflict();
      }
      if (!Array.isArray(boxes)) throw conflict();
      changed.set(index, boxes);
    }
    const current = changed.get(index)[box.index];
    const original = typeof current?.transcription === "string" ? current.transcription : "";
    if (JSON.stringify(current?.points) !== JSON.stringify(box.points)
      || (original !== box.text && original !== correction)) throw conflict();
    current.transcription = correction;
  }
  for (const [index, boxes] of changed) {
    const line = parts[index];
    parts[index] = line.slice(0, line.indexOf("\t") + 1) + JSON.stringify(boxes);
  }
  return parts.join("");
}

async function saveLabelText(handle, edits) {
  if (handle.saveEdits) return handle.saveEdits(edits);
  const writable = await handle.createWritable({mode: "exclusive"});
  try {
    const file = await handle.getFile();
    await writable.write(await updatedLabelText(file, edits));
    const latest = await handle.getFile();
    if (latest.lastModified !== file.lastModified || latest.size !== file.size) {
      throw new Error("Label.txt vừa thay đổi trên đĩa. Hãy kiểm tra lại trước khi lưu.");
    }
    await writable.close();
  } catch (error) {
    await writable.abort().catch(() => {});
    throw error;
  }
}

function boxGeometry(box) {
  const xs = box.points.map(point => point[0]);
  const ys = box.points.map(point => point[1]);
  return {
    x: (Math.min(...xs) + Math.max(...xs)) / 2,
    y: (Math.min(...ys) + Math.max(...ys)) / 2,
    width: Math.max(...xs) - Math.min(...xs),
    height: Math.max(...ys) - Math.min(...ys),
  };
}

function median(values) {
  values.sort((a, b) => a - b);
  return (values[Math.floor((values.length - 1) / 2)] + values[Math.floor(values.length / 2)]) / 2;
}

function orderViBoxes(boxes) {
  if (boxes.length < 2 || boxes.some(box => !box.points)) return boxes;
  // Cùng quy tắc đọc với ocr_engines.order_boxes cho các hàng ngang.
  const geometry = boxes.map(box => ({box, ...boxGeometry(box)})).sort((a, b) => a.y - b.y);
  const tolerance = 0.6 * median(geometry.map(line => Math.max(1, line.height)));
  const rows = [];
  for (const line of geometry) {
    const previous = rows.at(-1);
    if (previous && Math.abs(line.y - median(previous.map(item => item.y))) <= tolerance) previous.push(line);
    else rows.push([line]);
  }
  return rows.flatMap(row => row.sort((a, b) => a.x - b.x).map(line => line.box));
}

function stem(name) {
  return name.replace(/\.[^.]+$/, "");
}

async function folderPages(files, side, cachedImages) {
  if (!files) return [];
  const [labels, states] = await Promise.all([tabRecords(files.get("label.txt")), tabRecords(files.get("filestate.txt"))]);
  const pages = [];
  for (const [key, {name, value}] of labels) {
    const rawNumber = stem(name).match(/(?:^|[._ -])(\d+)$/)?.[1];
    const parsedNumber = rawNumber === undefined ? null : Number(rawNumber);
    const number = Number.isSafeInteger(parsedNumber) ? parsedNumber : null;
    const source = files.get(key) ?? cachedImages.get(key);
    const image = source?.kind === "file" && imageExtension.test(name) ? source : null;
    const state = states.get(key)?.value.trim();
    const page = {name, number, image, checked: state === undefined ? null : Number(state === "1"), boxes: []};
    try {
      const boxes = decodeBoxes(value);
      page.boxes = side === "vi" ? orderViBoxes(boxes) : boxes;
    } catch {
      page.error = "Dòng tương ứng trong Label.txt bị lỗi.";
    }
    pages.push(page);
  }
  return pages;
}

async function pagesForBook(book) {
  const lists = await Promise.all(sides.map(side => folderPages(book.folders[side], side, book.cachedImages)));
  const numbers = lists.map(pages => new Set(pages.map(page => page.number)));
  const [chPages, viPages] = lists;
  // Cùng quy tắc với align_books.py cho sách có trang Nôm và Quốc ngữ xen kẽ.
  if (chPages.length && chPages.length === viPages.length
    && numbers.every(set => set.size === chPages.length && !set.has(null))
    && ![...numbers[0]].some(number => numbers[1].has(number))) {
    const sorted = lists.map(pages => pages.toSorted((a, b) => a.number - b.number));
    return sorted[0].map((ch, index) => {
      const vi = sorted[1][index];
      return {label: String(ch.number).padStart(3, "0"), ch, vi};
    }).filter(pair => [pair.ch, pair.vi].some(page => page.boxes.some(box => box.points)));
  }
  const groups = new Map();
  for (const [index, side] of sides.entries()) {
    for (const page of lists[index]) {
      const key = page.number ?? normalizedName(stem(page.name));
      if (!groups.has(key)) groups.set(key, {ch: [], vi: []});
      groups.get(key)[side].push(page);
    }
  }
  const result = [];
  const keys = [...groups.keys()].sort((a, b) => {
    if (typeof a === "number") return typeof b === "number" ? a - b : -1;
    return typeof b === "number" ? 1 : nameOrder(a, b);
  });
  for (const key of keys) {
    const {ch, vi} = groups.get(key);
    const ambiguous = ch.length > 1 || vi.length > 1;
    const remaining = new Map(vi.map(page => [normalizedName(page.name), page]));
    const pairs = ch.map(page => {
      const name = normalizedName(page.name);
      const match = ambiguous ? remaining.get(name) : vi[0];
      if (match) remaining.delete(normalizedName(match.name));
      return [page, match];
    });
    pairs.push(...[...remaining.values()].map(page => [null, page]));
    for (const [left, right] of pairs) {
      if (![left, right].some(page => page?.boxes.some(box => box.points))) continue;
      const name = (left || right).name;
      let label = typeof key === "number" ? String(key).padStart(3, "0") : stem(name);
      if (ambiguous) label += " · " + name;
      result.push({label, ch: left, vi: right});
    }
  }
  return result;
}

function interleavedRows(ch, vi) {
  if (!ch?.boxes.length || !vi?.boxes.length) return null;
  const lines = [];
  for (const [side, page] of [ch, vi].entries()) {
    const sideLines = [];
    for (const [index, box] of page.boxes.entries()) {
      if (!box.points) return null;
      const {y, width, height} = boxGeometry(box);
      if (height <= 0 || width <= height) return null;
      sideLines.push({y, height, side, index});
    }
    sideLines.sort((a, b) => a.y - b.y);
    // Hai box cạnh nhau trên một hàng không phải hai dòng xen kẽ.
    if (sideLines.some((line, i) => i > 0 &&
      line.y - sideLines[i - 1].y < 0.6 * Math.min(line.height, sideLines[i - 1].height))) return null;
    lines.push(...sideLines);
  }
  lines.sort((a, b) => a.y - b.y);

  const rows = [];
  let matches = 0;
  for (const [i, line] of lines.entries()) {
    const previous = lines[i - 1];
    if (line.side === 1 && previous?.side === 0) {
      const gap = line.y - previous.y;
      if (gap >= 0.4 * Math.min(line.height, previous.height) && gap <= 2 * Math.max(line.height, previous.height)) {
        rows.at(-1)[1] = line.index;
        matches++;
        continue;
      }
    }
    rows.push(line.side === 0 ? [line.index, null] : [null, line.index]);
  }
  return matches * 2 > Math.min(ch.boxes.length, vi.boxes.length) ? rows : null;
}

async function alignPage(pair, [chImage, viImage]) {
  if (!chImage || !viImage || chImage.size !== viImage.size) return null;
  const rows = interleavedRows(pair.ch, pair.vi);
  if (!rows) return null;
  if (chImage === viImage) return rows;
  try {
    const buffers = await Promise.all([chImage.arrayBuffer(), viImage.arrayBuffer()]);
    const [left, right] = buffers.map(buffer => new Uint8Array(buffer));
    return left.every((byte, index) => byte === right[index]) ? rows : null;
  } catch {
    return null;
  }
}

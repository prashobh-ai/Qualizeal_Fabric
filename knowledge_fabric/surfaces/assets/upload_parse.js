/* T138 — real in-browser document parsing.
 *
 * DOCX / PPTX / XLSX are ZIP archives of XML; JSZip (vendored, MIT) unzips them
 * with no network, and we read the OOXML with plain regex (no DOMParser) so the
 * same code runs in the browser AND under Node for tests. The output is a set of
 * real passages (one per paragraph / slide / row), tables and image references,
 * each with a citation coordinate — so an uploaded file is genuinely indexed,
 * answerable and citable, not a placeholder.
 *
 * KFUpload.parse(filename, bytes, opts) -> Promise<{
 *   filename, ext, hash, parsed, note,
 *   passages: [{text, coord, kind}], tables: [{name, columns, rows}], images: [{name, slide}]
 * }>  — coord is the citation label, e.g. "SOW.docx · ¶47" or "deck.pptx · slide 6".
 */
(function (root) {
  "use strict";
  function getZip(opts) {
    if (opts && opts.JSZip) return opts.JSZip;
    if (typeof JSZip !== "undefined") return JSZip; // browser global (vendored)
    if (typeof require !== "undefined") { try { return require("jszip"); } catch (e) {} }
    return null;
  }
  var MAX_PASSAGES = 800; // bound the fabric growth from one file
  var ENTS = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'" };
  function unesc(s) {
    return String(s || "").replace(/&(amp|lt|gt|quot|apos);/g, function (m) { return ENTS[m]; })
      .replace(/&#(\d+);/g, function (_, n) { return String.fromCharCode(+n); });
  }
  function stripTags(s) { return unesc(String(s || "").replace(/<[^>]+>/g, "")); }
  function clean(s) { return stripTags(s).replace(/\s+/g, " ").trim(); }
  function hashBytes(bytes) {
    // FNV-1a (32-bit) over the raw bytes — stable content id for dedupe.
    var h = 0x811c9dc5;
    for (var i = 0; i < bytes.length; i++) { h ^= bytes[i]; h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0; }
    return "up_" + h.toString(16);
  }
  function toU8(data) {
    if (data instanceof Uint8Array) return data;
    if (typeof ArrayBuffer !== "undefined" && data instanceof ArrayBuffer) return new Uint8Array(data);
    if (typeof Buffer !== "undefined" && Buffer.isBuffer && Buffer.isBuffer(data)) return new Uint8Array(data);
    return new Uint8Array(data || []);
  }
  function extOf(name) { var m = /\.([a-z0-9]+)$/i.exec(name || ""); return m ? m[1].toLowerCase() : ""; }
  function decodeText(u8) {
    try { return new TextDecoder("utf-8").decode(u8); }
    catch (e) { var s = ""; for (var i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]); return s; }
  }

  // ---- DOCX -------------------------------------------------------------
  function parseDocx(xml, filename) {
    var passages = [], tables = [], section = "";
    // Tables first, then remove them so the paragraph pass doesn't double-count.
    (xml.match(/<w:tbl\b[\s\S]*?<\/w:tbl>/g) || []).forEach(function (tbl, ti) {
      var rows = (tbl.match(/<w:tr\b[\s\S]*?<\/w:tr>/g) || []).map(function (tr) {
        return (tr.match(/<w:tc\b[\s\S]*?<\/w:tc>/g) || []).map(function (tc) {
          return (tc.match(/<w:t\b[^>]*>([\s\S]*?)<\/w:t>/g) || [])
            .map(function (t) { return clean(t); }).join(" ").trim();
        });
      }).filter(function (r) { return r.some(function (c) { return c; }); });
      if (!rows.length) return;
      var columns = rows[0], body = rows.slice(1);
      tables.push({ name: "Table " + (ti + 1), columns: columns, rows: body });
      var flat = rows.map(function (r) { return r.join(" | "); }).join("  ·  ");
      passages.push({ text: ("Table " + (ti + 1) + ": " + flat).slice(0, 600),
        coord: filename + " · Table " + (ti + 1), kind: "table" });
    });
    var body = xml.replace(/<w:tbl\b[\s\S]*?<\/w:tbl>/g, "");
    var paras = body.match(/<w:p\b[\s\S]*?<\/w:p>/g) || [];
    var pn = 0;
    for (var i = 0; i < paras.length && passages.length < MAX_PASSAGES; i++) {
      var pm = paras[i];
      var style = /<w:pStyle\s+w:val="([^"]*)"/.exec(pm);
      var runs = (pm.match(/<w:t\b[^>]*>([\s\S]*?)<\/w:t>/g) || []).map(function (t) { return clean(t); });
      var text = runs.join("").replace(/\s+/g, " ").trim();
      if (!text) continue;
      pn += 1;
      var isHeading = style && /heading|title/i.test(style[1]);
      if (isHeading) section = text;
      passages.push({ text: text.slice(0, 600), coord: filename + " · ¶" + pn,
        kind: isHeading ? "heading" : "document", section_path: section });
    }
    return { passages: passages, tables: tables };
  }

  // ---- PPTX -------------------------------------------------------------
  function slideNo(name) { var m = /slide(\d+)\.xml$/.exec(name); return m ? +m[1] : 0; }
  function parsePptx(zip, filename) {
    var passages = [], images = [];
    var slideNames = Object.keys(zip.files).filter(function (n) { return /ppt\/slides\/slide\d+\.xml$/.test(n); }).sort(function (a, b) { return slideNo(a) - slideNo(b); });
    var noteNames = Object.keys(zip.files).filter(function (n) { return /ppt\/notesSlides\/notesSlide\d+\.xml$/.test(n); });
    Object.keys(zip.files).filter(function (n) { return /ppt\/media\/[^/]+$/.test(n); }).forEach(function (n) {
      images.push({ name: n.split("/").pop(), slide: null });
    });
    var chain = Promise.resolve();
    slideNames.forEach(function (name) {
      chain = chain.then(function () {
        return zip.files[name].async("string").then(function (xml) {
          var texts = (xml.match(/<a:t>([\s\S]*?)<\/a:t>/g) || []).map(function (t) { return clean(t); }).filter(Boolean);
          var n = slideNo(name);
          var noteName = "ppt/notesSlides/notesSlide" + n + ".xml";
          var body = texts.join(" ").replace(/\s+/g, " ").trim();
          var done = Promise.resolve("");
          if (noteNames.indexOf(noteName) >= 0) {
            done = zip.files[noteName].async("string").then(function (nx) {
              return (nx.match(/<a:t>([\s\S]*?)<\/a:t>/g) || []).map(function (t) { return clean(t); }).join(" ").trim();
            });
          }
          return done.then(function (note) {
            var full = body + (note ? " Notes: " + note : "");
            if (full.trim()) passages.push({ text: full.slice(0, 600), coord: filename + " · slide " + n, kind: "slide", slide: n });
          });
        });
      });
    });
    return chain.then(function () { return { passages: passages, images: images }; });
  }

  // ---- XLSX -------------------------------------------------------------
  function colLetters(ref) { var m = /^([A-Z]+)/.exec(ref || ""); return m ? m[1] : ""; }
  function colIndex(letters) { var n = 0; for (var i = 0; i < letters.length; i++) n = n * 26 + (letters.charCodeAt(i) - 64); return n - 1; }
  function parseXlsx(zip, filename) {
    var tables = [], passages = [];
    var shared = [];
    var chain = Promise.resolve();
    if (zip.files["xl/sharedStrings.xml"]) {
      chain = chain.then(function () {
        return zip.files["xl/sharedStrings.xml"].async("string").then(function (xml) {
          (xml.match(/<si>[\s\S]*?<\/si>/g) || []).forEach(function (si) {
            shared.push((si.match(/<t\b[^>]*>([\s\S]*?)<\/t>/g) || []).map(function (t) { return clean(t); }).join(""));
          });
        });
      });
    }
    var sheetNames = Object.keys(zip.files).filter(function (n) { return /xl\/worksheets\/sheet\d+\.xml$/.test(n); }).sort();
    sheetNames.forEach(function (name, si) {
      chain = chain.then(function () {
        return zip.files[name].async("string").then(function (xml) {
          var rows = (xml.match(/<row\b[\s\S]*?<\/row>/g) || []).map(function (row) {
            var cells = row.match(/<c\b[^>]*>[\s\S]*?<\/c>|<c\b[^>]*\/>/g) || [];
            var out = [];
            cells.forEach(function (c) {
              var ref = (/r="([^"]+)"/.exec(c) || [])[1] || "";
              var idx = colIndex(colLetters(ref));
              var v = (/<v>([\s\S]*?)<\/v>/.exec(c) || [])[1];
              var isStr = /t="s"/.test(c);
              var val = v == null ? "" : (isStr ? (shared[+v] || "") : v);
              if (idx >= 0) out[idx] = val;
            });
            for (var k = 0; k < out.length; k++) if (out[k] == null) out[k] = "";
            return out;
          }).filter(function (r) { return r.some(function (c) { return String(c).trim(); }); });
          if (!rows.length) return;
          var columns = rows[0], bodyRows = rows.slice(1);
          tables.push({ name: "Sheet " + (si + 1), columns: columns, rows: bodyRows });
          bodyRows.slice(0, 200).forEach(function (r, ri) {
            var text = columns.map(function (col, ci) { return col + ": " + (r[ci] || ""); }).join("; ");
            if (text.trim()) passages.push({ text: text.slice(0, 600), coord: filename + " · Sheet " + (si + 1) + " row " + (ri + 2), kind: "table" });
          });
        });
      });
    });
    return chain.then(function () { return { passages: passages, tables: tables }; });
  }

  // ---- text / csv / md --------------------------------------------------
  function parseCsv(text, filename) {
    var lines = text.split(/\r?\n/).filter(function (l) { return l.trim(); });
    var rows = lines.map(function (l) { return l.split(",").map(function (c) { return c.trim(); }); });
    var tables = [], passages = [];
    if (rows.length) {
      var columns = rows[0], bodyRows = rows.slice(1);
      tables.push({ name: filename, columns: columns, rows: bodyRows });
      bodyRows.slice(0, 300).forEach(function (r, ri) {
        var t = columns.map(function (col, ci) { return col + ": " + (r[ci] || ""); }).join("; ");
        if (t.trim()) passages.push({ text: t.slice(0, 600), coord: filename + " · row " + (ri + 2), kind: "table" });
      });
    }
    return { passages: passages, tables: tables };
  }
  function parsePlain(text, filename) {
    var paras = text.split(/\n\s*\n/).map(function (p) { return p.replace(/\s+/g, " ").trim(); }).filter(Boolean);
    var passages = [];
    for (var i = 0; i < paras.length && i < MAX_PASSAGES; i++) {
      passages.push({ text: paras[i].slice(0, 600), coord: filename + " · ¶" + (i + 1), kind: "document" });
    }
    return { passages: passages, tables: [] };
  }

  function parse(filename, data, opts) {
    var u8 = toU8(data), ext = extOf(filename), hash = hashBytes(u8);
    var base = { filename: filename, ext: ext, hash: hash, parsed: true, note: "",
      passages: [], tables: [], images: [] };
    function withText() { return decodeText(u8); }
    if (ext === "pdf") {
      base.parsed = false;
      base.note = "PDF stored; text extraction runs server-side on commit.";
      base.passages = [{ text: "PDF document “" + filename + "” (" + u8.length + " bytes) added to the Files source. Its text is extracted server-side when committed to the repository.", coord: filename, kind: "document" }];
      return Promise.resolve(base);
    }
    if (ext === "csv" || ext === "tsv") { var r = parseCsv(withText(), filename); base.passages = r.passages; base.tables = r.tables; return Promise.resolve(base); }
    if (ext === "md" || ext === "txt" || ext === "text" || ext === "json" || ext === "log") { var p = parsePlain(withText(), filename); base.passages = p.passages; base.tables = p.tables; return Promise.resolve(base); }
    var Z = getZip(opts);
    if (!Z) { base.parsed = false; base.note = "parser unavailable"; base.passages = [{ text: "Document “" + filename + "” added to the Files source.", coord: filename, kind: "document" }]; return Promise.resolve(base); }
    return Z.loadAsync(u8).then(function (zip) {
      if (ext === "docx" || ext === "docm") {
        return zip.files["word/document.xml"].async("string").then(function (xml) {
          var r = parseDocx(xml, filename); base.passages = r.passages; base.tables = r.tables;
          Object.keys(zip.files).filter(function (n) { return /word\/media\/[^/]+$/.test(n); }).forEach(function (n) { base.images.push({ name: n.split("/").pop() }); });
          return base;
        });
      }
      if (ext === "pptx" || ext === "pptm") { return parsePptx(zip, filename).then(function (r) { base.passages = r.passages; base.images = r.images; return base; }); }
      if (ext === "xlsx" || ext === "xlsm") { return parseXlsx(zip, filename).then(function (r) { base.passages = r.passages; base.tables = r.tables; return base; }); }
      base.parsed = false; base.note = "unsupported archive type " + ext;
      base.passages = [{ text: "Document “" + filename + "” added to the Files source.", coord: filename, kind: "document" }];
      return base;
    }).catch(function (e) {
      base.parsed = false; base.note = "could not parse: " + (e && e.message ? e.message : e);
      base.passages = [{ text: "Document “" + filename + "” added to the Files source (parse failed).", coord: filename, kind: "document" }];
      return base;
    });
  }

  var KFUpload = { parse: parse, hashBytes: hashBytes, PARSED_EXT: ["docx", "docm", "pptx", "pptm", "xlsx", "xlsm", "csv", "tsv", "md", "txt", "json", "log"] };
  if (typeof module !== "undefined" && module.exports) module.exports = KFUpload;
  root.KFUpload = KFUpload;
})(typeof self !== "undefined" ? self : this);

/* QualiZeal Knowledge Fabric — real-physics answer galaxy (L2.4).
 *
 * A self-contained, ES5, no-bundler browser module. It renders the tenant
 * knowledge graph with a real force layout (vis-network barnesHut), lights the
 * nodes an answer activated in coral, their one-hop halo in blue, and dims the
 * rest. The Python payload builder is knowledge_fabric/health/galaxy.py; this
 * file is the view half and depends on nothing but the global `vis` exposed by
 * the vendored vis-network.min.js (included by the page before this script).
 *
 * Public surface: window.KFGalaxy.mount(container, payload, opts) and
 * window.KFGalaxy.flash(activatedIds). mount also returns an object carrying a
 * bound flash for convenience. No network, no external URLs, no other deps.
 */
(function () {
  "use strict";

  var CORAL = "#F53E5A"; // activated
  var BLUE = "#0096FF"; // halo
  var MUTED = "#5A6B7C"; // everything else
  var NAVY = "#0E1A45"; // canvas ground (both themes)

  // module-level handles so KFGalaxy.flash can act on the last mounted graph
  var NET = null;
  var NODES = null;
  var STATE = { activated: {}, halo: {}, deg: {} };

  function isDark() {
    try {
      var t = document.documentElement.getAttribute("data-theme");
      if (t === "dark") return true;
      if (t === "light") return false;
      return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
    } catch (e) {
      return false;
    }
  }

  function toSet(list) {
    var out = {};
    (list || []).forEach(function (id) {
      out[id] = true;
    });
    return out;
  }

  function stateOf(id) {
    if (STATE.activated[id]) return "activated";
    if (STATE.halo[id]) return "halo";
    return "rest";
  }

  // rgba muted colour: 55% opacity, a little lighter in dark theme so dim
  // nodes stay visible against the navy ground.
  function mutedFill(dark) {
    return dark ? "rgba(138,157,176,0.62)" : "rgba(90,107,124,0.55)";
  }

  function colorFor(st, dark) {
    if (st === "activated") return { bg: CORAL, border: CORAL, label: "#FFFFFF" };
    if (st === "halo") return { bg: BLUE, border: BLUE, label: dark ? "#DCEBFF" : "#0B3B66" };
    var fill = mutedFill(dark);
    return { bg: fill, border: fill, label: dark ? "#9FB0C2" : "#5A6B7C" };
  }

  function note(container, text, dark) {
    var color = dark ? "#9FB0C2" : "#5A6B7C";
    container.innerHTML =
      '<div style="display:flex;align-items:center;justify-content:center;' +
      "height:100%;min-height:120px;font:13px system-ui,sans-serif;color:" +
      color +
      ';padding:16px;text-align:center">' +
      esc(text) +
      "</div>";
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // labelled ids = every activated node + the eight highest-degree halo nodes,
  // so a busy graph never becomes a wall of overlapping text.
  function labelledIds(payload) {
    var labelled = {};
    (payload.nodes || []).forEach(function (n) {
      if (STATE.activated[n.id]) labelled[n.id] = true;
    });
    var halo = (payload.nodes || []).filter(function (n) {
      return STATE.halo[n.id];
    });
    halo.sort(function (a, b) {
      return (b.deg || 0) - (a.deg || 0);
    });
    halo.slice(0, 8).forEach(function (n) {
      labelled[n.id] = true;
    });
    return labelled;
  }

  function paintGround(container) {
    // Navy gradient ground in BOTH themes — the one allowed navy. vis draws on
    // a transparent canvas, so the container background shows through.
    container.style.background =
      "radial-gradient(circle at 50% 32%, #1A2C63 0%, " + NAVY + " 70%, #0A1436 100%)";
    container.style.borderRadius = "10px";
    container.style.overflow = "hidden";
    if (!container.style.height) container.style.minHeight = "260px";
  }

  function buildNodes(payload, dark, labelled) {
    return (payload.nodes || []).map(function (n) {
      var st = stateOf(n.id);
      var col = colorFor(st, dark);
      var deg = n.deg || 0;
      return {
        id: n.id,
        label: labelled[n.id] ? String(n.label || "") : undefined,
        title: String(n.label || ""),
        shape: "dot",
        size: 6 + Math.min(deg, 40) * 0.5,
        color: {
          background: col.bg,
          border: col.border,
          highlight: { background: col.bg, border: "#FFFFFF" }
        },
        font: { color: col.label, size: 12, face: "system-ui, sans-serif" }
      };
    });
  }

  function buildEdges(payload) {
    return (payload.edges || []).map(function (e) {
      var a = !!STATE.activated[e.from];
      var b = !!STATE.activated[e.to];
      var opacity = 0.04;
      var width = 0.6;
      if (a && b) {
        opacity = 0.9;
        width = 1.6;
      } else if (a || b) {
        opacity = 0.25;
        width = 1.0;
      }
      return {
        from: e.from,
        to: e.to,
        title: String(e.relation || "related"),
        width: width,
        color: { color: "#B8C6D6", opacity: opacity }
      };
    });
  }

  function options() {
    return {
      physics: {
        enabled: true,
        solver: "barnesHut",
        barnesHut: {
          gravitationalConstant: -3000,
          springLength: 95,
          springConstant: 0.02,
          damping: 0.4
        },
        stabilization: { iterations: 150 }
      },
      interaction: { hover: true, dragNodes: true, zoomView: true, tooltipDelay: 120 },
      nodes: { borderWidth: 1 },
      edges: { smooth: false }
    };
  }

  function emptyOverlay(container, payload, dark) {
    // No activation and no edges: a caption plus the dimmed top concepts, never
    // a blank box. The dimmed nodes are already rendered by the network below;
    // this floats the caption over them.
    var color = dark ? "#9FB0C2" : "#B9C6D6";
    var cap = document.createElement("div");
    cap.style.cssText =
      "position:absolute;left:0;right:0;top:12px;text-align:center;pointer-events:none;" +
      "font:12px system-ui,sans-serif;letter-spacing:.02em;color:" +
      color +
      ";z-index:2";
    cap.textContent = "Nothing linked for this answer";
    if (container.style.position !== "absolute") container.style.position = "relative";
    container.appendChild(cap);
  }

  function mount(container, payload, opts) {
    opts = opts || {};
    payload = payload || {};
    var dark = isDark();

    if (!container) return { flash: function () {} };

    if (typeof vis === "undefined") {
      note(container, "Graph unavailable", dark);
      return { flash: function () {} };
    }

    STATE.activated = toSet(payload.activated_ids);
    STATE.halo = toSet(payload.halo_ids);
    STATE.deg = {};
    (payload.nodes || []).forEach(function (n) {
      STATE.deg[n.id] = n.deg || 0;
    });

    paintGround(container);
    container.innerHTML = "";

    var labelled = labelledIds(payload);
    var nodes = buildNodes(payload, dark, labelled);
    var edges = buildEdges(payload);

    var nodesDS, edgesDS;
    try {
      nodesDS = new vis.DataSet(nodes);
      edgesDS = new vis.DataSet(edges);
    } catch (e) {
      note(container, "Graph unavailable", dark);
      return { flash: function () {} };
    }

    var net = new vis.Network(container, { nodes: nodesDS, edges: edgesDS }, options());

    // freeze the layout once it has settled — a static galaxy reads cleaner and
    // costs no CPU while the asker studies it.
    net.on("stabilizationIterationsDone", function () {
      net.setOptions({ physics: false });
    });

    if (opts.onNode) {
      net.on("click", function (params) {
        if (params && params.nodes && params.nodes.length) {
          opts.onNode(params.nodes[0]);
        }
      });
    }

    NET = net;
    NODES = nodesDS;

    var noActivation = !(payload.activated_ids || []).length;
    var noEdges = !(payload.edges || []).length;
    if (noActivation && noEdges) {
      emptyOverlay(container, payload, dark);
    }

    return {
      network: net,
      flash: function (ids) {
        flashOn(net, nodesDS, ids);
      }
    };
  }

  function flashOn(net, nodesDS, ids) {
    if (!net || !nodesDS) return;
    ids = (ids || []).filter(function (id) {
      return !!nodesDS.get(id);
    });
    if (!ids.length) return;

    var base = {};
    ids.forEach(function (id) {
      var n = nodesDS.get(id);
      base[id] = n && n.size ? n.size : 8;
    });
    function scale(mult) {
      nodesDS.update(
        ids.map(function (id) {
          return { id: id, size: base[id] * mult };
        })
      );
    }
    // three steps: 1.0 -> 1.5 -> 1.0 across 500ms, then a gentle fit.
    setTimeout(function () {
      scale(1.5);
    }, 0);
    setTimeout(function () {
      scale(1.2);
    }, 250);
    setTimeout(function () {
      scale(1.0);
      net.fit({ nodes: ids, animation: { duration: 600, easingFunction: "easeInOutQuad" } });
    }, 500);
  }

  window.KFGalaxy = {
    mount: mount,
    flash: function (ids) {
      flashOn(NET, NODES, ids);
    }
  };
})();

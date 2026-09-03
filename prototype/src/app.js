(() => {
  "use strict";

  const DATA = JSON.parse(document.getElementById("trendy-data").textContent);
  const canvas = document.getElementById("map");
  const ctx = canvas.getContext("2d");
  const tip = document.getElementById("tip");

  const CAT_ORDER = ["food", "cafe", "view", "culture", "life"];
  const CAT_VAR = {
    food: "--cat-food", cafe: "--cat-cafe", view: "--cat-view",
    culture: "--cat-culture", life: "--cat-life", place: "--cat-place",
  };

  // 줌 티어 경계 (fit 배율 대비 log2 배). 라벨은 자기 티어에 도달하면 서서히 나타난다.
  // L1은 전체 보기(z=0)에서 이미 완전히 보여야 하므로 시작점이 음수다
  const TIER_IN = [-0.6, 0.85, 2.1];
  const FADE = 0.45;
  const DENSITY_RADIUS = 62; // 밀도 균등화 시 폴백 라벨 사이 최소 간격(px)

  const state = {
    cats: new Set(CAT_ORDER),
    fallback: true,
    density: true,
    scale: 1, fit: 1, cx: 0, cy: 0,
    W: 0, H: 0,
  };

  // ── 투영 ────────────────────────────────────────────────
  const [minLon, minLat, maxLon, maxLat] = DATA.bbox;
  const KX = Math.cos((((minLat + maxLat) / 2) * Math.PI) / 180);
  const worldW = (maxLon - minLon) * KX;
  const worldH = maxLat - minLat;
  const wx = (lon) => (lon - minLon) * KX;
  const wy = (lat) => maxLat - lat;

  function prepRings(ringSets) {
    return ringSets.map((rings) =>
      rings.map((ring) => {
        const pts = new Float64Array(ring.length * 2);
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        for (let i = 0; i < ring.length; i++) {
          const x = wx(ring[i][0]), y = wy(ring[i][1]);
          pts[i * 2] = x; pts[i * 2 + 1] = y;
          if (x < x0) x0 = x; if (x > x1) x1 = x;
          if (y < y0) y0 = y; if (y > y1) y1 = y;
        }
        return { pts, bbox: [x0, y0, x1, y1] };
      })
    );
  }
  const SILHOUETTE = prepRings([DATA.silhouette])[0];
  const SGG = prepRings(DATA.sgg);
  const DONG = prepRings(DATA.dong);

  // ── 라벨 목록 ───────────────────────────────────────────
  const labels = [];
  for (const k of DATA.keywords) {
    const size = k.tr === 1 ? 10.5 + 15 * k.s : k.tr === 2 ? 10.5 + 12 * k.s : 10 + 8 * k.s;
    labels.push({
      text: k.t, cat: k.c, tier: k.tr, score: k.s, kind: "kw", appear: TIER_IN[k.tr - 1],
      where: k.d, x: wx(k.lon), y: wy(k.lat), size, weight: 500,
    });
  }
  for (const p of DATA.places) {
    // 구·군 이름은 전체 보기부터, 행정동 이름은 L2 후반부터 빈 곳을 채운다
    const isSgg = p.k === "sgg";
    labels.push({
      text: p.n, cat: "place", tier: isSgg ? 1 : 3, score: isSgg ? 0.3 : 0.1,
      kind: "place", appear: isSgg ? TIER_IN[0] : 1.5,
      where: p.d, x: wx(p.lon), y: wy(p.lat),
      size: isSgg ? 13 : 11, weight: 400,
    });
  }
  const KEYWORDS = labels.filter((l) => l.kind === "kw").sort((a, b) => b.score - a.score);
  const PLACES = labels.filter((l) => l.kind === "place").sort((a, b) => b.score - a.score);

  // ── 색상 (테마 토큰에서 읽어와 테마 전환에 따라간다) ────
  let COLOR = {};
  function readColors() {
    const cs = getComputedStyle(document.documentElement);
    COLOR = { land: cs.getPropertyValue("--land").trim(), edge: cs.getPropertyValue("--land-edge").trim(),
      sea: cs.getPropertyValue("--sea-line").trim(), paper: cs.getPropertyValue("--paper").trim() };
    for (const [k, v] of Object.entries(CAT_VAR)) COLOR[k] = cs.getPropertyValue(v).trim();
  }

  // ── 카메라 ──────────────────────────────────────────────
  const sx = (x) => (x - state.cx) * state.scale + state.W / 2;
  const sy = (y) => (y - state.cy) * state.scale + state.H / 2;
  const zf = () => Math.log2(state.scale / state.fit);
  const tierOf = () => (zf() < TIER_IN[1] ? 1 : zf() < TIER_IN[2] ? 2 : 3);

  function resize() {
    const r = canvas.parentElement.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    state.W = Math.max(320, Math.round(r.width));
    state.H = Math.max(360, Math.round(r.height));
    canvas.width = state.W * dpr;
    canvas.height = state.H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const prev = state.fit;
    state.fit = Math.min(state.W / worldW, state.H / worldH) * 0.94;
    state.scale = prev ? (state.scale / prev) * state.fit : state.fit;
    draw();
  }

  function resetView() {
    state.scale = state.fit;
    state.cx = worldW / 2;
    state.cy = worldH / 2;
    draw();
  }

  function zoomAt(factor, px, py) {
    const before = { x: (px - state.W / 2) / state.scale + state.cx, y: (py - state.H / 2) / state.scale + state.cy };
    state.scale = Math.min(Math.max(state.scale * factor, state.fit * 0.85), state.fit * 26);
    state.cx = before.x - (px - state.W / 2) / state.scale;
    state.cy = before.y - (py - state.H / 2) / state.scale;
    draw();
  }

  // ── 라벨 배치 ───────────────────────────────────────────
  const widthCache = new Map();
  function textWidth(text, size, weight) {
    const key = weight + "|" + size.toFixed(1) + "|" + text;
    let w = widthCache.get(key);
    if (w === undefined) {
      ctx.font = `${weight} ${size.toFixed(1)}px "IBM Plex Sans KR", sans-serif`;
      w = ctx.measureText(text).width;
      widthCache.set(key, w);
    }
    return w;
  }

  let placed = [];
  function layout() {
    placed = [];
    const z = zf();
    const margin = 40;
    let dropped = 0;

    // 겹칠 때 바로 버리지 않고 앵커 주변으로 조금 밀어내 본다.
    // 원도심처럼 앵커가 몰린 곳에서 라벨이 통째로 사라지는 것을 막는다.
    const NUDGE = [[0, 0], [0, -1], [0, 1], [-1, 0], [1, 0],
                   [-1, -1], [1, -1], [-1, 1], [1, 1],
                   [0, -2], [0, 2], [-1.7, 0], [1.7, 0]];

    const tryPlace = (l, minGap) => {
      const alpha = Math.min(1, (z - l.appear) / FADE);
      if (alpha <= 0.04) return false;
      const ax = sx(l.x), ay = sy(l.y);
      if (ax < -margin || ay < -margin || ax > state.W + margin || ay > state.H + margin) return false;
      const w = textWidth(l.text, l.size, l.weight);
      const halfW = w / 2 + 4, halfH = l.size * 0.62 + 2;
      // 변위 상한 — 이 이상 밀면 라벨이 다른 동네로 넘어가 위치가 거짓이 된다
      const stepX = Math.min(w * 0.5, 22), stepY = l.size + 5;

      for (const [ox, oy] of NUDGE) {
        const px = ax + ox * stepX, py = ay + oy * stepY;
        const box = [px - halfW, py - halfH, px + halfW, py + halfH];
        let clash = false;
        for (const q of placed) {
          if (!(box[2] < q.box[0] || box[0] > q.box[2] || box[3] < q.box[1] || box[1] > q.box[3])) { clash = true; break; }
          if (minGap && Math.hypot(px - q.px, py - q.py) < minGap) { clash = true; break; }
        }
        if (clash) continue;
        // 밀어낸 라벨은 앵커와의 관계가 약해지므로 가는 실선으로 연결한다
        placed.push({ l, px, py, box, alpha, ax, ay, nudged: ox !== 0 || oy !== 0 });
        return true;
      }
      dropped++;
      return false;
    };

    for (const l of KEYWORDS) if (state.cats.has(l.cat)) tryPlace(l, 0);
    const kwCount = placed.length;
    if (state.fallback) for (const l of PLACES) tryPlace(l, state.density ? DENSITY_RADIUS : 0);

    document.getElementById("r-kw").textContent = kwCount;
    document.getElementById("r-pl").textContent = placed.length - kwCount;
    document.getElementById("r-drop").textContent = dropped;
    document.getElementById("r-zoom").textContent = (state.scale / state.fit).toFixed(1) + "×";
    const t = tierOf();
    for (const el of document.querySelectorAll(".tier")) {
      el.dataset.on = Number(el.dataset.tier) === t ? "1" : "0";
    }
  }

  // ── 그리기 ──────────────────────────────────────────────
  function tracePolys(rings, minPx) {
    ctx.beginPath();
    let any = false;
    for (const { pts, bbox } of rings) {
      if ((bbox[2] - bbox[0]) * state.scale < minPx) continue;
      if (sx(bbox[2]) < 0 || sx(bbox[0]) > state.W || sy(bbox[3]) < 0 || sy(bbox[1]) > state.H) continue;
      ctx.moveTo(sx(pts[0]), sy(pts[1]));
      for (let i = 2; i < pts.length; i += 2) ctx.lineTo(sx(pts[i]), sy(pts[i + 1]));
      ctx.closePath();
      any = true;
    }
    return any;
  }

  function draw() {
    layout();
    ctx.clearRect(0, 0, state.W, state.H);
    ctx.fillStyle = COLOR.paper;
    ctx.fillRect(0, 0, state.W, state.H);

    // 육지
    if (tracePolys(SILHOUETTE, 0)) {
      ctx.fillStyle = COLOR.land;
      ctx.fill("evenodd");
      ctx.strokeStyle = COLOR.edge;
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    // 경계선 — 줌이 들어갈수록 잘게
    const z = zf();
    const sggA = Math.min(1, Math.max(0, (z - 0.35) / 0.6));
    if (sggA > 0.02) {
      ctx.save();
      ctx.globalAlpha = sggA * 0.75;
      ctx.strokeStyle = COLOR.edge;
      ctx.lineWidth = 1;
      for (const rings of SGG) { if (tracePolys(rings, 2)) ctx.stroke(); }
      ctx.restore();
    }
    const dongA = Math.min(1, Math.max(0, (z - 1.7) / 0.7));
    if (dongA > 0.02) {
      ctx.save();
      ctx.globalAlpha = dongA * 0.5;
      ctx.strokeStyle = COLOR.sea;
      ctx.lineWidth = 1;
      for (const rings of DONG) { if (tracePolys(rings, 2)) ctx.stroke(); }
      ctx.restore();
    }

    // 밀어낸 라벨의 지시선 — 글자와 실제 위치를 잇는다
    ctx.save();
    ctx.strokeStyle = COLOR.edge;
    ctx.lineWidth = 1;
    for (const p of placed) {
      if (!p.nudged) continue;
      ctx.globalAlpha = p.alpha * 0.5;
      ctx.beginPath();
      ctx.moveTo(p.ax, p.ay);
      ctx.lineTo(p.px, p.py + (p.py > p.ay ? -p.l.size * 0.6 : p.l.size * 0.6));
      ctx.stroke();
    }
    ctx.restore();

    // 라벨
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (const p of placed) {
      const l = p.l;
      ctx.globalAlpha = p.alpha * (l.kind === "place" ? 0.72 : 1);
      ctx.fillStyle = COLOR[l.cat];
      ctx.font = `${l.weight} ${l.size.toFixed(1)}px "IBM Plex Sans KR", sans-serif`;
      ctx.lineWidth = 3;
      ctx.strokeStyle = COLOR.paper;
      ctx.globalAlpha *= 0.85;
      ctx.strokeText(l.text, p.px, p.py); // 배경과의 대비를 위한 얇은 외곽
      ctx.globalAlpha = p.alpha * (l.kind === "place" ? 0.72 : 1);
      ctx.fillText(l.text, p.px, p.py);
    }
    ctx.globalAlpha = 1;
  }

  // ── 상호작용 ────────────────────────────────────────────
  let drag = null;
  canvas.addEventListener("pointerdown", (e) => {
    drag = { x: e.clientX, y: e.clientY, cx: state.cx, cy: state.cy };
    canvas.setPointerCapture(e.pointerId);
    canvas.classList.add("dragging");
    tip.hidden = true;
  });
  canvas.addEventListener("pointermove", (e) => {
    if (drag) {
      state.cx = drag.cx - (e.clientX - drag.x) / state.scale;
      state.cy = drag.cy - (e.clientY - drag.y) / state.scale;
      draw();
      return;
    }
    const r = canvas.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const hit = placed.find((p) => mx >= p.box[0] && mx <= p.box[2] && my >= p.box[1] && my <= p.box[3]);
    if (hit) {
      tip.innerHTML = `<b>${hit.l.text}</b> <span>${hit.l.where}</span>`;
      tip.style.left = Math.min(mx + 12, state.W - 220) + "px";
      tip.style.top = Math.max(my - 34, 4) + "px";
      tip.hidden = false;
    } else {
      tip.hidden = true;
    }
  });
  const endDrag = (e) => {
    drag = null;
    canvas.classList.remove("dragging");
    if (e.pointerId !== undefined && canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
  };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);
  canvas.addEventListener("pointerleave", () => { tip.hidden = true; });

  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = canvas.getBoundingClientRect();
    zoomAt(Math.exp(-e.deltaY * 0.0016), e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });

  canvas.addEventListener("keydown", (e) => {
    const step = 60 / state.scale;
    const keys = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    if (keys[e.key]) {
      state.cx += keys[e.key][0];
      state.cy += keys[e.key][1];
      draw();
      e.preventDefault();
    } else if (e.key === "+" || e.key === "=") {
      zoomAt(1.35, state.W / 2, state.H / 2); e.preventDefault();
    } else if (e.key === "-") {
      zoomAt(1 / 1.35, state.W / 2, state.H / 2); e.preventDefault();
    }
  });

  document.getElementById("zoom-in").onclick = () => zoomAt(1.5, state.W / 2, state.H / 2);
  document.getElementById("zoom-out").onclick = () => zoomAt(1 / 1.5, state.W / 2, state.H / 2);
  document.getElementById("zoom-reset").onclick = resetView;
  document.getElementById("sw-fallback").onchange = (e) => { state.fallback = e.target.checked; draw(); };
  document.getElementById("sw-density").onchange = (e) => { state.density = e.target.checked; draw(); };

  // 카테고리 토글
  const catsEl = document.getElementById("cats");
  for (const key of CAT_ORDER) {
    const n = DATA.keywords.filter((k) => k.c === key).length;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "cat";
    b.setAttribute("aria-pressed", "true");
    b.style.setProperty("--c", `var(${CAT_VAR[key]})`);
    b.innerHTML = `<span class="dot"></span><span>${DATA.categories[key]}</span><span class="n">${n}</span>`;
    b.onclick = () => {
      const on = b.getAttribute("aria-pressed") === "true";
      b.setAttribute("aria-pressed", on ? "false" : "true");
      if (on) state.cats.delete(key); else state.cats.add(key);
      draw();
    };
    catsEl.appendChild(b);
  }

  window.addEventListener("resize", resize);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { readColors(); draw(); });

  readColors();
  resize();
  resetView();
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => { widthCache.clear(); draw(); });
  }
})();

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
    sel: null,
    stores: true,
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

  // ── 카테고리 집계 ───────────────────────────────────────
  // 과밀은 라벨을 더 잘 배치해서 푸는 게 아니라, '한 구역 한 카테고리에 라벨 하나'로 푼다.
  // 지도에는 대표 키워드만 놓고 나머지는 클릭했을 때 목록으로 펼친다.
  // 집계 단위는 줌에 따라 갈린다 — L1·L2는 구·군, L3는 행정동.
  const ITEMS = DATA.keywords
    .map((k) => {
      const [sgg] = k.d.split(" ");
      return {
        text: k.t, cat: k.c, score: k.s, poi: k.k, approx: k.a === 1,
        sgg, where: k.d, x: wx(k.lon), y: wy(k.lat), lon: k.lon, lat: k.lat,
      };
    })
    .sort((a, b) => b.score - a.score);

  function buildGroups(keyOf) {
    const map = new Map();
    for (const it of ITEMS) {
      const key = keyOf(it) + "\u0000" + it.cat;
      let g = map.get(key);
      if (!g) { g = { unit: keyOf(it), cat: it.cat, members: [] }; map.set(key, g); }
      g.members.push(it); // ITEMS가 점수 내림차순이라 members[0]이 대표
    }
    return [...map.values()];
  }
  const GROUPS = {
    sgg: buildGroups((it) => it.sgg),
    dong: buildGroups((it) => it.where),
  };

  function activeGroups() {
    const useDong = tierOf() >= 3;
    const out = [];
    for (const g of (useDong ? GROUPS.dong : GROUPS.sgg)) {
      if (!state.cats.has(g.cat)) continue;
      const members = state.stores ? g.members : g.members.filter((m) => m.poi !== "store");
      if (!members.length) continue;
      const rep = members[0];
      out.push({
        kind: "kw", group: g, members, rep,
        text: rep.text, cat: g.cat, score: rep.score, where: g.unit, approx: rep.approx,
        x: rep.x, y: rep.y, weight: 500,
        // 묶인 개수가 많을수록 조금 크게 — 그 구역에서 그 카테고리가 두껍다는 신호
        size: 10.5 + 14 * rep.score + Math.min(4.5, (members.length - 1) * 0.7),
        appear: useDong ? TIER_IN[2] : TIER_IN[0],
        vanish: useDong ? null : TIER_IN[2],
      });
    }
    return out.sort((a, b) => b.score - a.score);
  }

  // 폴백 지명 라벨 — 실제 위치의 실제 이름이라 집계 대상이 아니다
  const PLACES = DATA.places
    .map((p) => {
      const isSgg = p.k === "sgg";
      return {
        kind: "place", text: p.n, cat: "place", score: isSgg ? 0.3 : 0.1,
        appear: isSgg ? TIER_IN[0] : 1.5, vanish: null,
        where: p.d, x: wx(p.lon), y: wy(p.lat), size: isSgg ? 13 : 11, weight: 400,
      };
    })
    .sort((a, b) => b.score - a.score);

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
    const before = tierOf();
    const anchor = { x: (px - state.W / 2) / state.scale + state.cx, y: (py - state.H / 2) / state.scale + state.cy };
    state.scale = Math.min(Math.max(state.scale * factor, state.fit * 0.85), state.fit * 26);
    state.cx = anchor.x - (px - state.W / 2) / state.scale;
    state.cy = anchor.y - (py - state.H / 2) / state.scale;
    if (tierOf() !== before && state.sel) select(null); else draw();
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
      let alpha = Math.min(1, (z - l.appear) / FADE);
      if (l.vanish != null) alpha = Math.min(alpha, Math.max(0, 1 - (z - l.vanish) / FADE));
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

    for (const l of activeGroups()) tryPlace(l, 0);
    const reps = placed.slice();
    if (state.fallback) for (const l of PLACES) tryPlace(l, state.density ? DENSITY_RADIUS : 0);

    document.getElementById("r-kw").textContent = reps.length;
    document.getElementById("r-mem").textContent = reps.reduce((n, p) => n + p.l.members.length, 0);
    document.getElementById("r-pl").textContent = placed.length - reps.length;
    document.getElementById("r-drop").textContent = dropped;
    document.getElementById("r-unit").textContent = tierOf() >= 3 ? "행정동" : "구·군";
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

    // 선택한 그룹의 구성원 — 라벨 하나에 묶인 실제 장소들을 점으로 편다
    if (state.sel) {
      const rx = sx(state.sel.rep.x), ry = sy(state.sel.rep.y);
      ctx.save();
      for (const m of state.sel.members) {
        const mx = sx(m.x), my = sy(m.y);
        ctx.globalAlpha = 0.35;
        ctx.strokeStyle = COLOR[state.sel.cat];
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(rx, ry);
        ctx.lineTo(mx, my);
        ctx.stroke();
        ctx.globalAlpha = 1;
        ctx.fillStyle = COLOR[state.sel.cat];
        ctx.beginPath();
        ctx.arc(mx, my, 3.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = COLOR.paper;
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
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
      if (state.sel && l.group === state.sel) {
        const half = (p.box[2] - p.box[0]) / 2 - 4;
        ctx.beginPath();
        ctx.moveTo(p.px - half, p.py + l.size * 0.62);
        ctx.lineTo(p.px + half, p.py + l.size * 0.62);
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = COLOR[l.cat];
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
  }

  // ── 상호작용 ────────────────────────────────────────────
  let drag = null;
  canvas.addEventListener("pointerdown", (e) => {
    drag = { x: e.clientX, y: e.clientY, cx: state.cx, cy: state.cy, moved: false };
    canvas.setPointerCapture(e.pointerId);
    canvas.classList.add("dragging");
    tip.hidden = true;
  });
  canvas.addEventListener("pointermove", (e) => {
    if (drag) {
      if (Math.abs(e.clientX - drag.x) > 3 || Math.abs(e.clientY - drag.y) > 3) drag.moved = true;
      state.cx = drag.cx - (e.clientX - drag.x) / state.scale;
      state.cy = drag.cy - (e.clientY - drag.y) / state.scale;
      draw();
      return;
    }
    const r = canvas.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const hit = placed.find((p) => mx >= p.box[0] && mx <= p.box[2] && my >= p.box[1] && my <= p.box[3]);
    if (hit) {
      const note = hit.l.approx ? " · 동 내 근사 위치" : "";
      tip.innerHTML = `<b>${hit.l.text}</b> <span>${hit.l.where}${note}</span>`;
      tip.style.left = Math.min(mx + 12, state.W - 220) + "px";
      tip.style.top = Math.max(my - 34, 4) + "px";
      tip.hidden = false;
    } else {
      tip.hidden = true;
    }
  });
  const endDrag = (e) => {
    if (drag && !drag.moved) {
      const r = canvas.getBoundingClientRect();
      const mx = e.clientX - r.left, my = e.clientY - r.top;
      const hit = placed.find((p) => p.l.kind === "kw" &&
        mx >= p.box[0] && mx <= p.box[2] && my >= p.box[1] && my <= p.box[3]);
      select(hit ? hit.l : null);
    }
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

  // ── 상세 목록 ───────────────────────────────────────────
  const panel = document.getElementById("panel");
  function select(label) {
    state.sel = label;
    if (!label) { panel.hidden = true; draw(); return; }
    const rows = label.members.map((m) => `
      <li>
        <span class="nm">${m.text}</span>
        <span class="badge">${m.poi === "store" ? "가게" : "명소"}</span>
        <span class="dong">${m.where.split(" ")[1]}${m.approx ? " · 근사" : ""}</span>
        <span class="bar" style="--v:${Math.round(m.score * 100)}%"></span>
      </li>`).join("");
    panel.innerHTML = `
      <div class="phead">
        <div>
          <span class="pcat" style="--c:var(${CAT_VAR[label.cat]})">${DATA.categories[label.cat]}</span>
          <h3>${label.where}</h3>
        </div>
        <button type="button" class="pclose" id="pclose" aria-label="닫기">×</button>
      </div>
      <p class="pmeta">${label.members.length}곳 · 대표 <b>${label.text}</b></p>
      <ul class="plist">${rows}</ul>
      <p class="pnote">샘플 데이터입니다. 실제 목록은 수집 시점의 언급 근거와 함께 표시됩니다.</p>`;
    panel.hidden = false;
    document.getElementById("pclose").onclick = () => select(null);
    draw();
  }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") select(null); });

  document.getElementById("zoom-in").onclick = () => zoomAt(1.5, state.W / 2, state.H / 2);
  document.getElementById("zoom-out").onclick = () => zoomAt(1 / 1.5, state.W / 2, state.H / 2);
  document.getElementById("zoom-reset").onclick = resetView;
  document.getElementById("sw-stores").onchange = (e) => { state.stores = e.target.checked; draw(); };
  document.getElementById("sw-fallback").onchange = (e) => { state.fallback = e.target.checked; draw(); };
  document.getElementById("sw-density").onchange = (e) => { state.density = e.target.checked; draw(); };

  // 카테고리 토글
  const catsEl = document.getElementById("cats");
  for (const key of CAT_ORDER) {
    const n = DATA.keywords.filter((k) => k.c === key).length;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "cat";
    b.dataset.cat = key;
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

  // 스크린샷·데모용 조작 API. UI로 할 수 있는 일과 동일한 상태를 코드로 지정한다.
  //   trendyMap.setView(3, 129.06, 35.16)  배율 3배로 해당 좌표를 중심에 둔다
  //   trendyMap.setOptions({ fallback: false, cats: ["food"] })
  window.trendyMap = {
    setView(mult, lon, lat) {
      state.scale = state.fit * mult;
      if (lon !== undefined && lat !== undefined) { state.cx = wx(lon); state.cy = wy(lat); }
      draw();
    },
    setOptions(o = {}) {
      if (o.stores !== undefined) {
        state.stores = o.stores;
        document.getElementById("sw-stores").checked = o.stores;
      }
      if (o.fallback !== undefined) {
        state.fallback = o.fallback;
        document.getElementById("sw-fallback").checked = o.fallback;
      }
      if (o.density !== undefined) {
        state.density = o.density;
        document.getElementById("sw-density").checked = o.density;
      }
      if (o.cats) {
        state.cats = new Set(o.cats);
        for (const b of document.querySelectorAll(".cat")) {
          b.setAttribute("aria-pressed", state.cats.has(b.dataset.cat) ? "true" : "false");
        }
      }
      draw();
    },
    // 대표 키워드 이름으로 상세 목록을 연다 (데모·스크린샷용)
    open(text) {
      const hit = placed.find((p) => p.l.kind === "kw" && p.l.text === text);
      if (hit) select(hit.l);
      return Boolean(hit);
    },
    stats: () => ({ tier: tierOf(), zoom: state.scale / state.fit, placed: placed.length }),
  };

  readColors();
  resize();
  resetView();
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => { widthCache.clear(); draw(); });
  }
})();

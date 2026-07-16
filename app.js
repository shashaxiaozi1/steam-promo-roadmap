const DATA_URL = "./steam_sales_output/promo_data.json";

let rawData = [];
let selectedPublishers = new Set();
let selectedSales = new Set();
let scaleMode = "month";

const COLORS = [
  "rgba(80, 145, 220, 0.36)",
  "rgba(86, 196, 165, 0.36)",
  "rgba(255, 180, 85, 0.38)",
  "rgba(180, 130, 220, 0.34)",
  "rgba(240, 120, 120, 0.34)",
  "rgba(120, 180, 100, 0.34)",
  "rgba(90, 190, 220, 0.34)",
  "rgba(220, 150, 190, 0.34)"
];

function parseDate(dateStr) {
  if (!dateStr) return null;
  const d = new Date(dateStr + "T00:00:00Z");
  return isNaN(d.getTime()) ? null : d;
}

function formatDate(dateStr) {
  if (!dateStr) return "";
  return dateStr.replaceAll("-", "/");
}

function daysBetween(a, b) {
  return Math.round((b - a) / (1000 * 60 * 60 * 24));
}

function addDays(date, days) {
  const d = new Date(date);
  d.setUTCDate(d.getUTCDate() + days);
  return d;
}

function getDateRange(data) {
  const dates = [];

  data.forEach(item => {
    const s = parseDate(item.start_date);
    const e = parseDate(item.end_date);
    if (s) dates.push(s);
    if (e) dates.push(e);
  });

  if (!dates.length) {
    const today = new Date();
    return [today, today];
  }

  return [
    new Date(Math.min(...dates.map(d => d.getTime()))),
    new Date(Math.max(...dates.map(d => d.getTime())))
  ];
}

function getUnitWidth() {
  if (scaleMode === "day") return 22;
  if (scaleMode === "week") return 28;
  return 70;
}

function getPosition(date, minDate) {
  const diffDays = daysBetween(minDate, date);

  if (scaleMode === "day") return diffDays * getUnitWidth();

  if (scaleMode === "week") {
    return Math.floor(diffDays / 7) * getUnitWidth();
  }

  return monthDiff(minDate, date) * getUnitWidth();
}

function getWidth(start, end) {
  const days = Math.max(1, daysBetween(start, end) + 1);

  if (scaleMode === "day") return Math.max(60, days * getUnitWidth());

  if (scaleMode === "week") {
    return Math.max(70, Math.ceil(days / 7) * getUnitWidth());
  }

  return Math.max(80, Math.max(1, monthDiff(start, end) + 1) * getUnitWidth());
}

function monthDiff(a, b) {
  return (b.getUTCFullYear() - a.getUTCFullYear()) * 12 +
         (b.getUTCMonth() - a.getUTCMonth());
}

function getMonthStart(date) {
  return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1));
}

function makeMonthLabel(date) {
  return `${date.getUTCFullYear()}/${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
}

function makeWeekLabel(date) {
  return `${date.getUTCFullYear()}/${String(date.getUTCMonth() + 1).padStart(2, "0")}/${String(date.getUTCDate()).padStart(2, "0")}`;
}

function filteredData() {
  return rawData.filter(item =>
    selectedPublishers.has(item.publisher) &&
    selectedSales.has(item.sale_name)
  );
}

function unique(arr) {
  return [...new Set(arr)].filter(Boolean).sort();
}

function renderFilters() {
  const publishers = unique(rawData.map(x => x.publisher));
  const sales = unique(
    rawData
      .filter(x => selectedPublishers.size === 0 || selectedPublishers.has(x.publisher))
      .map(x => x.sale_name)
  );

  const publisherBox = document.getElementById("publisherFilters");
  publisherBox.innerHTML = "";

  publishers.forEach(p => {
    publisherBox.appendChild(makeCheckbox("publisher", p, selectedPublishers.has(p), () => {
      if (selectedPublishers.has(p)) selectedPublishers.delete(p);
      else selectedPublishers.add(p);

      const availableSales = new Set(
        rawData
          .filter(x => selectedPublishers.has(x.publisher))
          .map(x => x.sale_name)
      );

      selectedSales = new Set([...selectedSales].filter(s => availableSales.has(s)));
      if (selectedSales.size === 0) selectedSales = new Set(availableSales);

      renderFilters();
      renderRoadmap();
    }));
  });

  const saleBox = document.getElementById("saleFilters");
  saleBox.innerHTML = "";

  sales.forEach(s => {
    saleBox.appendChild(makeCheckbox("sale", s, selectedSales.has(s), () => {
      if (selectedSales.has(s)) selectedSales.delete(s);
      else selectedSales.add(s);
      renderRoadmap();
    }));
  });
}

function makeCheckbox(type, value, checked, onChange) {
  const label = document.createElement("label");
  label.className = "checkbox-item";

  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = checked;
  input.addEventListener("change", onChange);

  label.appendChild(input);
  label.appendChild(document.createTextNode(" " + value));

  return label;
}

function renderTimelineHeader(container, minDate, maxDate, totalWidth) {
  const header = document.createElement("div");
  header.className = "timeline-header";
  header.style.width = `${totalWidth + 180}px`;

  if (scaleMode === "month") {
    let cur = getMonthStart(minDate);
    while (cur <= maxDate) {
      const tick = document.createElement("div");
      tick.className = "tick";
      tick.style.left = `${180 + getPosition(cur, minDate)}px`;
      tick.style.width = `${getUnitWidth()}px`;
      tick.textContent = makeMonthLabel(cur);
      header.appendChild(tick);
      cur = new Date(Date.UTC(cur.getUTCFullYear(), cur.getUTCMonth() + 1, 1));
    }
  } else {
    const step = scaleMode === "week" ? 7 : 1;
    let cur = new Date(minDate);

    while (cur <= maxDate) {
      const tick = document.createElement("div");
      tick.className = "tick";
      tick.style.left = `${180 + getPosition(cur, minDate)}px`;
      tick.style.width = `${getUnitWidth()}px`;
      tick.textContent = scaleMode === "week" ? makeWeekLabel(cur) : String(cur.getUTCDate());
      header.appendChild(tick);
      cur = addDays(cur, step);
    }
  }

  container.appendChild(header);
}

function renderRoadmap() {
  const data = filteredData();
  const roadmap = document.getElementById("roadmap");
  roadmap.innerHTML = "";

  if (!data.length) {
    roadmap.innerHTML = `<div style="padding:24px;color:#6b7280;">No selected sales.</div>`;
    return;
  }

  const [minDate, maxDate] = getDateRange(data);
  const totalWidth = getPosition(maxDate, minDate) + getUnitWidth() * 2;
  roadmap.style.width = `${totalWidth + 180}px`;

  renderTimelineHeader(roadmap, minDate, maxDate, totalWidth);

  const publishers = unique(data.map(x => x.publisher));

  publishers.forEach(pub => {
    const pubData = data.filter(x => x.publisher === pub);

    const row = document.createElement("div");
    row.className = "publisher-row";

    const name = document.createElement("div");
    name.className = "publisher-name";
    name.textContent = pub;
    row.appendChild(name);

    const layer = document.createElement("div");
    layer.className = "sale-layer";

    const lanes = [];

    pubData.forEach((item, index) => {
      const start = parseDate(item.start_date);
      const end = parseDate(item.end_date);
      if (!start || !end) return;

      const left = getPosition(start, minDate);
      const width = getWidth(start, end);

      let lane = 0;
      while (lanes[lane] && lanes[lane] > left) {
        lane++;
      }
      lanes[lane] = left + width + 8;

      const block = document.createElement("div");
      block.className = "sale-block";
      block.style.left = `${left}px`;
      block.style.top = `${18 + lane * 56}px`;
      block.style.width = `${width}px`;
      block.style.background = COLORS[index % COLORS.length];

      block.innerHTML = `
        <div class="sale-name">${escapeHtml(item.sale_name)}</div>
        <div class="sale-date">${formatDate(item.start_date)} - ${formatDate(item.end_date)}</div>
      `;

      block.addEventListener("mousemove", e => showTooltip(e, item));
      block.addEventListener("mouseleave", hideTooltip);
      block.addEventListener("click", () => showDetail(item));

      layer.appendChild(block);
    });

    const rowHeight = Math.max(86, 86 + Math.max(0, lanes.length - 1) * 56);
    row.style.minHeight = `${rowHeight}px`;
    name.style.minHeight = `${rowHeight}px`;
    layer.style.minHeight = `${rowHeight}px`;

    row.appendChild(layer);
    roadmap.appendChild(row);
  });

  document.getElementById("status").textContent =
    `${data.length} sales shown / ${rawData.length} total`;
}

function showTooltip(e, item) {
  const tooltip = document.getElementById("tooltip");
  const games = Array.isArray(item.games) ? item.games : [];

  tooltip.innerHTML = `
    <div class="tooltip-title">${escapeHtml(item.sale_name)}</div>
    <div>${escapeHtml(item.publisher)}</div>
    <div>${formatDate(item.start_date)} - ${formatDate(item.end_date)}</div>
    ${games.length ? `<ul>${games.slice(0, 12).map(g => `<li>${escapeHtml(g)}</li>`).join("")}</ul>` : `<div style="margin-top:6px;opacity:.8;">No game list detected</div>`}
  `;

  tooltip.style.display = "block";
  tooltip.style.left = `${e.clientX + 14}px`;
  tooltip.style.top = `${e.clientY + 14}px`;
}

function hideTooltip() {
  document.getElementById("tooltip").style.display = "none";
}

function showDetail(item) {
  const games = Array.isArray(item.games) ? item.games : [];

  document.getElementById("saleDetail").className = "sale-detail";
  document.getElementById("saleDetail").innerHTML = `
    <div class="detail-grid">
      <div class="detail-label">Publisher</div>
      <div>${escapeHtml(item.publisher)}</div>

      <div class="detail-label">Sale Name</div>
      <div>${escapeHtml(item.sale_name)}</div>

      <div class="detail-label">Date</div>
      <div>${formatDate(item.start_date)} - ${formatDate(item.end_date)}</div>

      <div class="detail-label">Confidence</div>
      <div>${escapeHtml(String(item.sale_confidence ?? ""))}</div>

      <div class="detail-label">Reason</div>
      <div>${escapeHtml(item.sale_reason || "")}</div>

      <div class="detail-label">Source</div>
      <div><a href="${escapeHtml(item.source_url || "#")}" target="_blank">Open Steam News</a></div>

      <div class="detail-label">Games</div>
      <div>
        ${games.length
          ? `<ul class="games-list">${games.map(g => `<li>${escapeHtml(g)}</li>`).join("")}</ul>`
          : "No game list detected"}
      </div>
    </div>
  `;
}

function escapeHtml(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setupButtons() {
  document.getElementById("publisherAll").addEventListener("click", () => {
    selectedPublishers = new Set(unique(rawData.map(x => x.publisher)));
    selectedSales = new Set(unique(rawData.map(x => x.sale_name)));
    renderFilters();
    renderRoadmap();
  });

  document.getElementById("publisherNone").addEventListener("click", () => {
    selectedPublishers.clear();
    selectedSales.clear();
    renderFilters();
    renderRoadmap();
  });

  document.getElementById("saleAll").addEventListener("click", () => {
    selectedSales = new Set(
      unique(rawData.filter(x => selectedPublishers.has(x.publisher)).map(x => x.sale_name))
    );
    renderFilters();
    renderRoadmap();
  });

  document.getElementById("saleNone").addEventListener("click", () => {
    selectedSales.clear();
    renderFilters();
    renderRoadmap();
  });

  document.querySelectorAll(".scale-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".scale-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      scaleMode = btn.dataset.scale;
      renderRoadmap();
    });
  });
}

async function init() {
  setupButtons();

  try {
    const res = await fetch(DATA_URL, { cache: "no-store" });
    if (!res.ok) throw new Error(`Failed to load data: ${res.status}`);

    rawData = await res.json();

    rawData = rawData.filter(x => x.start_date && x.end_date && x.sale_name && x.publisher);

    selectedPublishers = new Set(unique(rawData.map(x => x.publisher)));
    selectedSales = new Set(unique(rawData.map(x => x.sale_name)));

    renderFilters();
    renderRoadmap();

  } catch (err) {
    console.error(err);
    document.getElementById("status").textContent = "Failed to load data";
    document.getElementById("roadmap").innerHTML =
      `<div style="padding:24px;color:#b91c1c;">Failed to load promo_data.json. Please check file path.</div>`;
  }
}

init();

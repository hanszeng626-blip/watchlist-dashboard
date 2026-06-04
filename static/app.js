const MARKETS = ["A股", "港股", "美股"];
const MARKET_META = {
  "A股": { group: "#group-a", count: "#count-a", className: "market-a", currency: "CNY" },
  "港股": { group: "#group-hk", count: "#count-hk", className: "market-hk", currency: "HKD" },
  "美股": { group: "#group-us", count: "#count-us", className: "market-us", currency: "USD" },
};

const form = document.querySelector("#watchlist-form");
const symbolsInput = document.querySelector("#symbols");
const statusLine = document.querySelector("#status-line");
const summary = document.querySelector("#summary");
const analyzeButton = document.querySelector("#analyze-button");
const wealthSummary = document.querySelector("#wealth-summary");
const wealthAdvice = document.querySelector("#wealth-advice");
const wealthList = document.querySelector("#wealth-list");
const groups = Object.fromEntries(MARKETS.map((market) => [market, document.querySelector(MARKET_META[market].group)]));
const counts = Object.fromEntries(MARKETS.map((market) => [market, document.querySelector(MARKET_META[market].count)]));

let latestRecords = [];

const storedSymbols = localStorage.getItem("watchlist.symbols");
if (storedSymbols) {
  symbolsInput.value = storedSymbols;
}

document.querySelectorAll("[data-sample]").forEach((button) => {
  button.addEventListener("click", () => {
    symbolsInput.value = button.dataset.sample;
    form.requestSubmit();
  });
});

document.querySelector("#clear-button").addEventListener("click", () => {
  symbolsInput.value = "";
  latestRecords = [];
  localStorage.removeItem("watchlist.symbols");
  renderResult(emptyPayload());
  setStatus("已清空");
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const symbols = symbolsInput.value.trim();
  if (!symbols) {
    setStatus("请输入至少一只股票代码", true);
    return;
  }

  localStorage.setItem("watchlist.symbols", symbols);
  setLoading(true);
  setStatus("正在抓取行情、K线、板块与新闻...");
  try {
    const response = await fetch(`/api/analyze?symbols=${encodeURIComponent(symbols)}`);
    if (!response.ok) {
      throw new Error(`请求失败：${response.status}`);
    }
    const payload = await response.json();
    renderResult(payload);
    const errorText = payload.errors?.length ? `，${payload.errors.length} 个代码未识别或抓取失败` : "";
    setStatus(`已更新 ${payload.summary.count} 只股票${errorText}`);
  } catch (error) {
    setStatus(error.message || "分析失败", true);
  } finally {
    setLoading(false);
  }
});

function emptyPayload() {
  return { grouped: { "A股": [], "港股": [], "美股": [] }, records: [], errors: [], summary: { count: 0 } };
}

function setLoading(loading) {
  analyzeButton.disabled = loading;
  analyzeButton.textContent = loading ? "分析中" : "分析";
}

function setStatus(message, isError = false) {
  statusLine.textContent = message;
  statusLine.classList.toggle("error", isError);
}

function renderResult(payload) {
  const grouped = payload.grouped || emptyPayload().grouped;
  latestRecords = payload.records || Object.values(grouped).flat();

  for (const market of MARKETS) {
    const records = grouped[market] || [];
    counts[market].textContent = records.length;
    groups[market].innerHTML = records.length
      ? records.map((record) => stockCard(record)).join("")
      : `<div class="empty">暂无${market}自选股</div>`;
  }

  renderSummary(payload.summary || {});
  bindPortfolioInputs();
  bindNotes();
  drawAllCharts();
  renderWealthPanel();
}

function renderSummary(data) {
  if (!data.count) {
    summary.innerHTML = "<span>等待输入</span>";
    return;
  }
  const strong = data.strong ? `${escapeHtml(data.strong.name)} ${data.strong.score}` : "-";
  const weak = data.weak ? `${escapeHtml(data.weak.name)} ${data.weak.score}` : "-";
  summary.innerHTML = `
    <span><strong>${data.count}</strong>标的数量</span>
    <span><strong>${formatValue(data.avg_score)}</strong>平均评分</span>
    <span><strong>${strong}</strong>较强 / 较弱 ${weak}</span>
  `;
}

function stockCard(record) {
  const scoreClass = record.score >= 70 ? "high" : record.score >= 55 ? "mid" : "low";
  const pctClass = record.pct > 0 ? "up" : record.pct < 0 ? "down" : "flat";
  const holding = getHolding(record);
  const levels = record.levels || {};
  const sector = record.sector || {};
  const volume = record.volume_analysis || {};
  const projection = record.projection || {};
  const marketClass = MARKET_META[record.market]?.className || "";
  return `
    <article class="stock-card ${marketClass}">
      <div class="card-top">
        <div>
          <h3 class="stock-name">${escapeHtml(record.name)}</h3>
          <div class="stock-code">${escapeHtml(record.code)} · ${escapeHtml(record.market)} · ${escapeHtml(record.time || "")}</div>
        </div>
        <div class="score ${scoreClass}" title="趋势评分">${record.score}</div>
      </div>

      <div class="quote-line">
        <div class="metric"><span>现价</span><strong>${formatValue(record.price)}</strong></div>
        <div class="metric"><span>涨跌幅</span><strong class="${pctClass}">${formatSigned(record.pct, "%")}</strong></div>
        <div class="metric"><span>20日线距离</span><strong>${formatSigned(record.indicators?.distance_ma20_pct, "%")}</strong></div>
        <div class="metric"><span>量能状态</span><strong>${escapeHtml(volume.label || "-")}</strong></div>
      </div>

      <div class="card-split">
        <section class="chart-panel">
          <div class="mini-chart-head">
            <span>日K线 / 成交量 / 趋势虚拟线</span>
            <strong>${escapeHtml(projection.direction || "-")}</strong>
          </div>
          <canvas class="kline-chart" width="640" height="260" data-code="${escapeHtml(record.code)}"></canvas>
        </section>

        <section class="portfolio-editor">
          <h4>个人持仓</h4>
          <div class="portfolio-row">
            <label>成本<input class="holding-input" data-field="cost" data-key="${holdingKey(record)}" value="${escapeHtml(holding.cost)}" inputmode="decimal" /></label>
            <label>数量<input class="holding-input" data-field="shares" data-key="${holdingKey(record)}" value="${escapeHtml(holding.shares)}" inputmode="decimal" /></label>
          </div>
          <div class="holding-result" data-holding-result="${holdingKey(record)}">${holdingLine(record, holding)}</div>
        </section>
      </div>

      <div class="level-grid">
        <div><span>支撑位</span><strong>${formatValue(levels.support)}</strong></div>
        <div><span>压力位</span><strong>${formatValue(levels.resistance)}</strong></div>
        <div><span>止损参考</span><strong>${formatValue(levels.stop_loss)}</strong></div>
        <div><span>止盈一档</span><strong>${formatValue(levels.take_profit_1)}</strong></div>
        <div><span>止盈二档</span><strong>${formatValue(levels.take_profit_2)}</strong></div>
        <div><span>盈亏比</span><strong>${formatValue(levels.risk_reward)}</strong></div>
      </div>

      <div class="analysis-block">
        <div>
          <h4>技术风险</h4>
          <ul>${(record.risk || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
        </div>
        <div>
          <h4>催化观察</h4>
          <ul>${(record.catalysts || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
        </div>
      </div>

      <div class="sector-block">
        <div>
          <h4>板块 / 概念</h4>
          <p>${escapeHtml(sector.sector || "-")}</p>
          <div class="tags">${(sector.concepts || []).map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>
        </div>
        <div>
          <h4>美股映射</h4>
          <p>${escapeHtml((sector.us_peers || []).join(" / ") || "-")}</p>
          <small>${escapeHtml(sector.mapping_note || "")}</small>
        </div>
      </div>

      <div class="news-block">
        <div>
          <h4>最近新闻</h4>
          ${newsList(record.news)}
        </div>
        <div>
          <h4>板块动态</h4>
          ${newsList(record.sector_news)}
        </div>
      </div>

      <div class="note-block">
        <h4>持仓备注</h4>
        <textarea class="note" data-note-key="${noteStorageKey(record)}" placeholder="仓位计划、加减仓条件、观察点">${escapeHtml(localStorage.getItem(noteStorageKey(record)) || "")}</textarea>
      </div>

      <nav class="links" aria-label="${escapeHtml(record.name)} 外部行情入口">
        ${Object.entries({ ...(record.links || {}), ...(record.news_links || {}) })
          .map(([label, url]) => `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(label)}</a>`)
          .join("")}
      </nav>
    </article>
  `;
}

function newsList(items = []) {
  if (!items.length) {
    return '<p class="muted-text">暂无新闻，建议点击新闻入口查看。</p>';
  }
  return `<ul class="news-list">${items
    .slice(0, 3)
    .map((item) => `<li><a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.title)}</a></li>`)
    .join("")}</ul>`;
}

function bindPortfolioInputs() {
  document.querySelectorAll(".holding-input").forEach((input) => {
    input.addEventListener("input", () => {
      const key = input.dataset.key;
      const record = latestRecords.find((item) => holdingKey(item) === key);
      const holding = getHolding(record);
      holding[input.dataset.field] = input.value;
      localStorage.setItem(key, JSON.stringify(holding));
      const result = document.querySelector(`[data-holding-result="${cssEscape(key)}"]`);
      if (result && record) result.innerHTML = holdingLine(record, holding);
      renderWealthPanel();
    });
  });
}

function bindNotes() {
  document.querySelectorAll(".note").forEach((note) => {
    note.addEventListener("input", () => {
      localStorage.setItem(note.dataset.noteKey, note.value);
    });
  });
}

function drawAllCharts() {
  latestRecords.forEach((record) => {
    const canvas = document.querySelector(`.kline-chart[data-code="${cssEscape(record.code)}"]`);
    if (canvas) drawChart(canvas, record);
  });
}

function drawChart(canvas, record) {
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);

  const rows = (record.history || []).filter((row) => row.close && row.high && row.low).slice(-56);
  if (rows.length < 5) {
    ctx.fillStyle = "#66717d";
    ctx.font = "22px Microsoft YaHei";
    ctx.fillText("K线样本不足", 24, 120);
    return;
  }

  const pad = { left: 40, right: 18, top: 18, bottom: 52 };
  const priceHeight = 150;
  const volumeTop = 182;
  const maxPrice = Math.max(...rows.map((row) => row.high));
  const minPrice = Math.min(...rows.map((row) => row.low));
  const maxVolume = Math.max(...rows.map((row) => row.volume || 0), 1);
  const slot = (width - pad.left - pad.right) / rows.length;
  const candle = Math.max(4, slot * 0.56);
  const scaleY = (price) => pad.top + ((maxPrice - price) / Math.max(maxPrice - minPrice, 0.01)) * priceHeight;

  ctx.strokeStyle = "rgba(103,113,125,0.24)";
  ctx.lineWidth = 1;
  for (let i = 0; i < 4; i += 1) {
    const y = pad.top + (priceHeight / 3) * i;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
  }

  rows.forEach((row, index) => {
    const x = pad.left + index * slot + slot / 2;
    const openY = scaleY(row.open || row.close);
    const closeY = scaleY(row.close);
    const highY = scaleY(row.high);
    const lowY = scaleY(row.low);
    const up = row.close >= (row.open || row.close);
    const color = up ? "#cf3f32" : "#13845f";
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(x, highY);
    ctx.lineTo(x, lowY);
    ctx.stroke();
    ctx.fillRect(x - candle / 2, Math.min(openY, closeY), candle, Math.max(2, Math.abs(closeY - openY)));

    const volHeight = ((row.volume || 0) / maxVolume) * 46;
    ctx.globalAlpha = 0.45;
    ctx.fillRect(x - candle / 2, volumeTop + 48 - volHeight, candle, volHeight);
    ctx.globalAlpha = 1;
  });

  drawLine(ctx, rows.map((row) => row.close), scaleY, pad.left, slot, "#225ea8", 2);
  drawProjection(ctx, record.projection?.points || [], rows.at(-1)?.close, scaleY, pad.left + (rows.length - 1) * slot + slot / 2, slot);

  ctx.fillStyle = "#66717d";
  ctx.font = "18px Microsoft YaHei";
  ctx.fillText(formatValue(maxPrice), 4, pad.top + 8);
  ctx.fillText(formatValue(minPrice), 4, pad.top + priceHeight);
  ctx.fillText("量", 10, volumeTop + 40);
}

function drawLine(ctx, values, scaleY, left, slot, color, width) {
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.beginPath();
  values.forEach((value, index) => {
    const x = left + index * slot + slot / 2;
    const y = scaleY(value);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function drawProjection(ctx, points, lastClose, scaleY, startX, slot) {
  if (!points.length || !lastClose) return;
  ctx.strokeStyle = "#8b4bd9";
  ctx.lineWidth = 2;
  ctx.setLineDash([8, 6]);
  ctx.beginPath();
  ctx.moveTo(startX, scaleY(lastClose));
  points.forEach((point, index) => {
    ctx.lineTo(startX + (index + 1) * slot, scaleY(point.price));
  });
  ctx.stroke();
  ctx.setLineDash([]);
}

function renderWealthPanel() {
  const rows = latestRecords.map((record) => ({ record, holding: getHolding(record) })).filter(({ holding }) => Number(holding.cost) > 0 && Number(holding.shares) > 0);
  const enriched = rows.map(({ record, holding }) => {
    const cost = Number(holding.cost);
    const shares = Number(holding.shares);
    const price = Number(record.price || 0);
    const marketValue = price * shares;
    const costValue = cost * shares;
    const profit = marketValue - costValue;
    const profitPct = costValue ? (profit / costValue) * 100 : 0;
    return { record, cost, shares, price, marketValue, costValue, profit, profitPct };
  });

  const totalValue = enriched.reduce((sum, item) => sum + item.marketValue, 0);
  const totalCost = enriched.reduce((sum, item) => sum + item.costValue, 0);
  const totalProfit = totalValue - totalCost;
  const totalPct = totalCost ? (totalProfit / totalCost) * 100 : null;

  wealthSummary.innerHTML = `
    <div><span>总市值</span><strong>${formatValue(totalValue)}</strong></div>
    <div><span>总成本</span><strong>${formatValue(totalCost)}</strong></div>
    <div><span>总盈亏</span><strong class="${totalProfit >= 0 ? "up" : "down"}">${formatSigned(totalProfit)}</strong></div>
    <div><span>总体收益率</span><strong class="${(totalPct || 0) >= 0 ? "up" : "down"}">${formatSigned(totalPct, "%")}</strong></div>
  `;

  const byMarket = Object.fromEntries(MARKETS.map((market) => [market, enriched.filter((item) => item.record.market === market)]));
  wealthList.innerHTML = MARKETS.map((market) => marketWealthBlock(market, byMarket[market])).join("");
  wealthAdvice.textContent = buildWealthAdvice(enriched, totalPct);
}

function marketWealthBlock(market, rows) {
  const value = rows.reduce((sum, item) => sum + item.marketValue, 0);
  const cost = rows.reduce((sum, item) => sum + item.costValue, 0);
  const pct = cost ? ((value - cost) / cost) * 100 : null;
  return `
    <section class="wealth-market ${MARKET_META[market].className}">
      <div class="wealth-market-head">
        <strong>${market}</strong>
        <span>${formatValue(value)} / ${formatSigned(pct, "%")}</span>
      </div>
      ${rows.length ? rows.map(wealthRow).join("") : '<p class="muted-text">暂无持仓数据</p>'}
    </section>
  `;
}

function wealthRow(item) {
  return `
    <div class="wealth-row">
      <div>
        <strong>${escapeHtml(item.record.name)}</strong>
        <span>${escapeHtml(item.record.code)} · ${formatValue(item.shares)}股</span>
      </div>
      <div class="${item.profit >= 0 ? "up" : "down"}">
        <strong>${formatSigned(item.profit)}</strong>
        <span>${formatSigned(item.profitPct, "%")}</span>
      </div>
    </div>
  `;
}

function buildWealthAdvice(rows, totalPct) {
  if (!rows.length) return "财富管理建议：先补充成本和持股数量，系统会汇总各市场持仓盈亏。";
  const concentration = rows.reduce((max, item) => Math.max(max, item.marketValue), 0) / rows.reduce((sum, item) => sum + item.marketValue, 0);
  if (concentration > 0.55) return "财富管理建议：单一持仓占比较高，建议重点跟踪止损线和仓位集中风险。";
  if ((totalPct || 0) > 20) return "财富管理建议：组合收益较高，可分批锁定部分利润，并保留强趋势仓位。";
  if ((totalPct || 0) < -10) return "财富管理建议：组合回撤较大，优先检查弱势票是否跌破关键支撑，避免亏损扩大。";
  return "财富管理建议：组合处于可观察区间，建议按市场分散、按技术位执行加减仓。";
}

function holdingLine(record, holding) {
  const cost = Number(holding.cost);
  const shares = Number(holding.shares);
  const price = Number(record.price || 0);
  if (!(cost > 0 && shares > 0 && price > 0)) return "填写成本和数量后自动计算盈亏。";
  const profit = (price - cost) * shares;
  const pct = ((price - cost) / cost) * 100;
  return `<span class="${profit >= 0 ? "up" : "down"}">${formatSigned(profit)} / ${formatSigned(pct, "%")}</span>`;
}

function getHolding(record) {
  if (!record) return { cost: "", shares: "" };
  try {
    return { cost: "", shares: "", ...JSON.parse(localStorage.getItem(holdingKey(record)) || "{}") };
  } catch {
    return { cost: "", shares: "" };
  }
}

function holdingKey(record) {
  return `watchlist.holding.${record.market}.${record.code}`;
}

function noteStorageKey(record) {
  return `watchlist.note.${record.market}.${record.code}`;
}

function formatValue(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const number = Number(value);
  if (Math.abs(number) >= 1000) return number.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  return number.toLocaleString("zh-CN", { maximumFractionDigits: 2, minimumFractionDigits: 0 });
}

function formatSigned(value, suffix = "") {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const number = Number(value);
  const sign = number > 0 ? "+" : "";
  return `${sign}${number.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}${suffix}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function cssEscape(value) {
  if (window.CSS?.escape) return CSS.escape(value);
  return String(value).replaceAll('"', '\\"').replaceAll("\\", "\\\\");
}

const urlSymbols = new URLSearchParams(window.location.search).get("symbols");
if (urlSymbols) {
  symbolsInput.value = urlSymbols;
  window.setTimeout(() => form.requestSubmit(), 0);
} else if (storedSymbols) {
  window.setTimeout(() => form.requestSubmit(), 0);
} else {
  renderResult(emptyPayload());
}

const form = document.querySelector("#watchlist-form");
const symbolsInput = document.querySelector("#symbols");
const statusLine = document.querySelector("#status-line");
const summary = document.querySelector("#summary");
const analyzeButton = document.querySelector("#analyze-button");
const groups = {
  "A股": document.querySelector("#group-a"),
  "港股": document.querySelector("#group-hk"),
  "美股": document.querySelector("#group-us"),
};
const counts = {
  "A股": document.querySelector("#count-a"),
  "港股": document.querySelector("#count-hk"),
  "美股": document.querySelector("#count-us"),
};

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
  localStorage.removeItem("watchlist.symbols");
  renderResult({ grouped: { "A股": [], "港股": [], "美股": [] }, errors: [], summary: { count: 0 } });
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
  setStatus("正在抓取行情和K线...");
  try {
    const response = await fetch(`/api/analyze?symbols=${encodeURIComponent(symbols)}`);
    if (!response.ok) {
      throw new Error(`请求失败：${response.status}`);
    }
    const payload = await response.json();
    renderResult(payload);
    const errorText = payload.errors?.length
      ? `；${payload.errors.length} 个代码未识别或抓取失败`
      : "";
    setStatus(`已更新 ${payload.summary.count} 只股票${errorText}`);
  } catch (error) {
    setStatus(error.message || "分析失败", true);
  } finally {
    setLoading(false);
  }
});

function setLoading(loading) {
  analyzeButton.disabled = loading;
  analyzeButton.textContent = loading ? "分析中" : "分析";
}

function setStatus(message, isError = false) {
  statusLine.textContent = message;
  statusLine.classList.toggle("error", isError);
}

function renderResult(payload) {
  const grouped = payload.grouped || { "A股": [], "港股": [], "美股": [] };
  for (const market of Object.keys(groups)) {
    const records = grouped[market] || [];
    counts[market].textContent = records.length;
    groups[market].innerHTML = records.length
      ? records.map((record) => stockCard(record)).join("")
      : `<div class="empty">暂无${market}自选股</div>`;
  }

  renderSummary(payload.summary || {});
  bindNotes();
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
  const noteKey = noteStorageKey(record);
  const note = localStorage.getItem(noteKey) || "";
  return `
    <article class="stock-card">
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
        <div class="metric"><span>量能比</span><strong>${formatValue(record.indicators?.volume_ratio)}</strong></div>
      </div>

      <div class="analysis-block">
        <div>
          <h4>风险</h4>
          <ul>${record.risk.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
        </div>
        <div>
          <h4>催化</h4>
          <ul>${record.catalysts.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
        </div>
      </div>

      <div class="note-block">
        <h4>持仓备注</h4>
        <textarea class="note" data-note-key="${noteKey}" placeholder="仓位、成本、止损线、观察点">${escapeHtml(note)}</textarea>
      </div>

      <nav class="links" aria-label="${escapeHtml(record.name)} 外部行情入口">
        ${Object.entries(record.links)
          .map(([label, url]) => `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(label)}</a>`)
          .join("")}
      </nav>
    </article>
  `;
}

function bindNotes() {
  document.querySelectorAll(".note").forEach((note) => {
    note.addEventListener("input", () => {
      localStorage.setItem(note.dataset.noteKey, note.value);
    });
  });
}

function noteStorageKey(record) {
  return `watchlist.note.${record.market}.${record.code}`;
}

function formatValue(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "-";
  }
  const number = Number(value);
  if (Math.abs(number) >= 1000) {
    return number.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  }
  return number.toLocaleString("zh-CN", { maximumFractionDigits: 2, minimumFractionDigits: 0 });
}

function formatSigned(value, suffix = "") {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "-";
  }
  const number = Number(value);
  const sign = number > 0 ? "+" : "";
  return `${sign}${number.toFixed(2)}${suffix}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

const urlSymbols = new URLSearchParams(window.location.search).get("symbols");
if (urlSymbols) {
  symbolsInput.value = urlSymbols;
  window.setTimeout(() => form.requestSubmit(), 0);
} else if (storedSymbols) {
  window.setTimeout(() => form.requestSubmit(), 0);
} else {
  renderResult({ grouped: { "A股": [], "港股": [], "美股": [] }, errors: [], summary: { count: 0 } });
}

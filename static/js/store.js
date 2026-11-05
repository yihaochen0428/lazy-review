import { api } from "./api.js";

const { reactive } = Vue;

const WATCH_KEY = "lazy-review-watchlist";

function readWatch() {
  try {
    const raw = JSON.parse(window.localStorage.getItem(WATCH_KEY) || "[]");
    return Array.isArray(raw) ? raw : [];
  } catch (err) {
    return [];
  }
}

export const state = reactive({
  route: "dashboard",
  indexCode: "000001",
  indexPeriod: 101,
  indexData: null,
  meta: null,
  dash: null,
  loading: true,
  error: "",
  refreshing: false,
  history: [],
  sector: { open: false, loading: false, code: "", name: "", sort: "f3", data: null, error: "" },
  stock: { open: false, loading: false, code: "", name: "", period: 101, data: null, error: "" },
  news: { loading: false, data: null, error: "" },
  abnormal: { loading: false, data: null, error: "" },
  watch: { codes: readWatch(), items: [], updated_at: "", loading: false },
});

export function go(route, code) {
  state.route = route;
  if (code) state.indexCode = code;
  const hash = `#/${route}${code ? `/${code}` : ""}`;
  if (window.location.hash !== hash) window.location.hash = hash;
}

export async function loadDashboard({ force = false, date = null } = {}) {
  state.loading = true;
  state.error = "";
  try {
    state.dash = await api.dashboard(date);
  } catch (err) {
    state.error = err.message || "数据加载失败";
  } finally {
    state.loading = false;
  }
  try {
    state.history = (await api.history()).items;
  } catch (err) {
    state.history = [];
  }
}

export async function refresh() {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    await api.refresh(state.dash ? state.dash.date : null);
    await loadDashboard({ date: state.dash ? state.dash.date : null });
  } catch (err) {
    state.error = err.message || "刷新失败";
  } finally {
    state.refreshing = false;
  }
}

export async function switchDate(date) {
  if (!date) return;
  await loadDashboard({ date });
}

/* ------------------------------------------------------------ 板块抽屉 */
export async function openSector(code, name) {
  state.sector.open = true;
  state.sector.code = code;
  state.sector.name = name || "";
  state.sector.sort = "f3";
  await fetchSector();
}

export async function fetchSector() {
  state.sector.loading = true;
  state.sector.error = "";
  try {
    state.sector.data = await api.sector(state.sector.code, state.sector.sort);
  } catch (err) {
    state.sector.error = err.message || "板块数据加载失败";
  } finally {
    state.sector.loading = false;
  }
}

export async function sortSector(sort) {
  if (state.sector.sort === sort) return;
  state.sector.sort = sort;
  await fetchSector();
}

export function closeSector() {
  state.sector.open = false;
}

/* ------------------------------------------------------------ 个股弹层 */
export async function openStock(code, name = "") {
  state.stock.open = true;
  state.stock.code = code;
  state.stock.name = name;
  state.stock.period = 101;
  await fetchStock();
}

export async function fetchStock(silent = false) {
  if (!silent) state.stock.loading = true;
  if (!silent) state.stock.error = "";
  try {
    state.stock.data = await api.stock(state.stock.code, state.stock.period);
  } catch (err) {
    // 静默轮询失败时保留上一次的数据，不打断阅读
    if (!silent) state.stock.error = err.message || "个股数据加载失败";
  } finally {
    if (!silent) state.stock.loading = false;
  }
}

export async function switchPeriod(period) {
  if (state.stock.period === period) return;
  state.stock.period = period;
  await fetchStock();
}

export function closeStock() {
  state.stock.open = false;
}

export function closeOverlays() {
  closeSector();
  closeStock();
}

/* ------------------------------------------------------------ 指数研判 */
export async function loadIndex(code, period = null, { silent = false } = {}) {
  state.indexCode = code;
  if (period) state.indexPeriod = period;
  if (!silent) {
    state.loading = true;
    state.error = "";
  }
  try {
    state.indexData = await api.index(code, state.indexPeriod);
  } catch (err) {
    if (!silent) {
      state.error = err.message || "指数研判数据加载失败";
      state.indexData = null;
    }
  } finally {
    if (!silent) state.loading = false;
  }
}

/* ------------------------------------------------------------ 新闻页 */
export async function loadNews(force = false) {
  if (state.news.data && !force) return;
  state.news.loading = true;
  state.news.error = "";
  try {
    state.news.data = await api.news();
  } catch (err) {
    state.news.error = err.message || "新闻加载失败";
  } finally {
    state.news.loading = false;
  }
}

/* ------------------------------------------------------------ 异动监控 */
export async function loadAbnormal(force = false) {
  if (state.abnormal.data && !force) return;
  state.abnormal.loading = true;
  state.abnormal.error = "";
  try {
    state.abnormal.data = await api.abnormal();
  } catch (err) {
    state.abnormal.error = err.message || "异动监控加载失败";
  } finally {
    state.abnormal.loading = false;
  }
}

/* ------------------------------------------------------------ 自选股 */
function persistWatch() {
  window.localStorage.setItem(WATCH_KEY, JSON.stringify(state.watch.codes));
}

export function isWatched(code) {
  return state.watch.codes.some((item) => item.code === code);
}

export function toggleWatch(stock) {
  const code = typeof stock === "string" ? stock : stock.code;
  const existing = state.watch.codes.findIndex((item) => item.code === code);
  if (existing >= 0) {
    state.watch.codes.splice(existing, 1);
    state.watch.items = state.watch.items.filter((item) => item.code !== code);
  } else {
    const name = (typeof stock === "object" && stock.name) || "";
    state.watch.codes.push({ code, name });
  }
  persistWatch();
}

export async function loadWatch() {
  if (!state.watch.codes.length) {
    state.watch.items = [];
    state.watch.updated_at = "";
    return;
  }
  state.watch.loading = true;
  try {
    const res = await api.quotes(state.watch.codes.map((item) => item.code));
    state.watch.items = res.items || [];
    state.watch.updated_at = res.updated_at || "";
    state.watch.items.forEach((item) => {
      if (item.name && item.name !== item.code) {
        const target = state.watch.codes.find((c) => c.code === item.code);
        if (target && !target.name) target.name = item.name;
      }
    });
  } catch (err) {
    state.watch.items = state.watch.codes.map((item) => ({ ...item, price: null, change_pct: null }));
  } finally {
    state.watch.loading = false;
  }
}

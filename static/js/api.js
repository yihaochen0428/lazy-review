async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Accept": "application/json" },
    ...options,
  });
  let data = null;
  try {
    data = await response.json();
  } catch (err) {
    throw new Error(`数据解析失败 (${response.status})`);
  }
  if (!response.ok || (data && data.error)) {
    throw new Error((data && data.error) || `请求失败 (${response.status})`);
  }
  return data;
}

export const api = {
  meta: () => request("/api/meta"),
  dashboard: (date) => request(`/api/dashboard${date ? `?date=${encodeURIComponent(date)}` : ""}`),
  refresh: (date) => request(`/api/refresh${date ? `?date=${encodeURIComponent(date)}` : ""}`, { method: "POST" }),
  sectors: (type, sort) => request(`/api/sectors?type=${type}&sort=${sort}`),
  sector: (code, sort = "f3") => request(`/api/sector/${code}?sort=${sort}`),
  stock: (code, period = 101) => request(`/api/stock/${code}?klt=${period}`),
  index: (code, period = 101) => request(`/api/index/${code}?klt=${period}`),
  history: () => request("/api/history"),
  news: () => request("/api/news"),
  newsArchive: (days = 90, impact = "全部", scope = "全部", perDay = 6, day = "") =>
    request(`/api/news/archive?days=${days}&impact=${encodeURIComponent(impact)}` +
      `&scope=${encodeURIComponent(scope)}&per_day=${perDay}${day ? `&day=${day}` : ""}`),
  newsAnalyze: (item) =>
    request("/api/news/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(item),
    }),
  abnormal: () => request("/api/abnormal"),
  tBacktest: (days = 10, force = false) => request(`/api/t/backtest?days=${days}&force=${force}`),
  tAdvanced: (code, force = false) => request(`/api/t/advanced/${encodeURIComponent(code)}?force=${force}`),
  openBacktest: (days = 20, force = false) => request(`/api/open/backtest?days=${days}&force=${force}`),
  kSignalBacktest: (days = 60, force = false) => request(`/api/k/signals/backtest?days=${days}&force=${force}`),
  search: (q) => request(`/api/search?q=${encodeURIComponent(q)}`),
  quotes: (codes) => request(`/api/quotes?codes=${codes.map(encodeURIComponent).join(",")}`),
};

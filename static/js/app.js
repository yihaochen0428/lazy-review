import { api } from "./api.js";
import {
  closeOverlays, closeSector, closeStock, go, loadDashboard, loadIndex, loadNews, loadWatch,
  openStock, refresh, state, switchDate,
} from "./store.js";
import DashboardView from "./views/dashboard.js";
import ShortView from "./views/short.js";
import IndexView from "./views/index.js";
import NewsView from "./views/news.js";
import WatchView from "./views/watchlist.js";
import SectorDrawer from "./views/sector.js";
import StockModal from "./views/stock.js";

const { createApp, computed, onMounted, ref, watch } = Vue;

const ROUTES = ["dashboard", "short", "index", "news", "watch"];

function parseHash() {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [name, code] = raw.split("/");
  if (ROUTES.includes(name)) {
    state.route = name;
    if (name === "index" && code) state.indexCode = code;
  } else {
    state.route = "dashboard";
  }
}

const App = {
  components: { DashboardView, ShortView, IndexView, NewsView, WatchView, SectorDrawer, StockModal },
  setup() {
    const history = computed(() => state.history || []);
    const dateValue = computed(() => (state.dash ? state.dash.date : ""));
    const keyword = ref("");
    const results = ref([]);
    const searching = ref(false);
    let timer = null;

    function searchInput() {
      window.clearTimeout(timer);
      const value = keyword.value.trim();
      if (!value) {
        results.value = [];
        return;
      }
      timer = window.setTimeout(async () => {
        searching.value = true;
        try {
          const res = await api.search(value);
          results.value = res.items || [];
        } catch (err) {
          results.value = [];
        } finally {
          searching.value = false;
        }
      }, 260);
    }

    function pickStock(item) {
      keyword.value = "";
      results.value = [];
      openStock(item.code, item.name);
    }

    onMounted(async () => {
      parseHash();
      if (state.route === "index") await loadIndex(state.indexCode);
      else if (state.route === "watch") await loadWatch();
      else await loadDashboard();
      api.meta().then((meta) => { state.meta = meta; }).catch(() => {});
      window.addEventListener("hashchange", () => {
        const previous = state.route;
        parseHash();
        if (state.route === "index" && (previous !== "index" || state.indexData?.code !== state.indexCode)) {
          loadIndex(state.indexCode);
        } else if (state.route === "watch") {
          loadWatch();
        } else if (state.route !== "index" && state.route !== "news" && !state.dash) {
          loadDashboard();
        }
      });
      window.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") return;
        if (results.value.length) results.value = [];
        else if (state.stock.open) closeStock();
        else if (state.sector.open) closeSector();
      });
      watch(
        () => state.stock.open || state.sector.open,
        (open) => { document.body.style.overflow = open ? "hidden" : ""; }
      );
    });

    function onDateChange(event) {
      switchDate(event.target.value);
    }

    function nav(route) {
      go(route);
    }

    return {
      state, history, dateValue, refresh, onDateChange, nav, closeOverlays, closeSector,
      keyword, results, searching, searchInput, pickStock, go,
    };
  },
  template: `
  <div class="app-shell">
    <header class="topbar">
      <div class="topbar-inner">
        <div class="brand" @click="nav('dashboard')">
          <div class="brand-mark">
            <svg viewBox="0 0 64 64" fill="none">
              <path d="M36 6 18 36h12l-4 22 22-32H34z" fill="#0b0e14" />
            </svg>
          </div>
          <div>
            <div class="brand-name">懒得复盘</div>
            <div class="brand-sub">LAZY REVIEW · A-SHARE</div>
          </div>
        </div>

        <nav class="nav">
          <button class="nav-item" :class="{ active: state.route === 'dashboard' }" @click="nav('dashboard')">复盘看板</button>
          <button class="nav-item" :class="{ active: state.route === 'short' }" @click="nav('short')">短线复盘</button>
          <button class="nav-item" :class="{ active: state.route === 'index' }" @click="nav('index')">指数研判</button>
          <button class="nav-item" :class="{ active: state.route === 'news' }" @click="nav('news')">新闻</button>
          <button class="nav-item" :class="{ active: state.route === 'watch' }" @click="nav('watch')">自选</button>
        </nav>

        <div class="topbar-right">
          <div class="search-wrap">
            <input class="search" v-model="keyword" @input="searchInput" placeholder="搜索代码 / 名称 / 拼音" />
            <div class="search-drop" v-if="results.length">
              <button class="search-item" v-for="r in results" :key="r.code" @click="pickStock(r)">
                <b>{{ r.name }}</b><span class="num">{{ r.code }}</span>
              </button>
            </div>
            <div class="search-drop empty" v-else-if="searching && keyword">
              搜索中…
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:7px" class="muted small">
            <span class="status-dot" :class="{ warn: state.error }"></span>
            <span class="num">{{ state.dash ? state.dash.date : '-' }}</span>
          </div>
          <select class="select" style="height:36px" :value="dateValue" @change="onDateChange" v-if="history.length">
            <option v-for="item in history" :key="item.date" :value="item.date">
              {{ item.date }}{{ item.cached ? ' · 已存' : '' }}
            </option>
          </select>
          <button class="refresh-btn" :class="{ spinning: state.refreshing }" @click="refresh" :disabled="state.refreshing">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6" stroke-linecap="round" />
            </svg>
            {{ state.refreshing ? '刷新中' : '刷新' }}
          </button>
        </div>
      </div>
    </header>

    <div v-if="state.loading" style="height:2px;background:linear-gradient(90deg,transparent,#f2b845,transparent);background-size:200% 100%;animation:shimmer 1.2s infinite linear;position:fixed;top:0;left:0;right:0;z-index:45"></div>

    <DashboardView v-if="state.route === 'dashboard'" />
    <ShortView v-else-if="state.route === 'short'" />
    <IndexView v-else-if="state.route === 'index'" />
    <NewsView v-else-if="state.route === 'news'" />
    <WatchView v-else-if="state.route === 'watch'" />

    <SectorDrawer />
    <StockModal />

    <footer class="footer">
      <div>懒得复盘 · 数据来源：东方财富公开行情接口 · 结果按交易日缓存，5 分钟自动更新</div>
      <div>本站所有内容仅为盘面结构化整理与技术指标计算，不构成投资建议，股市有风险，入市需谨慎。</div>
    </footer>
  </div>`,
};

createApp(App).mount("#app");

import { loadWatch, openStock, state, toggleWatch } from "../store.js";
import { MiniCandle } from "../components.js";
import { fmtAmount, fmtFlow, fmtPct, fmtPrice, intraPath, tone } from "../utils.js";

export default {
  components: { MiniCandle },
  setup() {
    const { computed, onMounted, onUnmounted } = Vue;
    let timer = null;

    onMounted(async () => {
      await loadWatch();
      timer = window.setInterval(() => loadWatch(), 5000);
    });
    onUnmounted(() => {
      if (timer) window.clearInterval(timer);
    });

    const items = computed(() => state.watch.items);
    const codes = computed(() => state.watch.codes);

    // 预计算每只自选的分时 path，避免模板里重复计算
    const rows = computed(() => items.value.map((item) => ({
      ...item,
      spark: item.intra ? intraPath(item.intra.pct, 150, 36) : null,
    })));

    const overview = computed(() => {
      const list = items.value.filter((i) => i.change_pct !== null && i.change_pct !== undefined);
      if (!list.length) return null;
      const avg = list.reduce((sum, i) => sum + i.change_pct, 0) / list.length;
      const up = list.filter((i) => i.change_pct > 0).length;
      const down = list.filter((i) => i.change_pct < 0).length;
      return { avg: avg.toFixed(2), up, down, total: list.length };
    });

    function remove(item) {
      toggleWatch({ code: item.code, name: item.name });
    }

    function detail(item) {
      openStock(item.code, item.name);
    }

    return {
      state, items, rows, codes, overview, loadWatch, remove, detail,
      fmtAmount, fmtFlow, fmtPct, fmtPrice, tone,
    };
  },
  template: `
  <div class="page">
    <div class="page-head">
      <div>
        <div class="page-title">自选股</div>
        <div class="page-desc">
          自选实时监控 · 每 5 秒自动刷新 · 行内嵌今日分时走势（红涨绿跌，金色虚线为昨收）
          <template v-if="state.watch.updated_at"> · {{ state.watch.updated_at }} 更新</template>
        </div>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <button class="ghost-btn" @click="loadWatch">刷新行情</button>
      </div>
    </div>

    <section class="kpi-grid watch-kpi" v-if="overview">
      <div class="kpi">
        <div class="kpi-label">自选数量</div>
        <div class="kpi-value num">{{ overview.total }}</div>
        <div class="kpi-foot">共 {{ codes.length }} 只</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">平均涨跌</div>
        <div class="kpi-value num" :class="tone(Number(overview.avg))">{{ fmtPct(Number(overview.avg)) }}</div>
        <div class="kpi-foot">自选组合表现</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">上涨 / 下跌</div>
        <div class="kpi-value num"><span class="up">{{ overview.up }}</span><span class="muted"> / </span><span class="down">{{ overview.down }}</span></div>
        <div class="kpi-foot">当日强弱分布</div>
      </div>
    </section>

    <div v-if="!codes.length" class="card">
      <div class="empty">
        <b>还没有自选股</b>
        用顶部搜索框查找个股，在个股详情右上角点击「加自选」即可加入实时监控
      </div>
    </div>

    <section class="watch-list" v-else>
      <div class="card watch-row" v-for="item in rows" :key="item.code" @click="detail(item)">
        <div class="wr-main">
          <div class="watch-name">{{ item.name }}</div>
          <div class="num muted small">{{ item.code }}</div>
        </div>
        <div class="wr-quote">
          <div class="watch-price num" :class="tone(item.change_pct)">{{ fmtPrice(item.price) }}</div>
          <div class="watch-pct num" :class="tone(item.change_pct)">{{ fmtPct(item.change_pct) }}</div>
        </div>
        <svg v-if="item.spark" class="wr-spark" width="150" height="36" viewBox="0 0 150 36" preserveAspectRatio="none">
          <line x1="2" x2="148" :y1="item.spark.zero" :y2="item.spark.zero"
                stroke="rgba(242,184,69,.55)" stroke-dasharray="3 2" stroke-width="1" />
          <path :d="item.spark.up" fill="none" stroke="#ff5063"
                stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
          <path :d="item.spark.down" fill="none" stroke="#14c39a"
                stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        <MiniCandle v-else :bars="item.bars || []" :prev-close="item.prev_close" :height="36" />
        <div class="wr-metrics">
          <div class="wr-metric">
            <span class="muted">换手率</span>
            <b class="num">{{ item.turnover != null ? item.turnover + '%' : '-' }}</b>
          </div>
          <div class="wr-metric">
            <span class="muted">量比</span>
            <b class="num" :class="{ gold: item.volume_ratio >= 1.5 }">{{ item.volume_ratio != null ? item.volume_ratio : '-' }}</b>
          </div>
          <div class="wr-metric">
            <span class="muted">涨速 5m</span>
            <b class="num" :class="tone(item.speed_5m)">{{ item.speed_5m != null ? fmtPct(item.speed_5m) : '-' }}</b>
          </div>
          <div class="wr-metric">
            <span class="muted">主力净额</span>
            <b class="num flow-chip" :class="tone(item.main_flow)">{{ fmtFlow(item.main_flow) }}</b>
          </div>
          <div class="wr-metric">
            <span class="muted">成交量</span>
            <b class="num">{{ item.volume != null ? fmtAmount(item.volume) + '手' : '-' }}</b>
          </div>
          <div class="wr-metric">
            <span class="muted">成交额</span>
            <b class="num">{{ fmtAmount(item.amount) }}</b>
          </div>
        </div>
        <div class="wr-side">
          <span class="tag" :class="item.change_pct >= 0 ? 'hot' : 'cold'">
            {{ item.change_pct >= 0 ? '实时上行' : '实时下行' }}
          </span>
          <button class="watch-remove" @click.stop="remove(item)" title="移除自选">✕</button>
        </div>
      </div>
      <div v-for="item in codes.filter((c) => !items.some((i) => i.code === c.code))" :key="item.code" class="card watch-row stale">
        <div class="wr-main">
          <div class="watch-name">{{ item.name || item.code }}</div>
          <div class="num muted small">{{ item.code }}</div>
        </div>
        <div class="empty" style="padding:6px 0">行情加载中…</div>
        <div class="wr-side">
          <button class="watch-remove" @click.stop="toggleWatch({ code: item.code, name: item.name })">✕</button>
        </div>
      </div>
    </section>
  </div>`,
};

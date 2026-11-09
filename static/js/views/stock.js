import { closeStock, fetchStock, isWatched, openStock, state, switchPeriod, toggleWatch } from "../store.js";
import { api } from "../api.js";
import { Chart, MiniCandle } from "../components.js";
import { intradayOption, klineOption } from "../charts.js";
import { fmtAmount, fmtNumber, fmtPct, fmtPrice, isMarketOpen, tone } from "../utils.js";

function lastValue(series) {
  if (!series) return null;
  for (let i = series.length - 1; i >= 0; i -= 1) {
    if (series[i] !== null && series[i] !== undefined) return series[i];
  }
  return null;
}

// 指标卡片排序偏好：只存本地，不依赖账号
const IND_ORDER_KEY = "lazy-review:ind-order";
const IND_LOCK_KEY = "lazy-review:ind-lock";

function readIndOrder() {
  try {
    const arr = JSON.parse(localStorage.getItem(IND_ORDER_KEY) || "[]");
    return Array.isArray(arr) ? arr.map(String) : [];
  } catch (err) {
    return [];
  }
}

export default {
  components: { Chart, MiniCandle },
  setup() {
    const { computed, onMounted, onUnmounted, ref, watch } = Vue;
    const indicator = ref("MACD");
    const chanOn = ref(false);
    const detailsEl = ref(null);
    // 指标卡片：拖拽排序 + 锁定
    const indOrder = ref(readIndOrder());
    const indLocked = ref(localStorage.getItem(IND_LOCK_KEY) === "1");
    const dragKey = ref(null);
    const dragOverKey = ref(null);
    const data = computed(() => state.stock.data);
    const meta = computed(() => (data.value && data.value.meta) || {});
    const series = computed(() => (data.value && data.value.series) || {});
    const ind = computed(() => (data.value && data.value.indicator) || null);
    const orderedVotes = computed(() => {
      const votes = (ind.value && ind.value.votes) || [];
      const order = indOrder.value;
      const byKey = new Map(votes.map((v) => [v.key, v]));
      const out = order.map((k) => byKey.get(k)).filter(Boolean);
      votes.forEach((v) => { if (!order.includes(v.key)) out.push(v); });
      return out;
    });
    const ctx = computed(() => (data.value && data.value.context) || null);
    const report = computed(() => ctx.value?.research || null);
    const boards = computed(() => ctx.value?.boards || []);
    const events = computed(() => ctx.value?.events || []);
    const valuation = computed(() => (data.value && data.value.valuation) || null);
    const chan = computed(() => (data.value && data.value.chan) || null);
    const analysis = computed(() => (data.value && data.value.analysis) || null);
    const strategy = computed(() => (data.value && data.value.strategy) || null);
    const intra = computed(() => (data.value && data.value.intraday) || null);
    const isIntraday = computed(() => state.stock.period === 105);
    const watched = computed(() => isWatched(state.stock.code));
    const researchWindows = computed(() => {
      const wins = report.value?.windows || {};
      return [
        { key: "1m", label: "近 1 个月", data: wins["1m"] || {} },
        { key: "3m", label: "近 3 个月", data: wins["3m"] || {} },
        { key: "6m", label: "近 6 个月", data: wins["6m"] || {} },
      ];
    });

    function toggleStar() {
      toggleWatch({ code: state.stock.code, name: meta.value.name || state.stock.name });
    }

    const chartOption = computed(() => {
      if (!data.value) return null;
      if (isIntraday.value) return intradayOption(data.value, "MACD", planLines.value, planMarks.value);
      return klineOption(data.value, indicator.value, {
        fib: data.value.fib,
        chan: chanOn.value ? chan.value : null,
      });
    });

    const tech = computed(() => {
      const s = series.value;
      if (!s || !s.ma5) return null;
      const dif = lastValue(s.dif);
      const dea = lastValue(s.dea);
      const hist = lastValue(s.hist);
      const k = lastValue(s.k);
      const d = lastValue(s.d);
      const j = lastValue(s.j);
      const rsi = lastValue(s.rsi);
      const ma5 = lastValue(s.ma5);
      const ma20 = lastValue(s.ma20);
      return {
        dif, dea, hist,
        k, d, j, rsi, ma5, ma20,
        macdState: dif !== null && dea !== null ? (dif > dea ? "金叉运行" : "死叉运行") : "-",
        kdjState: k !== null ? (k > 80 ? "超买" : k < 20 ? "超卖" : k > d ? "多头排列" : "空头排列") : "-",
        maState: ma5 !== null && ma20 !== null ? (ma5 > ma20 ? "短期均线多头" : "短期均线空头") : "-",
      };
    });

    const infoRows = computed(() => {
      const m = meta.value;
      return [
        { k: "今开", v: fmtPrice(m.open) },
        { k: "最高", v: fmtPrice(m.high) },
        { k: "最低", v: fmtPrice(m.low) },
        { k: "昨收", v: fmtPrice(m.prev_close) },
        { k: "成交额", v: fmtAmount(m.amount) },
        { k: "换手率", v: m.turnover !== null && m.turnover !== undefined ? `${m.turnover}%` : "-" },
        { k: "量比", v: m.volume_ratio ? fmtNumber(m.volume_ratio, 2) : "-" },
        { k: "振幅", v: m.amplitude !== null && m.amplitude !== undefined ? `${m.amplitude}%` : "-" },
        { k: "市盈率(动)", v: m.pe ? fmtNumber(m.pe, 2) : "-" },
        { k: "市净率", v: m.pb ? fmtNumber(m.pb, 2) : "-" },
        { k: "总市值", v: fmtAmount(m.total_cap) },
        { k: "流通市值", v: fmtAmount(m.float_cap) },
      ];
    });

    const fibRows = computed(() => {
      if (!data.value || !data.value.fib) return [];
      const price = meta.value.price || data.value.bars?.[data.value.bars.length - 1]?.close || 0;
      return data.value.fib.map((level) => ({
        ...level,
        active: price ? Math.abs(level.price - price) / price < 0.012 : false,
      }));
    });

    const chanPoints = computed(() => ((chan.value && chan.value.points) || []).slice().reverse());
    const chanLevels = computed(() => (chan.value && chan.value.levels) || {});
    const tPoints = computed(() => ((intra.value && intra.value.t_points) || []).slice().reverse());

    const financeRows = computed(() => {
      const rows = (valuation.value && valuation.value.finance) || [];
      return rows.map((r) => ({
        ...r,
        revenue_text: fmtAmount(r.revenue),
        profit_text: fmtAmount(r.net_profit),
        gm: r.gross_margin !== null && r.gross_margin !== undefined ? `${r.gross_margin.toFixed(2)}%` : "-",
        roe_text: r.roe !== null && r.roe !== undefined ? `${r.roe.toFixed(2)}%` : "-",
      }));
    });
    const industryReports = computed(() => (valuation.value && valuation.value.industry_reports) || []);
    const valuationNotes = computed(() => (valuation.value && valuation.value.notes) || []);
    const valDims = computed(() => (valuation.value && valuation.value.dimensions) || []);
    const tStrategies = computed(() => Object.values((intra.value && intra.value.stats) || {}));
    const chanFuture = computed(() => (chan.value && chan.value.future) || null);
    const tEnv = computed(() => (intra.value && intra.value.env) || null);
    const advice = computed(() => (data.value && data.value.open_advice) || null);

    // 深度加权分析：手动触发、结果只留在本组件，不随分时 30 秒刷新重算
    const adv = ref(null);
    const advLoading = ref(false);
    const advError = ref("");
    async function loadAdv(force = false) {
      if (advLoading.value) return;
      const code = meta.value.code || state.stock.code;
      if (!code) return;
      advLoading.value = true;
      advError.value = "";
      try {
        adv.value = await api.tAdvanced(code, force);
      } catch (err) {
        advError.value = err && err.message ? err.message : String(err);
      } finally {
        advLoading.value = false;
      }
    }
    watch(() => meta.value.code, () => {
      adv.value = null;
      advError.value = "";
    });
    const advEnergy = computed(() => {
      const energy = adv.value && adv.value.energy;
      if (!energy) return [];
      const rows = [
        { key: "stock", label: "个股量能", item: energy.stock },
        { key: "sector", label: "板块量能", item: energy.sector },
        { key: "index", label: "大盘量能", item: energy.index },
      ];
      return rows.map((item) => ({
        key: item.key,
        label: item.label,
        name: (item.item && item.item.name) || "",
        tone: !item.item ? "muted" : item.item.label === "expanding" ? "up"
          : item.item.label === "shrinking" ? "down" : "",
        text: !item.item ? "无数据" : `${item.item.label_cn || "常规"} ×${item.item.ratio}`,
      }));
    });
    function advTone(grade) {
      return grade === "强" ? "up" : grade === "弱" ? "down" : "";
    }
    // 预估 T 出：画在分时图上的虚线，不在下方罗列卖出建议
    const planLines = computed(() => {
      const plan = adv.value && adv.value.plan;
      return plan && plan.t_out_lines && plan.t_out_lines.length ? plan.t_out_lines : null;
    });
    // 图上只标加权出来的买点；adv 已加载但当天没有买点时，连原始信号一起隐藏
    const planMarks = computed(() => {
      const plan = adv.value && adv.value.plan;
      if (!plan || !plan.buys) return null;
      return plan.buys.map((p, i) => ({ ...p, type: "buy", mark: `T买${i + 1}` }));
    });

    const bt = ref(null);
    const btLoading = ref(false);
    const btError = ref("");
    async function loadBt(force = false) {
      if (btLoading.value) return;
      btLoading.value = true;
      btError.value = "";
      try {
        bt.value = await api.tBacktest(10, force);
      } catch (err) {
        btError.value = err && err.message ? err.message : String(err);
      } finally {
        btLoading.value = false;
      }
    }

    const ob = ref(null);
    const obLoading = ref(false);
    const obError = ref("");
    async function loadOb(force = false) {
      if (obLoading.value) return;
      obLoading.value = true;
      obError.value = "";
      try {
        ob.value = await api.openBacktest(20, force);
      } catch (err) {
        obError.value = err && err.message ? err.message : String(err);
      } finally {
        obLoading.value = false;
      }
    }

    // 弹窗停留在分时页时每 30 秒静默刷新行情；指标分析结论按 09:25 / 15:00 / 24:00 换挡更新
    let timer = null;
    onMounted(() => {
      loadOb(false);
      timer = window.setInterval(() => {
        if (!state.stock.open || state.stock.period !== 105) return;
        if (!isMarketOpen()) return;
        fetchStock(true);
      }, 30000);
    });
    onUnmounted(() => {
      if (timer) window.clearInterval(timer);
    });

    // 点开分时就加载加权计划与股性画像（不做懒加载，避免盘中还要手动点一次）
    watch(isIntraday, (on) => {
      if (on && !bt.value && !btLoading.value) loadBt(false);
      if (on && !adv.value && !advLoading.value) loadAdv(false);
    }, { immediate: true });

    function onMask(event) {
      if (event.target.classList.contains("modal-mask")) closeStock();
    }

    function closeDetails() {
      if (detailsEl.value) detailsEl.value.open = false;
    }

    function saveIndOrder() {
      localStorage.setItem(IND_ORDER_KEY, JSON.stringify(indOrder.value));
    }

    function onIndDragStart(item, event) {
      if (indLocked.value) {
        event.preventDefault();
        return;
      }
      dragKey.value = item.key;
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", item.key);
      }
    }

    function onIndDrop(target) {
      const from = dragKey.value;
      dragKey.value = null;
      dragOverKey.value = null;
      if (indLocked.value || !from || from === target.key) return;
      const keys = orderedVotes.value.map((v) => v.key);
      const i = keys.indexOf(from);
      const j = keys.indexOf(target.key);
      if (i < 0 || j < 0) return;
      keys.splice(j, 0, keys.splice(i, 1)[0]);
      indOrder.value = keys;
      saveIndOrder();
    }

    function onIndDragEnd() {
      dragKey.value = null;
      dragOverKey.value = null;
    }

    function toggleIndLock() {
      indLocked.value = !indLocked.value;
      localStorage.setItem(IND_LOCK_KEY, indLocked.value ? "1" : "0");
    }

    function resetIndOrder() {
      indOrder.value = [];
      localStorage.removeItem(IND_ORDER_KEY);
      dragKey.value = null;
      dragOverKey.value = null;
    }

    return {
      state, data, meta, tech, chartOption, infoRows, fibRows, indicator, ind, ctx, report, boards, events, analysis, strategy,
      watched, researchWindows, toggleStar, valuation, chan, chanOn, chanPoints, chanLevels, intra, isIntraday,
      detailsEl, closeDetails,
      orderedVotes, indOrder, indLocked, dragKey, dragOverKey,
      onIndDragStart, onIndDrop, onIndDragEnd, toggleIndLock, resetIndOrder,
      tPoints, financeRows, industryReports, valuationNotes, valDims, tStrategies, chanFuture,
      tEnv, bt, btLoading, btError, loadBt, advice, ob, obLoading, obError, loadOb,
      adv, advLoading, advError, loadAdv, advEnergy, advTone, planLines, planMarks,
      closeStock, switchPeriod, onMask, openStock, fmtAmount, fmtNumber, fmtPct, fmtPrice, tone,
    };
  },
  template: `
  <div v-if="state.stock.open" class="modal-mask" @click="onMask">
    <div class="modal">
      <div class="modal-head">
        <div v-if="meta.name" style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">
          <span class="modal-title">{{ meta.name }}</span>
          <span class="tag num">{{ meta.code }}</span>
          <span class="tag" v-if="meta.industry">{{ meta.industry }}</span>
          <span class="modal-price num" :class="tone(meta.change_pct)">{{ fmtPrice(meta.price) }}</span>
          <span class="num" :class="tone(meta.change_pct)">{{ fmtPct(meta.change_pct) }}</span>
        </div>
        <div style="display:flex;gap:8px">
          <button class="ghost-btn" :class="{active: watched}" @click="toggleStar">{{ watched ? "已自选" : "加自选" }}</button>
          <button class="ghost-btn" @click="closeStock">关闭</button>
        </div>
      </div>

      <div class="modal-body">
        <div v-if="state.stock.error" class="error-bar">{{ state.stock.error }}</div>
        <div v-if="state.stock.loading" class="skeleton" style="height:320px"></div>

        <template v-else-if="data">
          <div class="filter-bar">
            <span class="filter-label">周期</span>
            <button v-for="p in [{k:105,l:'分时'},{k:101,l:'日'},{k:102,l:'周'},{k:103,l:'月'}]" :key="p.k"
                    class="chip" :class="{active: state.stock.period === p.k}" @click="switchPeriod(p.k)">{{ p.l }}</button>
            <span class="filter-label" style="margin-left:12px">指标</span>
            <button v-for="m in ['MACD','KDJ','RSI']" :key="m" class="chip"
                    :class="{active: indicator === m}" :disabled="isIntraday && m !== 'MACD'"
                    @click="indicator = m">{{ m }}</button>
            <button v-if="!isIntraday" class="chip" :class="{active: chanOn}" @click="chanOn = !chanOn">缠论K线</button>
          </div>

          <div class="oa-card" v-if="advice">
            <div class="oa-head">
              <span class="tag hot">开盘买入决策</span>
              <span class="muted small">{{ advice.phase }} · {{ advice.updated_at }}</span>
              <span class="oa-action" :class="advice.level === 'high' ? 'up' : (advice.level === 'low' ? 'down' : '')">{{ advice.action }}</span>
              <span class="oa-pos">建议仓位 {{ advice.position }}</span>
            </div>
            <div class="oa-row">
              <div class="oa-item">
                <span class="oa-label">指数竞价</span>
                <span class="num" :class="tone(advice.index.gap_pct)">{{ advice.index.gap_pct === null ? '-' : fmtPct(advice.index.gap_pct) }}</span>
                <span class="oa-sub">{{ advice.index.label }}</span>
              </div>
              <div class="oa-item">
                <span class="oa-label">板块竞价</span>
                <span class="num" :class="tone(advice.sector.gap_pct)">{{ advice.sector.gap_pct === null ? '-' : fmtPct(advice.sector.gap_pct) }}</span>
                <span class="oa-sub">{{ advice.sector.name || '未匹配' }}<template v-if="advice.sector.etf"> · {{ advice.sector.etf }}</template></span>
              </div>
              <div class="oa-item">
                <span class="oa-label">个股竞价</span>
                <span class="num" :class="tone(advice.stock.gap_pct)">{{ advice.stock.gap_pct === null ? '-' : fmtPct(advice.stock.gap_pct) }}</span>
                <span class="oa-sub">相对板块 {{ advice.stock.relative }}</span>
              </div>
              <div class="oa-item">
                <span class="oa-label">综合分</span>
                <span class="num" :class="tone(advice.score)">{{ advice.score }}</span>
                <span class="oa-sub">{{ advice.label }}</span>
              </div>
              <div class="oa-item" v-if="advice.dk">
                <span class="oa-label">日K 背景</span>
                <span class="num" :class="tone(advice.dk.score)">{{ fmtNumber(advice.dk.score, 2) }}</span>
                <span class="oa-sub">{{ advice.dk.label }} · MA20 偏离 {{ advice.dk.dev20 }}%</span>
              </div>
              <div class="oa-item" v-if="advice.market">
                <span class="oa-label">大盘环境</span>
                <span class="num" :class="tone(advice.market.score)">{{ fmtNumber(advice.market.score, 2) }}</span>
                <span class="oa-sub">情绪 {{ advice.market.sent.score }} {{ advice.market.sent.label }} · {{ advice.market.forecast.pattern }}</span>
              </div>
              <div class="oa-item" v-if="advice.sector_dk">
                <span class="oa-label">板块日K</span>
                <span class="num" :class="tone(advice.sector_dk.score)">{{ fmtNumber(advice.sector_dk.score, 2) }}</span>
                <span class="oa-sub">{{ advice.sector_dk.label }} · MA20 偏离 {{ advice.sector_dk.dev20 }}%</span>
              </div>
              <div class="oa-item" v-if="advice.catalyst">
                <span class="oa-label">催化</span>
                <span class="num" :class="tone(advice.catalyst.score || 0)">{{ advice.catalyst.score === null ? '-' : fmtNumber(advice.catalyst.score, 2) }}</span>
                <span class="oa-sub">{{ advice.catalyst.news.label }} · {{ advice.catalyst.overseas.label }}</span>
              </div>
            </div>
            <div class="oa-reason">{{ advice.reason }}</div>
            <div class="oa-regime" v-if="advice.plan">
              <span class="tag">大盘剧本</span>
              <span>{{ advice.plan.regime }} · 仓位上限 {{ advice.plan.cap }} 成 · {{ advice.plan.why }}</span>
            </div>
            <div class="oa-regime" v-if="advice.catalyst && advice.catalyst.news.items && advice.catalyst.news.items.length">
              <span class="tag">消息</span>
              <span>{{ advice.catalyst.news.items[0].impact }} · {{ advice.catalyst.news.items[0].title }}</span>
            </div>
            <div class="oa-levels">
              <span><em>买点</em>{{ advice.entry }}</span>
              <span><em>止损</em>{{ advice.stop }}</span>
              <span><em>目标</em>{{ advice.target }}</span>
            </div>
            <div class="oa-plans">
              <div class="oa-plan" v-for="(p, i) in advice.plans" :key="i"><span class="oa-idx">{{ i + 1 }}</span>{{ p }}</div>
            </div>
            <div class="oa-bt" v-if="ob">
              <span class="oa-bt-label">开盘策略回测 · 近 {{ ob.days }} 日</span>
              <span class="tag">建议买入 {{ ob.filtered.win_rate }}%（{{ ob.filtered.samples }} 次 · 均 {{ ob.filtered.avg_return }}%）</span>
              <span class="tag" v-if="ob.support_only">仅竞价低吸 {{ ob.support_only.win_rate }}%（{{ ob.support_only.samples }} 次）</span>
              <span class="tag">全部开盘买 {{ ob.baseline.win_rate }}%（{{ ob.baseline.samples }} 次）</span>
              <span class="tag">观望/回避 {{ ob.avoid.win_rate }}%（{{ ob.avoid.samples }} 次）</span>
              <span class="muted small" v-if="ob.excluded_limit_days">已剔除 {{ ob.excluded_limit_days }} 个连板日</span>
              <button class="ghost-btn" :disabled="obLoading" @click="loadOb(true)">{{ obLoading ? '回测中…' : '重算' }}</button>
            </div>
            <div class="muted small" v-else-if="obLoading">正在回测开盘策略（近 20 个交易日）…</div>
            <div class="error-bar" v-if="obError">{{ obError }}</div>
            <div class="muted small">{{ advice.note }}</div>
          </div>

          <div class="card chart-card">
            <div class="card-head">
              <div class="card-title">{{ isIntraday ? "分时走势 · 成交量 · MACD · 做T点位" : "K 线 · 均线 · 指标" }}</div>
              <div class="card-sub num" v-if="meta.price">
                {{ fmtPrice(meta.price) }} <span :class="tone(meta.change_pct)">{{ fmtPct(meta.change_pct) }}</span>
                <span class="muted" v-if="isIntraday"> · 30s 自动刷新</span>
              </div>
            </div>
            <Chart :option="chartOption" height="430" />
          </div>

          <div class="t-strip" v-if="isIntraday && intra">
            <div class="t-head">
              <span class="tag hot">做T点位</span>
              <span class="muted small">均价线回归 / 零轴支撑压力 / MACD 背离 / 量价背离，同向共振信号优先</span>
            </div>
            <div class="t-sum">{{ intra.summary }}</div>

            <div class="t-env" v-if="tEnv">
              <div class="t-env-item">
                <span class="t-env-label">指数环境</span>
                <span class="num" :class="tone(tEnv.index.change_pct)">{{ fmtPct(tEnv.index.change_pct) }}</span>
                <span class="t-env-badge">{{ tEnv.index.label }}</span>
              </div>
              <div class="t-env-item">
                <span class="t-env-label">板块环境</span>
                <span v-if="tEnv.sector.matched" class="num" :class="tone(tEnv.sector.change_pct)">{{ fmtPct(tEnv.sector.change_pct) }}</span>
                <span v-else class="num muted">无代理</span>
                <span class="t-env-badge">{{ tEnv.sector.matched ? tEnv.sector.name + ' · ' + tEnv.sector.etf : tEnv.sector.label }}</span>
              </div>
              <div class="t-env-item">
                <span class="t-env-label">个股相对</span>
                <span class="num" :class="tone(tEnv.relative)">{{ fmtNumber(tEnv.relative, 2) }}</span>
                <span class="t-env-badge">{{ tEnv.relative >= 0.15 ? '强于板块' : (tEnv.relative <= -0.15 ? '弱于板块' : '与板块同步') }}</span>
              </div>
              <div class="t-env-item" :class="tEnv.score >= 0.35 ? 'up' : (tEnv.score <= -0.35 ? 'down' : '')">
                <span class="t-env-label">综合环境</span>
                <span class="num" :class="tone(tEnv.score)">{{ fmtNumber(tEnv.score, 2) }}</span>
                <span class="t-env-badge">{{ tEnv.label }}</span>
              </div>
              <div class="t-env-item" v-if="tEnv.dk">
                <span class="t-env-label">日K 背景</span>
                <span class="num" :class="tone(tEnv.dk.score)">{{ fmtNumber(tEnv.dk.score, 2) }}</span>
                <span class="t-env-badge">{{ tEnv.dk.label }} · {{ tEnv.dk.align }}</span>
              </div>
              <div class="t-env-item" v-if="tEnv.mkt">
                <span class="t-env-label">大盘环境</span>
                <span class="num" :class="tone(tEnv.mkt.score)">{{ fmtNumber(tEnv.mkt.score, 2) }}</span>
                <span class="t-env-badge">情绪 {{ tEnv.mkt.sent.score }} {{ tEnv.mkt.sent.label }} · {{ tEnv.mkt.forecast.pattern }}</span>
              </div>
            </div>
            <div class="t-env-note" v-if="tEnv">
              {{ tEnv.note }}
              <span v-if="tEnv.aligned_up && tEnv.score < 0.5"> · 做T买点强度上调 {{ tEnv.buy_boost + 6 }} 分</span>
              <span v-else-if="tEnv.score >= 0.5"> · 指数/板块偏热，买点降级避免追高</span>
              <span v-else-if="tEnv.score <= -0.5"> · 共振走弱，买点降级、目标收窄至 0.3%</span>
              <span v-if="tEnv.dk"> · 日K {{ tEnv.dk.label }}，空头排列时买点降 10 分</span>
            </div>

            <div class="t-limit" v-if="intra.limit && intra.limit.limit_up_today">
              <span class="tag" :class="intra.limit.distorted ? 'cold' : 'hot'">{{ intra.limit.label }}</span>
              <span class="muted small">{{ intra.limit.distorted ? '连板/封板导致分时指标失真，已暂停做T点位' : '首板涨停当日封板后波动有限，做T点位仅供参考' }}</span>
            </div>

            <details class="t-raw" v-if="tPoints.length">
              <summary>原始信号 {{ tPoints.length }} 个，默认只看上面的加权买点</summary>
              <div class="t-list">
                <span v-for="p in tPoints.slice(0, 20)" :key="p.time + p.type + p.strategy" class="tag" :class="p.type === 'buy' ? 'hot' : 'cold'">
                  {{ p.time }} {{ p.type === 'buy' ? 'T买' : 'T卖' }} {{ p.price }}
                  <em class="t-tag">{{ p.strategy }}</em>
                  <template v-if="p.grade"><em class="t-strength" :class="p.grade === '强' ? 'up' : (p.grade === '弱' ? 'down' : '')">{{ p.grade }}{{ p.strength }}</em></template>
                  <template v-if="p.deviation !== null && p.deviation !== undefined">（{{ fmtPct(p.deviation) }}）</template>
                </span>
              </div>
            </details>
            <div class="t-stats">
              <span class="tag" v-for="s in tStrategies" :key="s.name" :title="s.note">
                {{ s.name }} · 目标 {{ s.target }}% / 止损 {{ s.stop }}%
              </span>
            </div>

            <div class="error-bar" v-if="advError">{{ advError }}</div>

            <div class="t-adv" v-if="adv">
              <div class="t-adv-head">
                <span class="tag hot">加权做T计划</span>
                <span class="muted small">{{ adv.summary }}</span>
                <button class="ghost-btn" :disabled="advLoading" @click="loadAdv(true)">
                  {{ advLoading ? '重算中…' : '重算' }}
                </button>
                <span class="muted small" v-if="advLoading && !adv">正在加载股性与加权…</span>
              </div>

              <div class="t-adv-meta">
                <span class="tag">{{ adv.tier }}股</span>
                <span class="tag" v-if="adv.plan.open">开盘 {{ adv.plan.open }}</span>
                <span class="tag" v-if="adv.plan.open_gap !== null && adv.plan.open_gap !== undefined"
                      :class="adv.plan.open_gap >= 0 ? 'up' : 'down'">
                  现价较开盘 {{ adv.plan.open_gap > 0 ? '+' : '' }}{{ adv.plan.open_gap }}%
                </span>
                <span class="tag">指数 {{ adv.weights.env }}% / 量能 {{ adv.weights.vol }}% / 股性 {{ adv.weights.prior }}%</span>
                <span class="muted small">{{ adv.updated_at }}</span>
              </div>

              <div class="t-energy">
                <div class="t-energy-cell" v-for="e in advEnergy" :key="e.key">
                  <span class="t-env-label">{{ e.label }}</span>
                  <span class="num" :class="e.tone">{{ e.text }}</span>
                  <span class="t-env-badge" v-if="e.name">{{ e.name }}</span>
                </div>
              </div>

              <div class="t-adv-list">
                <div class="t-adv-row" v-for="(p, i) in adv.plan.buys" :key="p.time + p.strategy_key">
                  <span class="t-adv-idx num">{{ i + 1 }}</span>
                  <span class="t-adv-time num">{{ p.time }}</span>
                  <span class="tag hot">T买</span>
                  <span class="num">{{ p.price }}</span>
                  <span class="t-strength" :class="advTone(p.grade)">{{ p.grade }}{{ p.strength }}</span>
                  <span class="t-adv-tag" :class="p.energy === 'expanding' ? 'up' : (p.energy === 'shrinking' ? 'down' : '')">
                    {{ p.energy_label }} ×{{ p.energy_ratio }}
                  </span>
                  <span class="t-adv-expect">
                    {{ p.expect }}
                    <template v-if="p.gap_from_last">，较上一个买点再跌 {{ p.gap_from_last }}%</template>
                  </span>
                </div>
                <div class="muted small" v-if="!adv.plan.buys.length">
                  今天还没有达标的加权买点，等下一次共振再说
                </div>
              </div>

              <div class="t-plan-notes">
                <div v-if="adv.plan.next_buy">
                  下一次买点：跌到 <b class="num">{{ adv.plan.next_buy.price }}</b>
                  （较上一个买点 -{{ adv.plan.next_buy.gap }}%）才会再提示
                </div>
                <div v-if="adv.plan.t_out_lines && adv.plan.t_out_lines.length">
                  图上虚线为预估 T 出：
                  <span class="tag" v-for="(l, i) in adv.plan.t_out_lines" :key="i">
                    {{ l.label }} {{ l.price }}
                  </span>
                </div>
                <div class="muted small">{{ adv.plan.rule }}</div>
              </div>

              <div class="t-persona" v-if="adv.persona && adv.persona.ok">
                <div class="t-persona-head">
                  <span class="tag gold">股性画像</span>
                  <span class="tag">{{ adv.persona.style }}</span>
                  <span class="muted small">近 {{ adv.persona.sample_days }} 个交易日</span>
                </div>
                <div class="persona-bins">
                  <div class="persona-bin" v-for="(v, i) in adv.persona.high_bin_prob" :key="i"
                       :title="adv.persona.bins[i] + ' 出现日内高点 ' + v + '%'">
                    <span class="persona-val num">{{ v }}</span>
                    <div class="persona-track"><i :style="{ height: Math.max(2, v) + '%' }"></i></div>
                    <span class="persona-tick">{{ adv.persona.bins[i].slice(0, 5) }}</span>
                  </div>
                </div>
                <div class="t-persona-grid">
                  <div class="t-persona-cell"><span>早盘冲高概率</span><b class="num">{{ adv.persona.morning_high_prob }}%</b></div>
                  <div class="t-persona-cell"><span>冲高回落概率</span><b class="num">{{ adv.persona.spike_fade_prob }}%</b></div>
                  <div class="t-persona-cell"><span>午后走弱概率</span><b class="num">{{ adv.persona.afternoon_weak_prob }}%</b></div>
                  <div class="t-persona-cell"><span>平均振幅</span><b class="num">{{ adv.persona.avg_amplitude }}%</b></div>
                </div>
                <div class="muted small">{{ adv.persona.note }}</div>
              </div>

              <div class="t-adv-foot muted small" v-if="adv.experience && adv.experience.ok">
                经验库 {{ adv.experience.start }} ~ {{ adv.experience.end }}（{{ adv.experience.days }} 个交易日 /
                {{ adv.experience.samples }} 条样本，第 {{ adv.experience.cycles }} 个周期）· 策略适应度：
                <span v-for="(w, k) in adv.experience.strategy_weights" :key="k" class="tag">{{ k }} {{ w }}</span>
              </div>
              <div class="muted small">{{ adv.note }}</div>
            </div>

            <div class="t-bt">
              <div class="t-bt-head">
                <span class="tag hot">环境过滤回测</span>
                <span class="muted small" v-if="bt">近 {{ bt.days }} 个交易日 · 5 分钟线 · 已剔除 {{ bt.excluded_limit_days }} 个连板/涨停日 · {{ bt.updated_at }}</span>
                <span class="muted small" v-else-if="btLoading">正在回测近 10 个交易日的 5 分钟线…</span>
                <button class="ghost-btn" v-if="bt" :disabled="btLoading" @click="loadBt(true)">{{ btLoading ? '重算中…' : '重算' }}</button>
              </div>
              <div class="error-bar" v-if="btError">{{ btError }}</div>
              <template v-if="bt">
                <div class="t-bt-body">
                  <div class="t-bt-cell">
                    <div class="vp-label">基准胜率（全部信号）</div>
                    <div class="t-bt-value num">{{ bt.baseline.win_rate }}%</div>
                    <div class="muted small">{{ bt.baseline.samples }} 次 · 平均 {{ bt.baseline.avg_return }}%</div>
                  </div>
                  <div class="t-bt-arrow">→</div>
                  <div class="t-bt-cell">
                    <div class="vp-label">叠加指数/板块环境后</div>
                    <div class="t-bt-value num up">{{ bt.env_filtered.win_rate }}%</div>
                    <div class="muted small">{{ bt.env_filtered.samples }} 次 · 平均 {{ bt.env_filtered.avg_return }}%</div>
                  </div>
                  <div class="t-bt-arrow">→</div>
                  <div class="t-bt-cell">
                    <div class="vp-label">强共振信号（板块向上+个股同步 或 强度≥70）</div>
                    <div class="t-bt-value num up">{{ bt.strong.win_rate }}%</div>
                    <div class="muted small">{{ bt.strong.samples }} 次 · 平均 {{ bt.strong.avg_return }}%</div>
                  </div>
                </div>
                <div class="t-bt-strategy" v-if="bt.buy_env">
                  <span class="tag">环境过滤后买点 {{ bt.buy_env.win_rate }}%（{{ bt.buy_env.samples }} 次）</span>
                  <span class="tag">环境过滤后卖点 {{ bt.sell_env.win_rate }}%（{{ bt.sell_env.samples }} 次）</span>
                </div>
                <div class="t-bt-strategy" v-if="bt.by_strategy && bt.by_strategy.length">
                  <span class="tag" v-for="s in bt.by_strategy" :key="s.name">
                    {{ s.name }}：基准 {{ s.win_rate }}%（{{ s.samples }}）→ 环境 {{ s.env_win_rate === null ? '-' : s.env_win_rate + '%' }}（{{ s.env_samples }}）
                  </span>
                </div>
                <div class="muted small">{{ bt.note }} 回测未计手续费与滑点，不构成收益承诺。</div>
              </template>
            </div>
          </div>

          <div class="card plan-card" v-if="strategy">
            <div class="card-head">
              <div>
                <div class="card-title">明日预期与应对策略</div>
                <div class="card-sub">
                  八指标 + 价值分析
                  <template v-if="analysis"> · {{ analysis.label }}快照 {{ analysis.computed_at }} 更新（{{ analysis.next_update }} 重算）</template>
                </div>
              </div>
              <span class="verdict-badge" :class="strategy.tomorrow.tone">{{ strategy.tomorrow.bias }}</span>
            </div>

            <div class="plan-top">
              <div>
                <div class="vp-label">明日预期区间</div>
                <div class="plan-range num">{{ fmtPrice(strategy.tomorrow.range.low) }} ~ {{ fmtPrice(strategy.tomorrow.range.high) }}</div>
                <div class="muted small">按近 20 日日均振幅 {{ strategy.tomorrow.amplitude }}% 估算 · 信心 {{ strategy.tomorrow.confidence }}</div>
              </div>
              <div class="verdict-points plan-levels">
                <div class="vp">
                  <div class="vp-label">明日参考支撑</div>
                  <div class="vp-value num down">{{ fmtPrice(strategy.tomorrow.support) }}</div>
                </div>
                <div class="vp">
                  <div class="vp-label">明日参考压力</div>
                  <div class="vp-value num up">{{ fmtPrice(strategy.tomorrow.resistance) }}</div>
                </div>
              </div>
            </div>

            <div class="advice" v-if="strategy.tomorrow.expectation">{{ strategy.tomorrow.expectation }}</div>

            <div class="plan-scenarios">
              <div class="ps-row" v-for="s in strategy.tomorrow.scenarios" :key="s.name">
                <span class="ps-name">{{ s.name }}</span>
                <span class="tag num">{{ s.prob }}%</span>
                <span class="ps-trigger">{{ s.trigger }}</span>
                <span class="ps-plan">{{ s.plan }}</span>
              </div>
            </div>

            <div class="plan-watch">
              <span class="eyebrow">明日盯盘</span>
              <span class="tag" v-for="w in strategy.tomorrow.watch" :key="w">{{ w }}</span>
            </div>

            <div class="plan-grid">
              <div class="plan-col">
                <div class="plan-head">
                  <span>短期 · {{ strategy.short_term.horizon }}</span>
                  <span class="tag gold">{{ strategy.short_term.stance }}</span>
                </div>
                <ul class="plan-list"><li v-for="a in strategy.short_term.actions" :key="a">{{ a }}</li></ul>
                <div class="muted small">{{ strategy.short_term.guard }}</div>
              </div>
              <div class="plan-col">
                <div class="plan-head">
                  <span>中期 · {{ strategy.mid_term.horizon }}</span>
                  <span class="tag">{{ strategy.mid_term.stance }}</span>
                </div>
                <ul class="plan-list"><li v-for="a in strategy.mid_term.actions" :key="a">{{ a }}</li></ul>
                <div class="muted small">{{ strategy.mid_term.guard }}</div>
              </div>
            </div>

            <div class="plan-basis muted small">
              指标依据：{{ strategy.basis.indicator }} · 价值依据：{{ strategy.basis.valuation }}
            </div>

            <details class="plan-details" v-if="strategy.details && strategy.details.sections" ref="detailsEl">
              <summary>展开指标依据与计算逻辑</summary>
              <div class="pd-sec" v-for="s in strategy.details.sections" :key="s.title">
                <div class="pd-title">{{ s.title }}</div>
                <div class="pd-note muted small" v-if="s.note">{{ s.note }}</div>
                <div class="pd-row" v-for="it in s.items" :key="it.label">
                  <span class="pd-label">{{ it.label }}</span>
                  <span class="pd-value num">{{ it.value }}</span>
                  <span class="pd-hint">{{ it.hint }}</span>
                </div>
                <div class="pd-formula" v-if="s.formula">算法：{{ s.formula }}</div>
              </div>
              <div class="pd-foot">
                <button type="button" class="chip" @click="closeDetails">收起</button>
              </div>
            </details>
          </div>

          <div class="analyze-grid">
            <!-- 板块联动 -->
            <div class="col-card">
              <div class="col-head">
                <span>板块联动</span>
                <span class="tag" v-if="boards.length">{{ boards.length }} 个关联板块</span>
              </div>
              <div class="col-sub" v-if="ctx && ctx.analysis">{{ ctx.analysis.summary }}</div>
              <div class="board-list">
                <div class="board-row" v-for="b in boards" :key="b.name">
                  <div class="board-line">
                    <span class="board-name">{{ b.name }}</span>
                    <span class="tag" v-if="b.kind === 'chain'">产业链</span>
                    <span class="num" :class="tone(b.change_pct)">今日 {{ fmtPct(b.change_pct) }}</span>
                    <span class="tag" :class="b.below_zero ? 'cold' : 'hot'">{{ b.below_zero ? '跌破0轴' : '站上0轴' }}</span>
                  </div>
                  <MiniCandle :bars="b.bars || []" :prev-close="b.prev_close" :height="46" />
                  <div class="board-meta">
                    <span class="tag">{{ b.state }}</span>
                    <span class="num small" :class="tone(b.change_5d)">5日 {{ fmtPct(b.change_5d) }}</span>
                    <span class="num small" :class="tone(b.change_20d)">20日 {{ fmtPct(b.change_20d) }}</span>
                    <span class="tag" v-if="b.relative !== null && b.relative !== undefined" :class="tone(b.relative)">
                      超额 {{ fmtPct(b.relative) }}
                    </span>
                  </div>
                  <div class="board-meta" v-if="b.today_low_pct !== null && b.today_low_pct !== undefined">
                    <span class="num small">日内 <span :class="tone(b.today_low_pct)">{{ fmtPct(b.today_low_pct) }}</span> ~ <span :class="tone(b.today_high_pct)">{{ fmtPct(b.today_high_pct) }}</span></span>
                    <span class="muted small" v-if="b.up !== null && b.up !== undefined">涨跌家数 {{ b.up }}/{{ b.down }}</span>
                  </div>
                  <div class="board-peers" v-if="b.peers && b.peers.length">
                    <span v-for="p in b.peers.slice(0, 4)" :key="p.code" class="tag">
                      {{ p.name }} <span :class="tone(p.change_pct)">{{ fmtPct(p.change_pct) }}</span>
                    </span>
                  </div>
                </div>
                <div v-if="!boards.length" class="muted small">暂无关联板块数据</div>
              </div>
              <div class="leader-box" v-if="ctx && ctx.leader">
                <div class="col-sub">板块龙头 · {{ ctx.leader.name }}（{{ ctx.leader.code }}）</div>
                <div class="muted small">
                  今日 <span :class="tone(ctx.leader.change_pct)">{{ fmtPct(ctx.leader.change_pct) }}</span>
                  · 近 5 日 <span :class="tone(ctx.leader.change_5d)">{{ fmtPct(ctx.leader.change_5d) }}</span>
                  <template v-if="ctx.relative && ctx.relative.to_leader !== null && ctx.relative.to_leader !== undefined">
                    · 个股相对龙头超额 <span :class="tone(ctx.relative.to_leader)">{{ fmtPct(ctx.relative.to_leader) }}</span>
                  </template>
                </div>
              </div>
            </div>

            <!-- 技术指标 + 缠论 -->
            <div class="col-card">
              <div class="col-head">
                <span>技术指标</span>
                <span class="tag" v-if="chan">{{ chan.trend }}</span>
              </div>
              <div class="tech-grid col-grid" v-if="tech">
                <div class="tech-cell">
                  <div class="tech-k">MACD</div>
                  <div class="tech-v" :class="tech.dif > tech.dea ? 'up' : 'down'">{{ tech.macdState }}</div>
                  <div class="tech-note num">DIF {{ fmtNumber(tech.dif, 2) }} / DEA {{ fmtNumber(tech.dea, 2) }}</div>
                </div>
                <div class="tech-cell">
                  <div class="tech-k">KDJ</div>
                  <div class="tech-v">{{ tech.kdjState }}</div>
                  <div class="tech-note num">K {{ fmtNumber(tech.k, 1) }} / D {{ fmtNumber(tech.d, 1) }} / J {{ fmtNumber(tech.j, 1) }}</div>
                </div>
                <div class="tech-cell">
                  <div class="tech-k">RSI</div>
                  <div class="tech-v" :class="tech.rsi > 70 ? 'down' : tech.rsi < 30 ? 'up' : 'flat'">
                    {{ tech.rsi > 70 ? '超买' : tech.rsi < 30 ? '超卖' : '中性' }}
                  </div>
                  <div class="tech-note num">{{ fmtNumber(tech.rsi, 1) }}</div>
                </div>
                <div class="tech-cell">
                  <div class="tech-k">均线</div>
                  <div class="tech-v" :class="tech.ma5 > tech.ma20 ? 'up' : 'down'">{{ tech.maState }}</div>
                  <div class="tech-note num">MA5 {{ fmtNumber(tech.ma5, 2) }} / MA20 {{ fmtNumber(tech.ma20, 2) }}</div>
                </div>
              </div>

              <div class="fib-list" v-if="fibRows.length">
                <div class="fib-row" v-for="f in fibRows" :key="f.ratio" :class="{active: f.active}">
                  <span>{{ f.label }}</span>
                  <span class="num">{{ fmtPrice(f.price) }}</span>
                </div>
              </div>

              <div class="chan-box" v-if="chan">
                <div class="chan-head">
                  <b>缠论分析</b>
                  <button class="ghost-btn small" :class="{active: chanOn}" @click="chanOn = !chanOn">
                    {{ chanOn ? '隐藏K线标注' : '生成缠论K线' }}
                  </button>
                </div>
                <div class="chan-sum">{{ chan.summary }}</div>
                <div class="chan-levels">
                  <span class="tag" v-if="chanLevels.support">支撑 {{ chanLevels.support }}</span>
                  <span class="tag" v-if="chanLevels.resistance">压力 {{ chanLevels.resistance }}</span>
                  <span class="tag" v-if="chanLevels.center_zg">中枢 {{ chanLevels.center_zd }}~{{ chanLevels.center_zg }}</span>
                </div>
                <div class="chan-future" v-if="chanFuture">
                  <div class="cf-head">
                    未来推演
                    <span class="tag" :class="chanFuture.bias === '偏多' ? 'hot' : chanFuture.bias === '偏空' ? 'cold' : ''">
                      {{ chanFuture.bias }}
                    </span>
                  </div>
                  <div class="cf-row buy" v-for="t in chanFuture.buy_triggers" :key="t.price + t.condition">
                    <span class="tag hot">买 {{ fmtPrice(t.price) }}</span>
                    <span class="cf-text">{{ t.condition }}</span>
                  </div>
                  <div class="cf-row sell" v-for="t in chanFuture.sell_triggers" :key="t.price + t.condition">
                    <span class="tag cold">卖 {{ fmtPrice(t.price) }}</span>
                    <span class="cf-text">{{ t.condition }}</span>
                  </div>
                </div>
                <div class="chan-points" v-if="chanPoints.length">
                  <div class="cp-row" v-for="p in chanPoints" :key="p.date + p.type">
                    <span class="tag" :class="p.side === 'buy' ? 'hot' : 'cold'">{{ p.type }}</span>
                    <span class="num muted">{{ p.date }}</span>
                    <span class="num">{{ fmtPrice(p.price) }}</span>
                    <span class="cp-reason">{{ p.reason }}</span>
                  </div>
                </div>
                <div class="muted small" v-else>暂无明确缠论买卖点</div>
                <div class="muted small">{{ chan.note }}</div>
              </div>
            </div>

            <!-- 价值分析 -->
            <div class="col-card">
              <div class="col-head">
                <span>价值分析</span>
                <span class="tag gold" v-if="valuation && valuation.main_business">主营 · {{ valuation.main_business }}</span>
                <span class="tag" v-else-if="valuation && valuation.industry">{{ valuation.industry }}</span>
              </div>
              <div class="val-score" v-if="valuation && valuation.score !== null">
                <div class="vs-num num">{{ valuation.score }}<span>分</span></div>
                <div class="vs-text">{{ valuation.verdict }}</div>
              </div>
              <div class="val-biz" v-if="valuation && ((valuation.business_products || []).length || (valuation.concept_tags || []).length)">
                <div class="vb-row" v-if="(valuation.business_products || []).length">
                  <span class="eyebrow">主营构成</span>
                  <span class="tag gold" v-for="p in valuation.business_products" :key="p.name">
                    {{ p.name }}<span class="vb-ratio num" v-if="p.ratio !== null"> {{ p.ratio }}%</span>
                  </span>
                </div>
                <div class="vb-row" v-if="(valuation.concept_tags || []).length">
                  <span class="eyebrow">关联概念</span>
                  <span class="tag" v-for="t in valuation.concept_tags" :key="t">{{ t }}</span>
                </div>
              </div>
              <div class="val-dims" v-if="valDims.length">
                <div class="vd" v-for="d in valDims" :key="d.key">
                  <div class="vd-head">
                    <span>{{ d.title }}</span>
                    <span class="num vd-score">{{ d.score === null ? '—' : d.score }}</span>
                  </div>
                  <div class="vd-row" v-for="it in d.items" :key="it.label">
                    <span class="vd-label">{{ it.label }}</span>
                    <span class="num vd-value">{{ it.value }}</span>
                    <span class="vd-judge">{{ it.judge }}</span>
                  </div>
                </div>
              </div>
              <div class="value-notes" v-if="valuationNotes.length">
                <div class="vn" v-for="n in valuationNotes" :key="n">{{ n }}</div>
              </div>
              <div class="value-table" v-if="financeRows.length">
                <div class="vt-head">
                  <span>报告期</span><span>营收</span><span>同比</span><span>净利</span><span>同比</span><span>毛利</span><span>ROE</span>
                </div>
                <div class="vt-row" v-for="r in financeRows" :key="r.date">
                  <span class="vt-date">{{ r.name }}</span>
                  <span class="num">{{ r.revenue_text }}</span>
                  <span class="num" :class="tone(r.revenue_yoy)">{{ fmtPct(r.revenue_yoy) }}</span>
                  <span class="num">{{ r.profit_text }}</span>
                  <span class="num" :class="tone(r.net_profit_yoy)">{{ fmtPct(r.net_profit_yoy) }}</span>
                  <span class="num">{{ r.gm }}</span>
                  <span class="num">{{ r.roe_text }}</span>
                </div>
              </div>
              <div class="muted small" v-else>财务数据暂不可用</div>

              <div class="sub-title">行业研究</div>
              <div class="report-list">
                <a class="report-row" v-for="r in industryReports" :key="r.title" :href="r.url || '#'" target="_blank" rel="noreferrer">
                  <span class="report-title">{{ r.title }}</span>
                  <span class="tag">{{ r.org }}</span>
                  <span class="num muted small">{{ r.date }}</span>
                </a>
                <div v-if="!industryReports.length" class="muted small">暂无匹配行业研报</div>
              </div>

              <div class="sub-title">个股研报</div>
              <div class="report-windows">
                <div class="rw" v-for="w in researchWindows" :key="w.key">
                  <span>{{ w.label }}</span>
                  <span class="num">{{ w.data.count || 0 }} 篇</span>
                  <span class="tag" v-if="w.data.rating">{{ w.data.rating }}</span>
                </div>
              </div>
            </div>
          </div>

          <div class="card ind-card" v-if="ind">
            <div class="card-head">
              <div class="card-title">八指标综合研判</div>
              <div class="card-sub" :class="tone(ind.score)">{{ ind.direction }} · {{ ind.score }} 分</div>
              <div class="right ind-tools">
                <span class="muted small" v-if="!indLocked">拖拽卡片可调整顺序</span>
                <button type="button" class="chip-btn" :class="{ gold: indLocked }" @click="toggleIndLock">
                  {{ indLocked ? '已锁定' : '锁定排序' }}
                </button>
                <button type="button" class="chip-btn" v-if="indOrder.length" @click="resetIndOrder">恢复默认</button>
              </div>
            </div>
            <div class="ind-grid">
              <div class="ind-cell" v-for="item in orderedVotes" :key="item.key"
                   :class="{ dragging: dragKey === item.key, 'drag-over': dragOverKey === item.key, locked: indLocked }"
                   :draggable="!indLocked"
                   @dragstart="onIndDragStart(item, $event)"
                   @dragover.prevent="dragOverKey = item.key"
                   @dragleave="dragOverKey = dragOverKey === item.key ? null : dragOverKey"
                   @drop.prevent="onIndDrop(item)"
                   @dragend="onIndDragEnd">
                <div class="ind-cell-head">
                  <div class="tech-k">{{ item.title }}</div>
                  <span class="ind-handle" v-if="!indLocked" title="拖拽排序">⋮⋮</span>
                  <span class="ind-lock-icon" v-else title="排序已锁定">锁</span>
                </div>
                <div class="tech-v" :class="item.vote > 0 ? 'up' : item.vote < 0 ? 'down' : 'flat'">{{ item.value }}</div>
                <div class="muted small">{{ item.desc }}</div>
              </div>
            </div>
            <div class="ind-tip muted small">{{ ind.tomorrow.expectation }}</div>
            <div class="muted small" v-if="analysis" style="margin-top:6px">
              {{ analysis.label }}快照 · {{ analysis.computed_at }} 更新 · 下一次 {{ analysis.next_update }} 重算
            </div>
          </div>

          <div class="card news-card">
            <div class="card-head">
              <div class="card-title">相关消息面</div>
              <div class="card-sub">仅保留与个股 / 板块相关的消息，标注信息源</div>
            </div>
            <div class="news-list" v-if="ctx && ctx.news && ctx.news.length">
              <div class="news-row" v-for="n in ctx.news" :key="n.title">
                <div class="news-main">
                  <a v-if="n.url" :href="n.url" target="_blank" rel="noreferrer">{{ n.title }}</a>
                  <span v-else>{{ n.title }}</span>
                </div>
                <div class="news-meta">
                  <span class="tag">{{ n.source }}</span>
                  <span class="num muted small">{{ n.time_text }}</span>
                  <span class="tag" v-if="n.impact">{{ n.impact }}</span>
                </div>
              </div>
            </div>
            <div v-else class="muted small">近 60 条快讯中没有与该股直接相关的消息</div>

            <div class="sub-title">未来事件（来自东方财富财经日历）</div>
            <div class="event-list" v-if="events.length">
              <div class="event-row" v-for="e in events" :key="e.date + e.name">
                <span class="tag num">{{ e.date }}</span>
                <span class="tag">{{ e.type }}</span>
                <div class="event-main">
                  <div class="event-name">{{ e.name }}</div>
                  <div class="muted small" v-if="e.content">{{ e.content }}</div>
                </div>
                <span class="num muted small">{{ e.city }}</span>
              </div>
            </div>
            <div v-else class="muted small">未来 3 周内暂无与该产业直接相关的会议 / 事件</div>

            <div class="sub-title" v-if="ctx && ctx.analysis && ctx.analysis.drivers">AI 解读</div>
            <div class="driver-list" v-if="ctx && ctx.analysis && ctx.analysis.drivers">
              <div class="driver-row" v-for="d in ctx.analysis.drivers" :key="d">{{ d }}</div>
            </div>

            <div class="concepts-bar" v-if="ctx && ctx.concepts && ctx.concepts.length">
              <span class="filter-label">概念</span>
              <span class="tag" v-for="c in ctx.concepts" :key="c">{{ c }}</span>
              <span class="muted small">点击概念可查看板块联动</span>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>`,
};

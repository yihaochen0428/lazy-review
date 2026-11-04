import { go, loadIndex, state } from "../store.js";
import { Chart } from "../components.js";
import { klineOption, intradayOption } from "../charts.js";
import { fmtPct, fmtPrice, tone } from "../utils.js";

export default {
  components: { Chart },
  setup() {
    const { computed, ref } = Vue;
    const indicator = ref("MACD");
    const data = computed(() => state.indexData);
    const loading = computed(() => state.loading);
    const indexList = computed(() => state.meta?.indices || [
      { code: "000001", name: "上证指数" },
      { code: "399001", name: "深证成指" },
      { code: "399006", name: "创业板指" },
      { code: "000688", name: "科创50" },
    ]);
    const periods = [
      { key: 105, label: "分时" },
      { key: 101, label: "日K" },
      { key: 102, label: "周K" },
      { key: 103, label: "月K" },
    ];
    const isIntraday = computed(() => state.indexPeriod === 105);
    const hasVwap = computed(() => (data.value?.intraday?.vwap || []).some((v) => v != null));
    const relatedNews = computed(() => data.value?.related_news || []);

    const chartOption = computed(() => {
      if (!data.value) return null;
      if (isIntraday.value) return intradayOption(data.value);
      return klineOption(data.value, indicator.value, { waves: data.value.waves, fib: data.value.fib });
    });

    function switchIndex(code) {
      if (state.indexCode === code) return;
      go("index", code);
    }

    function switchPeriod(period) {
      if (state.indexPeriod === period) return;
      loadIndex(state.indexCode, period);
    }

    function impactClass(impact) {
      return impact === "利好" ? "up" : impact === "利空" ? "down" : "muted";
    }

    const fibRows = computed(() => {
      if (!data.value?.fib || !data.value.price) return [];
      const price = data.value.price;
      return data.value.fib.map((level) => ({
        ...level,
        active: Math.abs(level.price - price) / price < 0.012,
      }));
    });

    return {
      state, go, data, loading, indicator, indexList, periods, isIntraday, hasVwap, relatedNews, chartOption, fibRows,
      switchIndex, switchPeriod, impactClass, fmtPct, fmtPrice, tone,
    };
  },
  template: `
  <div class="page">
    <div class="page-head">
      <div>
        <div class="page-title">指数研判</div>
        <div class="page-desc">MACD · KDJ · RSI · 波浪结构 · 斐波那契回撤，支持日 / 周 / 月周期切换</div>
      </div>
      <div class="index-switch">
        <button v-for="idx in indexList" :key="idx.code"
                :class="{ active: state.indexCode === idx.code }"
                @click="switchIndex(idx.code)">{{ idx.name }}</button>
      </div>
    </div>

    <div v-if="state.error" class="banner error">{{ state.error }}</div>
    <div v-if="loading && !data" class="skeleton" style="height:520px"></div>

    <template v-if="data">
      <div class="period-bar">
        <div class="tabs">
          <button class="tab" v-for="p in periods" :key="p.key"
                  :class="{ active: state.indexPeriod === p.key }"
                  @click="switchPeriod(p.key)">{{ p.label }}</button>
        </div>
        <span class="tag gold">{{ isIntraday ? '分时行情' : data.period_label + '研判' }}</span>
        <span class="muted small" v-if="!isIntraday">切换周期后，指标、波浪与支撑压力全部按{{ data.period_unit }}K重新计算</span>
        <span class="muted small" v-else>5 分钟线 · 均价线 · MACD，与个股分时同版式（水上红 / 水下绿）</span>
      </div>

      <section class="verdict-row" v-if="!isIntraday">
        <div class="card verdict-card">
          <div class="card-head">
            <div>
              <div class="card-title">{{ data.name }} · {{ data.period_label }}技术研判</div>
              <div class="card-sub">当前点位 {{ fmtPrice(data.price) }} · 综合评分 {{ data.score }}</div>
            </div>
            <div class="right">
              <button class="ghost-btn" @click="go('dashboard')">← 复盘看板</button>
            </div>
          </div>
          <div class="verdict-top">
            <span class="verdict-badge" :class="data.tone">{{ data.direction }}</span>
            <div class="verdict-meta">
              现价&nbsp;<b class="num" :class="tone(data.price - data.support)">{{ fmtPrice(data.price) }}</b><br />
              近 120 根{{ data.period_unit }}K区间 {{ fmtPrice(data.swing_low) }} ~ {{ fmtPrice(data.swing_high) }}
            </div>
          </div>
          <div class="verdict-points">
            <div class="vp">
              <div class="vp-label">第一支撑</div>
              <div class="vp-value num down">{{ fmtPrice(data.support) }}</div>
            </div>
            <div class="vp">
              <div class="vp-label">第一压力</div>
              <div class="vp-value num up">{{ fmtPrice(data.resistance) }}</div>
            </div>
            <div class="vp">
              <div class="vp-label">多空评分</div>
              <div class="vp-value num gold">{{ data.score }}</div>
            </div>
          </div>
          <div class="advice"><b style="color:#dfe5ef">操作思路：</b>{{ data.advice }}</div>
          <ul class="risk-list">
            <li v-for="risk in data.risks" :key="risk">{{ risk }}</li>
          </ul>
        </div>

        <div class="card">
          <div class="card-head">
            <div>
              <div class="card-title">斐波那契回撤</div>
              <div class="card-sub">{{ data.fib_rising ? '上升段回撤' : '下跌段反弹' }} · {{ data.period_label }}关键位</div>
            </div>
          </div>
          <div class="fib-list">
            <div class="row" v-for="row in fibRows" :key="row.ratio" :class="{ near: row.active }">
              <span>{{ row.label }}</span>
              <span class="num">{{ fmtPrice(row.price) }}</span>
            </div>
          </div>
          <div class="small muted" style="margin-top:12px;line-height:1.8">
            价格靠近回撤位时，该位置的多空争夺往往更激烈，可结合量能与 MACD 状态确认有效性。
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-head">
          <div>
            <div class="card-title">{{ data.period_label }}走势</div>
            <div class="card-sub" v-if="isIntraday">{{ hasVwap ? '分时线 + 均价线 + 成交量 + MACD' : '分时线 + 成交量 + MACD' }} · 金色虚线为昨收</div>
            <div class="card-sub" v-else>K线 + MA + 成交量 + 指标副图 · 金色虚线为斐波那契关键位</div>
          </div>
          <div class="right" v-if="!isIntraday">
            <div class="tabs">
              <button class="tab" :class="{ active: indicator === 'MACD' }" @click="indicator = 'MACD'">MACD</button>
              <button class="tab" :class="{ active: indicator === 'KDJ' }" @click="indicator = 'KDJ'">KDJ</button>
              <button class="tab" :class="{ active: indicator === 'RSI' }" @click="indicator = 'RSI'">RSI</button>
            </div>
          </div>
        </div>
        <Chart v-if="chartOption" :option="chartOption" tall />
      </section>

      <section class="analysis-grid" v-if="!isIntraday">
        <div class="card analysis-block" v-for="section in data.sections" :key="section.key">
          <h4>{{ section.title }}</h4>
          <ul>
            <li v-for="point in section.points" :key="point">{{ point }}</li>
          </ul>
        </div>
        <div class="card analysis-block">
          <h4>波段结构</h4>
          <ul>
            <li v-for="wave in data.waves.slice(-5)" :key="wave.date + wave.type">
              {{ wave.date }} · {{ wave.type === 'high' ? '高点' : '低点' }} {{ fmtPrice(wave.price) }}
            </li>
            <li v-if="!data.waves.length">近期波动幅度有限，未识别出清晰波段。</li>
          </ul>
        </div>
        <div class="card analysis-block news-linked">
          <h4>关联消息 · {{ data.period_label }}</h4>
          <div class="small muted" style="margin-bottom:8px">{{ data.news_period_hint }}</div>
          <ul class="linked-news" v-if="relatedNews.length">
            <li v-for="n in relatedNews" :key="n.title">
              <span class="news-time num">{{ n.time_text }}</span>
              <span class="impact" :class="impactClass(n.impact)">{{ n.impact }}</span>
              <a :href="n.url || undefined" target="_blank" rel="noopener">{{ n.title }}</a>
            </li>
          </ul>
          <div v-else class="muted small">暂无与当前指数直接关联的新闻，可到「新闻」页查看全部消息面。</div>
        </div>
      </section>
    </template>
  </div>`,
};

import { closeStock, isWatched, openStock, state, switchPeriod, toggleWatch } from "../store.js";
import { Chart, MiniCandle } from "../components.js";
import { intradayOption, klineOption } from "../charts.js";
import { fmtAmount, fmtNumber, fmtPct, fmtPrice, tone } from "../utils.js";

function lastValue(series) {
  if (!series) return null;
  for (let i = series.length - 1; i >= 0; i -= 1) {
    if (series[i] !== null && series[i] !== undefined) return series[i];
  }
  return null;
}

export default {
  components: { Chart, MiniCandle },
  setup() {
    const { computed, ref } = Vue;
    const indicator = ref("MACD");
    const chanOn = ref(false);
    const data = computed(() => state.stock.data);
    const meta = computed(() => (data.value && data.value.meta) || {});
    const series = computed(() => (data.value && data.value.series) || {});
    const ind = computed(() => (data.value && data.value.indicator) || null);
    const ctx = computed(() => (data.value && data.value.context) || null);
    const report = computed(() => ctx.value?.research || null);
    const boards = computed(() => ctx.value?.boards || []);
    const events = computed(() => ctx.value?.events || []);
    const valuation = computed(() => (data.value && data.value.valuation) || null);
    const chan = computed(() => (data.value && data.value.chan) || null);
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
      if (isIntraday.value) return intradayOption(data.value, "MACD");
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

    function onMask(event) {
      if (event.target.classList.contains("modal-mask")) closeStock();
    }

    return {
      state, data, meta, tech, chartOption, infoRows, fibRows, indicator, ind, ctx, report, boards, events,
      watched, researchWindows, toggleStar, valuation, chan, chanOn, chanPoints, chanLevels, intra, isIntraday,
      tPoints, financeRows, industryReports, valuationNotes, valDims, tStrategies, chanFuture,
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

          <div class="card chart-card">
            <div class="card-head">
              <div class="card-title">{{ isIntraday ? "分时走势 · 成交量 · MACD · 做T点位" : "K 线 · 均线 · 指标" }}</div>
              <div class="card-sub num" v-if="meta.price">
                {{ fmtPrice(meta.price) }} <span :class="tone(meta.change_pct)">{{ fmtPct(meta.change_pct) }}</span>
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
            <div class="t-list" v-if="tPoints.length">
              <span v-for="p in tPoints.slice(0, 8)" :key="p.time + p.type + p.strategy" class="tag" :class="p.type === 'buy' ? 'hot' : 'cold'">
                {{ p.time }} {{ p.type === 'buy' ? 'T买' : 'T卖' }} {{ p.price }}
                <em class="t-tag">{{ p.strategy }}</em>
                <template v-if="p.deviation !== null && p.deviation !== undefined">（{{ fmtPct(p.deviation) }}）</template>
              </span>
            </div>
            <div class="t-stats">
              <span class="tag" v-for="s in tStrategies" :key="s.name" :title="s.note">
                {{ s.name }} · 胜率 {{ s.win_rate }}%（{{ s.samples }} 次）
              </span>
            </div>
            <div class="muted small">回测为 10 只样本股近 5 个交易日的 5 分钟线统计，未计手续费，不构成收益承诺。</div>
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
                    <span class="num muted small">5日 {{ fmtPct(b.change_5d) }}</span>
                    <span class="num muted small">20日 {{ fmtPct(b.change_20d) }}</span>
                    <span class="tag" v-if="b.relative !== null && b.relative !== undefined" :class="tone(b.relative)">
                      超额 {{ fmtPct(b.relative) }}
                    </span>
                  </div>
                  <div class="board-meta" v-if="b.today_low_pct !== null && b.today_low_pct !== undefined">
                    <span class="num muted small">日内 {{ fmtPct(b.today_low_pct) }} ~ {{ fmtPct(b.today_high_pct) }}</span>
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
                  · 近 5 日 {{ fmtPct(ctx.leader.change_5d) }}
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
              <div class="card-title">六指标综合研判</div>
              <div class="card-sub" :class="tone(ind.score)">{{ ind.direction }} · {{ ind.score }} 分</div>
            </div>
            <div class="ind-grid">
              <div class="ind-cell" v-for="item in ind.votes" :key="item.key">
                <div class="tech-k">{{ item.title }}</div>
                <div class="tech-v" :class="item.vote > 0 ? 'up' : item.vote < 0 ? 'down' : 'flat'">{{ item.value }}</div>
                <div class="muted small">{{ item.desc }}</div>
              </div>
            </div>
            <div class="ind-tip muted small">{{ ind.tomorrow.expectation }}</div>
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

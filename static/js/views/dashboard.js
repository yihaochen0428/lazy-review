import { go, openSector, openStock, state } from "../store.js";
import { api } from "../api.js";
import { Chart, Sparkline, SentimentGauge } from "../components.js";
import {
  fmtAmount, fmtFlow, fmtPct, fmtPrice, fmtNumber, tone, sparkPath, intraPath,
} from "../utils.js";

export default {
  components: { Chart, Sparkline, SentimentGauge },
  setup() {
    const { computed, ref, watch } = Vue;
    const dash = computed(() => state.dash);
    const loading = computed(() => state.loading);
    const sectorType = ref("concept");
    const sectorSort = ref("f3");
    const sectorItems = ref([]);
    const sectorLoading = ref(false);
    const expanded = ref({});

    watch(
      dash,
      (value) => {
        if (value && value.sectors) {
          sectorItems.value = value.sectors[sectorType.value] || [];
        }
      },
      { immediate: true }
    );

    async function loadSectors() {
      sectorLoading.value = true;
      try {
        const res = await api.sectors(sectorType.value, sectorSort.value);
        sectorItems.value = res.items || [];
      } catch (err) {
        sectorItems.value = (dash.value?.sectors?.[sectorType.value]) || [];
      } finally {
        sectorLoading.value = false;
      }
    }

    function switchType(type) {
      if (sectorType.value === type) return;
      sectorType.value = type;
      sectorItems.value = (dash.value?.sectors?.[type]) || [];
      loadSectors();
    }

    function switchSort(event) {
      sectorSort.value = event.target.value;
      loadSectors();
    }

    const indices = computed(() => (dash.value?.indices || []).length ? dash.value.indices : []);
    const breadth = computed(() => dash.value?.breadth || null);
    const sentiment = computed(() => dash.value?.sentiment || null);
    const ai = computed(() => dash.value?.ai || null);
    const ladder = computed(() => dash.value?.ladder || null);
    const hotConcepts = computed(() => (dash.value?.limitup?.concepts || []).slice(0, 10));
    const outlook = computed(() => sentiment.value?.outlook || null);
    const sentimentOption = computed(() => {
      const history = sentiment.value?.history || [];
      if (!history.length) return null;
      const dates = history.map((p) => String(p.date).slice(5));
      const scores = history.map((p) => p.score);
      return {
        animation: false,
        backgroundColor: "transparent",
        grid: { left: 40, right: 12, top: 16, bottom: 26 },
        tooltip: {
          trigger: "axis",
          backgroundColor: "rgba(12,16,24,.96)",
          borderColor: "rgba(255,255,255,.10)",
          padding: [6, 9],
          textStyle: { fontSize: 11 },
          formatter: (params) => {
            const p = params[0];
            const point = history[p.dataIndex] || {};
            return `<div style="font-size:10.5px;color:#a3adc0;margin-bottom:4px">${point.date}</div>
              <div style="font-size:11.5px">情绪温度 <b style="color:#f2b845">${p.value}</b></div>
              <div style="font-size:11px;color:#8b96a8">指数涨跌 ${point.change_pct > 0 ? "+" : ""}${point.change_pct}%</div>
              <div style="font-size:11px;color:#8b96a8">量能比 ${point.volume_ratio}</div>`;
          },
        },
        xAxis: {
          type: "category", data: dates, boundaryGap: false,
          axisLine: { lineStyle: { color: "rgba(255,255,255,0.10)" } },
          axisTick: { show: false },
          axisLabel: { color: "#6c7789", fontSize: 9, interval: Math.max(Math.floor(dates.length / 6), 0) },
          splitLine: { show: false },
        },
        yAxis: {
          type: "value", min: 0, max: 100, splitNumber: 4,
          axisLabel: { color: "#6c7789", fontSize: 9 },
          splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
          axisLine: { show: false },
        },
        series: [{
          type: "line", name: "情绪温度", data: scores, smooth: true, showSymbol: false,
          lineStyle: { color: "#f2b845", width: 1.6 },
          areaStyle: {
            color: {
              type: "linear", x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: "rgba(242,184,69,.35)" },
                { offset: 1, color: "rgba(242,184,69,0)" },
              ],
            },
          },
          markLine: {
            silent: true, symbol: "none",
            lineStyle: { color: "rgba(255,255,255,.18)", type: "dashed", width: 1 },
            label: { color: "#6c7789", fontSize: 9, position: "insideEndTop" },
            data: [{ yAxis: 65, name: "偏热" }, { yAxis: 45, name: "中性" }, { yAxis: 30, name: "偏冷" }],
          },
          markPoint: {
            symbolSize: 6,
            itemStyle: { color: "#f2b845" },
            label: { show: false },
            data: [{ name: "今日", coord: [dates.length - 1, scores[scores.length - 1]] }],
          },
        }],
      };
    });
    const aiTab = ref("review");
    const aiTabs = [
      { key: "review", label: "今日复盘" },
      { key: "tomorrow", label: "明日预期" },
      { key: "news", label: "新闻解读" },
    ];

    function biasTone(bias) {
      if (!bias) return "neutral";
      if (bias.includes("偏多")) return "bull";
      if (bias.includes("偏空")) return "bear";
      return "neutral";
    }

    function impactClass(impact) {
      return impact === "利好" ? "up" : impact === "利空" ? "down" : "muted";
    }

    const stats = computed(() => {
      const b = breadth.value;
      if (!b) return [];
      return [
        { label: "上涨家数", value: b.up, cls: "up", sub: `${((b.up / Math.max(b.up + b.down, 1)) * 100).toFixed(1)}% 占比` },
        { label: "下跌家数", value: b.down, cls: "down", sub: `平盘 ${b.flat}` },
        { label: "涨停家数", value: b.limit_up, cls: "up", sub: `首板 ${b.limit_up - b.lianban} · 连板 ${b.lianban}` },
        { label: "跌停家数", value: b.limit_down, cls: "down", sub: "亏钱效应" },
        { label: "封板率", value: b.seal_rate, cls: "gold", suffix: "%", sub: `炸板 ${b.broken} 家` },
        { label: "炸板率", value: b.broken_rate, cls: "gold", suffix: "%", sub: "封板质量" },
        { label: "连板家数", value: b.lianban, cls: "gold", sub: `晋级率 ${b.promote_rate}%` },
        { label: "最高板", value: b.max_board, cls: "up", suffix: "板", sub: "空间高度" },
      ];
    });

    const ladderStats = computed(() => ladder.value?.stats || {});
    const ladderLevels = computed(() => ladder.value?.levels || []);
    const maxBoard = computed(() => ladderStats.value.max_board || 0);

    function upRatio(board) {
      const total = Math.max(board.up + board.down, 1);
      return `${Math.round((board.up / total) * 100)}%`;
    }

    function visibleStocks(level) {
      const limit = expanded.value[level.board] ? 24 : level.board === 1 ? 8 : 12;
      return level.stocks.slice(0, limit);
    }

    function toggleLevel(level) {
      expanded.value = { ...expanded.value, [level.board]: !expanded.value[level.board] };
    }

    function openIndex(item) {
      go("index", item.code);
    }

    function openHot(concept) {
      const board = (dash.value?.sectors?.concept || []).find((s) => s.name === concept.name);
      if (board) openSector(board.code, board.name);
      else go("short");
    }

    function signed(value, digits = 2) {
      if (value === null || value === undefined) return "-";
      const n = Number(value);
      return `${n >= 0 ? "+" : ""}${n.toFixed(digits)}`;
    }

    // 指数分时迷你线由 utils.intraPath 生成（0轴对称，水上红/水下绿）

    return {
      state, go, dash, loading, indices, breadth, sentiment, outlook, sentimentOption, ai, ladder,
      ladderStats, ladderLevels, maxBoard, stats, hotConcepts,
      sectorType, sectorSort, sectorItems, sectorLoading,
      switchType, switchSort, upRatio, openSector, openStock, visibleStocks, toggleLevel,
      expanded, openIndex, openHot, signed, intraPath,
      aiTab, aiTabs, biasTone, impactClass,
      fmtAmount, fmtFlow, fmtPct, fmtPrice, fmtNumber, tone, sparkPath,
    };
  },
  template: `
  <div class="page">
    <div class="page-head">
      <div>
        <div class="page-title">复盘看板</div>
        <div class="page-desc">
          <template v-if="dash">{{ dash.date }} · 数据更新于 {{ dash.updated_at }}</template>
          <template v-else>正在汇总今日盘面数据…</template>
        </div>
      </div>
      <div v-if="dash" style="display:flex;gap:8px;align-items:center">
        <span class="tag" :class="dash.is_latest ? 'gold' : ''">{{ dash.is_latest ? '最新交易日' : '历史快照' }}</span>
        <span class="tag" v-if="dash.cached">缓存快照</span>
        <span class="tag" v-if="dash.stale">数据源限流 · 展示上次结果</span>
      </div>
    </div>

    <div v-if="state.error" class="banner error">{{ state.error }}</div>

    <template v-if="loading && !dash">
      <div class="index-grid">
        <div v-for="n in 4" :key="n" class="skeleton" style="height:152px"></div>
      </div>
      <div class="overview-grid">
        <div class="skeleton" style="height:210px"></div>
        <div class="skeleton" style="height:210px"></div>
      </div>
      <div class="skeleton" style="height:260px"></div>
    </template>

    <template v-else-if="dash">
      <section class="index-grid">
        <div v-for="idx in indices" :key="idx.code" class="index-card"
             :class="tone(idx.change_pct) + '-accent'" @click="openIndex(idx)">
          <div class="idx-top">
            <span class="idx-name">{{ idx.name }}</span>
            <span class="idx-code num">{{ idx.code }}</span>
          </div>
          <div class="idx-price num" :class="tone(idx.change_pct)">{{ fmtPrice(idx.price) }}</div>
          <div class="idx-delta" :class="tone(idx.change_pct)">
            <span class="num">{{ signed(idx.change) }}</span>
            <span class="pct-pill num">{{ fmtPct(idx.change_pct) }}</span>
          </div>
          <div class="idx-meta">
            <span>成交 <span class="num">{{ fmtAmount(idx.amount) }}</span></span>
            <svg v-if="idx.intra && intraPath(idx.intra.pct)" class="spark" width="96" height="26"
                 viewBox="0 0 96 26" preserveAspectRatio="none">
              <line x1="2" x2="94" :y1="intraPath(idx.intra.pct).zero" :y2="intraPath(idx.intra.pct).zero"
                    stroke="rgba(242,184,69,.55)" stroke-dasharray="3 2" stroke-width="1" />
              <path :d="intraPath(idx.intra.pct).up" fill="none" stroke="#ff5063"
                    stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" />
              <path :d="intraPath(idx.intra.pct).down" fill="none" stroke="#14c39a"
                    stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            <Sparkline v-else :values="idx.spark || []" />
          </div>
        </div>
      </section>

      <div v-if="!breadth" class="banner">该交易日为历史快照，仅展示指数与涨停数据，全市场涨跌家数需当日运行后记录。</div>

      <section class="overview-grid" v-if="breadth">
        <div class="card sentiment-card">
          <div class="card-head">
            <div>
              <div class="card-title">市场情绪温度</div>
              <div class="card-sub">综合涨跌家数、涨跌停、连板强度的量化评分</div>
            </div>
            <div class="right" v-if="outlook">
              <span class="verdict-badge" :class="outlook.tone">{{ outlook.pattern }}</span>
            </div>
          </div>
          <div class="sentiment-wrap">
            <SentimentGauge :score="sentiment.score" :label="sentiment.label" />
            <div class="sentiment-side">
              <span class="sentiment-label" :class="sentiment.tone">当前状态：{{ sentiment.label }}</span>
              <div class="sentiment-desc">
                0-30 冰点 · 30-45 偏冷 · 45-65 中性 · 65-80 偏热 · 80+ 过热
              </div>
              <div class="comp-list">
                <div class="comp" v-for="c in sentiment.components" :key="c.name">
                  <span>{{ c.name }}</span>
                  <span class="comp-bar"><i :style="{ width: c.value + '%' }"></i></span>
                  <span class="val num">{{ c.value }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="card stat-card">
          <div class="card-head">
            <div>
              <div class="card-title">市场概览</div>
              <div class="card-sub">全市场 {{ fmtNumber(breadth.total) }} 只个股 · 涨跌停与封板质量</div>
            </div>
            <div class="right"><span class="eyebrow">BREADTH</span></div>
          </div>
          <div class="stat-grid">
            <div class="stat" v-for="s in stats" :key="s.label">
              <div class="stat-label">{{ s.label }}</div>
              <div class="stat-value num" :class="s.cls">{{ s.value }}<small v-if="s.suffix">{{ s.suffix }}</small></div>
              <div class="stat-sub">{{ s.sub }}</div>
            </div>
          </div>
        </div>
      </section>

      <section class="card sentiment-trend-card" v-if="outlook && sentimentOption">
        <div class="card-head">
          <div>
            <div class="card-title">情绪温度趋势 · 量价判断</div>
            <div class="card-sub">由上证指数近 60 个交易日量价还原，用于判断情绪周期位置</div>
          </div>
          <div class="right">
            <span class="tag">近5日 <b class="num" :class="tone(outlook.change_5d)">{{ outlook.change_5d }}%</b></span>
            <span class="tag">近10日 <b class="num" :class="tone(outlook.change_10d)">{{ outlook.change_10d }}%</b></span>
            <span class="tag">量能 <b class="num gold">{{ outlook.volume_ratio }}</b></span>
            <span class="tag">20日均线偏离 <b class="num">{{ outlook.dev20 }}%</b></span>
          </div>
        </div>
        <Chart :option="sentimentOption" height="200px" />
        <div class="outlook-grid">
          <div class="outlook-block">
            <div class="outlook-label">盘面结构</div>
            <div class="outlook-body">{{ outlook.detail }}</div>
          </div>
          <div class="outlook-block">
            <div class="outlook-label">明确预期</div>
            <div class="outlook-body">{{ outlook.expectation }}</div>
          </div>
          <div class="outlook-block">
            <div class="outlook-label">操作应对</div>
            <div class="outlook-body">{{ outlook.advice }}</div>
          </div>
          <div class="outlook-block">
            <div class="outlook-label">关键价位</div>
            <div class="outlook-body">
              支撑 <b class="num up">{{ fmtPrice(outlook.support) }}</b> ·
              压力 <b class="num down">{{ fmtPrice(outlook.resistance) }}</b><br />
              <span class="muted small">20日均线偏离 {{ outlook.dev20 }}%，跌破支撑视为结构转弱</span>
            </div>
          </div>
        </div>
      </section>

      <section class="ai-grid">
        <div class="card ai-card">
          <div class="card-head">
            <div>
              <div class="card-title">AI 每日复盘</div>
              <div class="card-sub">基于当日真实盘面数据自动生成</div>
            </div>
            <div class="right">
              <span class="ai-badge">
                {{ ai ? (ai.engine === 'llm' ? 'AI 大模型' : '规则引擎') : '暂无数据' }}
              </span>
            </div>
          </div>
          <template v-if="ai">
            <div class="ai-summary">{{ ai.summary }}</div>
            <div class="ai-tabs">
              <button v-for="t in aiTabs" :key="t.key" class="tab"
                      :class="{ active: aiTab === t.key }" @click="aiTab = t.key">{{ t.label }}</button>
            </div>

            <div v-if="aiTab === 'review'" class="ai-sections">
              <div class="ai-block" v-for="(section, i) in ai.sections" :key="section.title">
                <div class="ai-block-head">
                  <span class="ai-index num">{{ String(i + 1).padStart(2, '0') }}</span>
                  <span class="ai-block-title">{{ section.title }}</span>
                </div>
                <div class="ai-block-body">{{ section.body }}</div>
              </div>
            </div>

            <div v-else-if="aiTab === 'tomorrow'" class="tomorrow-wrap">
              <div class="tomorrow-head">
                <span class="bias-chip" :class="biasTone(ai.tomorrow.direction)">
                  明日 {{ ai.tomorrow.bias }}
                </span>
                <span class="tag">技术评分 <b class="num">{{ ai.tomorrow.score }}</b></span>
                <span class="tag" v-if="ai.tomorrow.support">
                  支撑 <b class="num up">{{ fmtPrice(ai.tomorrow.support) }}</b>
                  <small class="num muted" v-if="ai.tomorrow.support_pct">{{ ai.tomorrow.support_pct }}%</small>
                </span>
                <span class="tag" v-if="ai.tomorrow.resistance">
                  压力 <b class="num down">{{ fmtPrice(ai.tomorrow.resistance) }}</b>
                  <small class="num muted" v-if="ai.tomorrow.resistance_pct">+{{ ai.tomorrow.resistance_pct }}%</small>
                </span>
              </div>
              <div class="scenario-list">
                <div class="scenario" v-for="s in ai.tomorrow.scenarios" :key="s.name">
                  <div class="scenario-top">
                    <b>{{ s.name }}</b><span class="prob num">{{ s.prob }}%</span>
                  </div>
                  <div class="scenario-trigger">触发：{{ s.trigger }}</div>
                  <div class="scenario-plan">应对：{{ s.plan }}</div>
                </div>
              </div>
              <div class="watch-line">
                <span class="eyebrow">明日关注</span>
                <span v-for="w in ai.tomorrow.watch" :key="w" class="tag">{{ w }}</span>
              </div>
            </div>

            <div v-else class="news-view">
              <div class="news-view-sum">{{ ai.news_view.summary }}</div>
              <div class="nv-point" v-for="p in ai.news_view.points" :key="p.title">
                <div class="nv-head">
                  <span class="news-scope" :class="p.scope === '国际' ? 'intl' : ''">{{ p.scope }}</span>
                  <span class="nv-title">{{ p.title }}</span>
                  <span class="impact" :class="impactClass(p.impact)">{{ p.impact }}</span>
                </div>
                <div class="nv-comment">{{ p.comment }}</div>
              </div>
              <div class="nv-liq">{{ ai.news_view.liquidity }}</div>
              <div class="nv-industry" v-if="ai.news_view.industry && ai.news_view.industry.length">
                <span class="eyebrow">行业映射</span>
                <span class="tag gold" v-for="n in ai.news_view.industry" :key="n.name">{{ n.name }} · {{ n.count }}条</span>
              </div>
            </div>
          </template>
          <div v-else class="empty">
            <b>历史快照无 AI 复盘</b>
            全市场涨跌数据缺失，无法生成完整复盘文案
          </div>
        </div>

        <div class="card">
          <div class="card-head">
            <div>
              <div class="card-title">涨停概念热度</div>
              <div class="card-sub">按涨停个股所属概念聚合</div>
            </div>
            <div class="right"><span class="eyebrow">TOP 10</span></div>
          </div>
          <div class="hot-list" v-if="hotConcepts.length">
            <div class="hot-item" v-for="c in hotConcepts" :key="c.name" @click="openHot(c)" style="cursor:pointer">
              <span class="hot-name" :title="c.name">{{ c.name }}</span>
              <span class="hot-bar"><i :style="{ width: Math.max(8, (c.count / Math.max(hotConcepts[0].count, 1)) * 100) + '%' }"></i></span>
              <span class="hot-count num">{{ c.count }}家</span>
            </div>
          </div>
          <div v-else class="empty">暂无涨停概念数据</div>
        </div>
      </section>

      <section class="section-grid">
        <div class="card">
          <div class="card-head">
            <div>
              <div class="card-title">板块排行</div>
              <div class="card-sub">点击板块查看成分股与领涨龙头</div>
            </div>
            <div class="right">
              <div class="tabs">
                <button class="tab" :class="{ active: sectorType === 'concept' }" @click="switchType('concept')">概念</button>
                <button class="tab" :class="{ active: sectorType === 'industry' }" @click="switchType('industry')">行业</button>
              </div>
              <select class="select" :value="sectorSort" @change="switchSort">
                <option value="f3">按涨跌幅</option>
                <option value="f6">按成交额</option>
                <option value="f62">按主力净流入</option>
              </select>
            </div>
          </div>
          <div class="table-wrap">
            <table class="simple">
              <thead>
                <tr>
                  <th style="width:38px">#</th>
                  <th>板块</th>
                  <th>涨跌幅</th>
                  <th>领涨股</th>
                  <th>涨跌家数</th>
                  <th>成交额</th>
                  <th>主力净额</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(b, i) in sectorItems" :key="b.code" @click="openSector(b.code, b.name)" style="cursor:pointer">
                  <td><span class="rank" :class="{ top: i < 3 }">{{ i + 1 }}</span></td>
                  <td class="name-cell">
                    {{ b.name }}
                    <small>换手 {{ b.turnover }}%</small>
                  </td>
                  <td class="num" :class="tone(b.change_pct)"><b>{{ fmtPct(b.change_pct) }}</b></td>
                  <td>
                    {{ b.leader || '-' }}
                    <span class="num" :class="tone(b.leader_pct)" style="font-size:11px">{{ fmtPct(b.leader_pct) }}</span>
                  </td>
                  <td>
                    <div class="mini-count">
                      <span class="up-n num">{{ b.up }}</span><span style="color:#4a5265">/</span><span class="down-n num">{{ b.down }}</span>
                      <span class="bar"><i :style="{ width: upRatio(b) }"></i></span>
                    </div>
                  </td>
                  <td class="num">{{ fmtAmount(b.amount) }}</td>
                  <td class="num" :class="tone(b.main_flow)">{{ fmtFlow(b.main_flow) }}</td>
                </tr>
                <tr v-if="!sectorItems.length"><td colspan="7"><div class="empty">暂无板块数据</div></td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="card ladder-card">
          <div class="card-head">
            <div>
              <div class="card-title">连板梯队</div>
              <div class="card-sub">空间高度与晋级结构</div>
            </div>
            <div class="right">
              <button class="ghost-btn" @click="go('short')">短线复盘</button>
            </div>
          </div>
          <div class="ladder-sum">
            <div>最高板<b class="gold num">{{ ladderStats.max_board || '-' }}</b></div>
            <div>连板<b class="up num">{{ ladderStats.lianban || 0 }}</b></div>
            <div>晋级率<b class="gold num">{{ ladderStats.promote_rate || 0 }}%</b></div>
            <div>断板均值<b :class="tone(ladderStats.broken_avg)" class="num">{{ fmtPct(ladderStats.broken_avg) }}</b></div>
          </div>
          <div class="ladder">
            <div class="ladder-row" v-for="level in ladderLevels" :key="level.board" :class="{ max: level.board === maxBoard }">
              <div class="board-badge num">{{ level.label }}</div>
              <div class="chips">
                <div class="chip" v-for="s in visibleStocks(level)" :key="s.code" @click="openStock(s.code)">
                  <b>{{ s.name }}</b>
                  <small class="num">{{ s.seal_time }}</small>
                </div>
                <div class="chip" v-if="level.stocks.length > visibleStocks(level).length" @click="toggleLevel(level)">
                  +{{ level.stocks.length - visibleStocks(level).length }}
                </div>
                <div class="chip" v-else-if="expanded[level.board]" @click="toggleLevel(level)">收起</div>
              </div>
            </div>
            <div v-if="!ladderLevels.length" class="empty">暂无涨停梯队</div>
          </div>
        </div>
      </section>
    </template>
  </div>`,
};

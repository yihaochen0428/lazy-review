import { go, loadAbnormal, openSector, openStock, state } from "../store.js";
import { fmtAmount, fmtPct, fmtPrice, tone, boardText } from "../utils.js";

export default {
  setup() {
    const { computed, onMounted, ref } = Vue;
    const limitup = computed(() => state.dash?.limitup || null);
    const ladder = computed(() => state.dash?.ladder || null);
    const stats = computed(() => limitup.value?.stats || {});
    const stocks = computed(() => limitup.value?.stocks || []);
    const concepts = computed(() => limitup.value?.concepts || []);
    const broken = computed(() => limitup.value?.broken || []);
    const limitDown = computed(() => limitup.value?.limit_down || []);
    const brokenStocks = computed(() => limitup.value?.broken_stocks || []);

    const view = ref("stocks");
    const boardFilter = ref("all");
    const sortKey = ref("height");
    const keyword = ref("");
    const activeConcept = ref("");
    const showAllConcepts = ref(false);

    onMounted(() => loadAbnormal());

    const abnormal = computed(() => state.abnormal.data);
    const abnormalGroups = computed(() => {
      const groups = abnormal.value?.groups || {};
      return [
        { key: "up", label: "已触发严重异动 · 上涨", tone: "danger", desc: "10日偏离≥+100% 或 30日≥+200%，进入重点监控", items: groups.up || [] },
        { key: "near", label: "接近触发", tone: "warn", desc: "10日偏离已超 70%，再涨一段可能触发监管", items: groups.near || [] },
        { key: "exiting", label: "即将出异动 · 脱离监管", tone: "calm", desc: "上涨触发严重异动后，10 日重点监控期即将结束", items: groups.exiting || [] },
      ];
    });

    const boardOptions = computed(() => {
      const boards = [...new Set(stocks.value.map((s) => s.board))].sort((a, b) => b - a);
      return boards;
    });

    const filtered = computed(() => {
      let list = stocks.value.slice();
      if (boardFilter.value !== "all") {
        const target = Number(boardFilter.value);
        list = list.filter((s) => (target === 99 ? s.board >= 3 : s.board === target));
      }
      if (activeConcept.value) {
        list = list.filter((s) => (s.concepts || []).includes(activeConcept.value));
      }
      if (keyword.value.trim()) {
        const kw = keyword.value.trim();
        list = list.filter(
          (s) => s.name.includes(kw) || s.code.includes(kw) || (s.concepts || []).some((c) => c.includes(kw)) || (s.reason || "").includes(kw)
        );
      }
      const sorters = {
        height: (a, b) => b.board - a.board || a.seal_time.localeCompare(b.seal_time),
        seal: (a, b) => a.seal_time.localeCompare(b.seal_time),
        amount: (a, b) => (b.amount || 0) - (a.amount || 0),
        turnover: (a, b) => (b.turnover || 0) - (a.turnover || 0),
        change: (a, b) => b.change_pct - a.change_pct,
      };
      return list.sort(sorters[sortKey.value] || sorters.height);
    });

    const conceptChips = computed(() => concepts.value.slice(0, showAllConcepts.value ? 24 : 10));

    const kpis = computed(() => {
      const s = stats.value;
      if (!limitup.value) return [];
      return [
        { label: "涨停家数", value: s.limit_up || 0, foot: `20cm ${s.twenty_cm || 0} 家`, cls: "up" },
        { label: "连板家数", value: s.lianban || 0, foot: `首板 ${s.first_board || 0} 家`, cls: "gold" },
        { label: "最高板", value: s.max_board || 0, foot: "空间高度", cls: "up", gold: true },
        { label: "封板率", value: s.seal_rate || 0, suffix: "%", foot: `炸板 ${s.broken || 0} 家`, cls: "gold" },
        { label: "晋级率", value: s.promote_rate || 0, suffix: "%", foot: `昨日连板 ${s.prev_lianban || 0} 家`, cls: "gold" },
        { label: "跌停家数", value: s.limit_down || 0, foot: "注意补跌风险", cls: "down", down: true },
      ];
    });

    function pickConcept(name) {
      activeConcept.value = activeConcept.value === name ? "" : name;
    }

    function openConceptBoard(concept) {
      const board = (state.dash?.sectors?.concept || []).find((b) => b.name === concept.name);
      if (board) openSector(board.code, board.name);
    }

    return {
      state, go, limitup, ladder, stats, stocks, concepts, broken, limitDown, brokenStocks,
      view, boardFilter, sortKey, keyword, activeConcept, showAllConcepts, boardOptions,
      filtered, conceptChips, kpis, pickConcept, openConceptBoard, openSector, openStock,
      abnormal, abnormalGroups, loadAbnormal,
      fmtAmount, fmtPct, fmtPrice, tone, boardText,
    };
  },
  template: `
  <div class="page">
    <div class="page-head">
      <div>
        <div class="page-title">短线复盘</div>
        <div class="page-desc">涨停结构 · 连板梯队 · 炸板与跌停 · 概念聚合{{ state.dash ? ' · ' + state.dash.date : '' }}</div>
      </div>
      <div v-if="state.dash && state.dash.limitup" style="display:flex;gap:8px">
        <span class="tag gold">涨停 {{ stats.limit_up }} 家</span>
        <span class="tag">最高 {{ stats.max_board }} 板</span>
        <span class="tag">炸板率 {{ stats.broken_rate }}%</span>
      </div>
    </div>

    <div v-if="state.error" class="banner error">{{ state.error }}</div>
    <div v-if="!state.dash" class="empty">正在加载短线数据…</div>

    <template v-if="state.dash">
      <section class="kpi-grid">
        <div class="kpi" v-for="k in kpis" :key="k.label" :class="{ 'gold-kpi': k.gold, 'down-kpi': k.down }">
          <div class="kpi-label">{{ k.label }}</div>
          <div class="kpi-value num" :class="k.cls">{{ k.value }}<span v-if="k.suffix" style="font-size:14px">{{ k.suffix }}</span></div>
          <div class="kpi-foot">{{ k.foot }}</div>
        </div>
      </section>

      <section class="card abnormal-card" style="margin-bottom:14px">
        <div class="card-head">
          <div>
            <div class="card-title">异动监控 · 严重异常波动</div>
            <div class="card-sub">
              交易所口径：10 个交易日累计偏离 ≥+100%，或 30 个交易日 ≥+200% 触发严重异动，触发后重点监控约 10 个交易日
              <template v-if="abnormal"> · {{ abnormal.date }} 数据</template>
            </div>
          </div>
          <div class="right">
            <span class="tag" v-if="state.abnormal.loading">刷新中…</span>
            <button class="ghost-btn" @click="loadAbnormal(true)">刷新</button>
          </div>
        </div>
        <div v-if="state.abnormal.error" class="banner error">{{ state.abnormal.error }}</div>
        <div v-if="!abnormal" class="skeleton" style="height:180px"></div>
        <div v-else class="abnormal-grid">
          <div class="abnormal-col" v-for="g in abnormalGroups" :key="g.key" :class="g.tone">
            <div class="abnormal-head">
              <span class="abnormal-title">{{ g.label }}</span>
              <span class="abnormal-count num">{{ g.items.length }}</span>
            </div>
            <div class="abnormal-desc">{{ g.desc }}</div>
            <div class="table-wrap" v-if="g.items.length">
              <table class="simple compact">
                <thead>
                  <tr><th>个股</th><th>触发日</th><th>区间</th><th>偏离值</th><th>{{ g.key === 'exiting' ? '监管剩余' : '触发需' }}</th></tr>
                </thead>
                <tbody>
                  <tr v-for="s in g.items" :key="s.code" @click="openStock(s.code)" style="cursor:pointer" :title="s.rule">
                    <td class="name-cell">{{ s.name }}<small class="num">{{ s.code }}</small></td>
                    <td class="num muted">{{ (s.date || '').slice(5) }}</td>
                    <td class="num">{{ s.days ? s.days + ' 日' : '-' }}</td>
                    <td class="num" :class="tone(s.dev)"><b>{{ fmtPct(s.dev) }}</b></td>
                    <td class="num gold" v-if="g.key === 'exiting'">{{ s.remaining }} 日</td>
                    <td class="num" :class="tone(s.target)" v-else-if="!s.happened">{{ fmtPct(s.target) }}</td>
                    <td class="num muted" v-else>已触发</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-else class="empty" style="padding:24px 0">今日无此类标的</div>
          </div>
        </div>
      </section>

      <section class="card" style="margin-bottom:14px">
        <div class="card-head">
          <div class="tabs">
            <button class="tab" :class="{ active: view === 'stocks' }" @click="view = 'stocks'">涨停明细</button>
            <button class="tab" :class="{ active: view === 'concepts' }" @click="view = 'concepts'">按概念</button>
            <button class="tab" :class="{ active: view === 'broken' }" @click="view = 'broken'">炸板池</button>
            <button class="tab" :class="{ active: view === 'down' }" @click="view = 'down'">跌停池</button>
          </div>
          <div class="right" v-if="view === 'stocks'">
            <input class="search" v-model="keyword" placeholder="搜索名称 / 代码 / 概念" />
            <select class="select" v-model="boardFilter">
              <option value="all">全部高度</option>
              <option v-for="b in boardOptions" :key="b" :value="b">{{ b === 1 ? '首板' : b + '板' }}</option>
              <option value="99">3板及以上</option>
            </select>
            <select class="select" v-model="sortKey">
              <option value="height">按连板高度</option>
              <option value="seal">按封板时间</option>
              <option value="amount">按成交额</option>
              <option value="turnover">按换手率</option>
              <option value="change">按涨跌幅</option>
            </select>
          </div>
        </div>

        <template v-if="view === 'stocks'">
          <div class="filter-bar">
            <span class="eyebrow" style="margin-right:2px">热门概念</span>
            <span class="tag" :class="activeConcept === '' ? 'gold' : ''" style="cursor:pointer" @click="activeConcept = ''">全部</span>
            <span class="tag" :class="activeConcept === c.name ? 'hot' : ''" style="cursor:pointer"
                  v-for="c in conceptChips" :key="c.name" @click="pickConcept(c.name)">
              {{ c.name }} {{ c.count }}
            </span>
            <span class="tag" style="cursor:pointer" v-if="concepts.length > 10" @click="showAllConcepts = !showAllConcepts">
              {{ showAllConcepts ? '收起' : '更多' }}
            </span>
          </div>
          <div class="table-wrap">
            <table class="simple">
              <thead>
                <tr>
                  <th>个股</th>
                  <th>现价</th>
                  <th>涨跌幅</th>
                  <th>连板</th>
                  <th>所属概念</th>
                  <th>涨停原因（自动归纳）</th>
                  <th>封板时间</th>
                  <th>开板</th>
                  <th>成交额</th>
                  <th>换手</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="s in filtered" :key="s.code" @click="openStock(s.code)" style="cursor:pointer">
                  <td class="name-cell">{{ s.name }}<small class="num">{{ s.code }}</small></td>
                  <td class="num" :class="tone(s.change_pct)">{{ fmtPrice(s.price) }}</td>
                  <td class="num" :class="tone(s.change_pct)"><b>{{ fmtPct(s.change_pct) }}</b></td>
                  <td><span class="tag" :class="s.board >= 3 ? 'hot' : s.board >= 2 ? 'gold' : ''">{{ boardText(s.board) }}</span></td>
                  <td>
                    <span class="tag" v-for="c in (s.concepts || []).slice(0, 3)" :key="c">{{ c }}</span>
                  </td>
                  <td :title="s.reason" style="max-width:230px;overflow:hidden;text-overflow:ellipsis">{{ s.reason }}</td>
                  <td class="num">{{ s.seal_time }}</td>
                  <td class="num" :class="s.break_times ? 'up' : 'muted'">{{ s.break_times || 0 }} 次</td>
                  <td class="num">{{ fmtAmount(s.amount) }}</td>
                  <td class="num">{{ s.turnover }}%</td>
                </tr>
                <tr v-if="!filtered.length"><td colspan="10"><div class="empty">没有符合条件的涨停股</div></td></tr>
              </tbody>
            </table>
          </div>
        </template>

        <template v-else-if="view === 'concepts'">
          <div class="concept-grid">
            <div class="concept-card" v-for="c in concepts" :key="c.name">
              <div class="concept-head">
                <span class="concept-name" style="cursor:pointer" @click="openConceptBoard(c)">{{ c.name }}</span>
                <span class="concept-count">{{ c.count }} 家</span>
              </div>
              <div class="concept-leader">领涨 {{ c.leader }} · 最高 {{ c.max_board }} 板</div>
              <div class="concept-stocks">
                <span class="chip" v-for="s in c.stocks" :key="s.code" @click="openStock(s.code)">
                  <b>{{ s.name }}</b><small>{{ boardText(s.board) }}</small>
                </span>
              </div>
            </div>
            <div v-if="!concepts.length" class="empty">暂无概念聚合数据</div>
          </div>
        </template>

        <template v-else-if="view === 'broken'">
          <div class="table-wrap">
            <table class="simple">
              <thead>
                <tr><th>个股</th><th>现价</th><th>涨跌幅</th><th>涨停价</th><th>炸板次数</th><th>首封时间</th><th>成交额</th><th>换手</th></tr>
              </thead>
              <tbody>
                <tr v-for="s in broken" :key="s.code" @click="openStock(s.code)" style="cursor:pointer">
                  <td class="name-cell">{{ s.name }}<small class="num">{{ s.code }}</small></td>
                  <td class="num" :class="tone(s.change_pct)">{{ fmtPrice(s.price) }}</td>
                  <td class="num" :class="tone(s.change_pct)"><b>{{ fmtPct(s.change_pct) }}</b></td>
                  <td class="num muted">{{ fmtPrice(s.limit_price) }}</td>
                  <td class="num up">{{ s.break_times }} 次</td>
                  <td class="num">{{ s.seal_time }}</td>
                  <td class="num">{{ fmtAmount(s.amount) }}</td>
                  <td class="num">{{ s.turnover }}%</td>
                </tr>
                <tr v-if="!broken.length"><td colspan="8"><div class="empty">今日无炸板个股</div></td></tr>
              </tbody>
            </table>
          </div>
        </template>

        <template v-else>
          <div class="table-wrap">
            <table class="simple">
              <thead>
                <tr><th>个股</th><th>现价</th><th>涨跌幅</th><th>封单金额</th><th>连续跌停</th><th>成交额</th><th>换手</th></tr>
              </thead>
              <tbody>
                <tr v-for="s in limitDown" :key="s.code" @click="openStock(s.code)" style="cursor:pointer">
                  <td class="name-cell">{{ s.name }}<small class="num">{{ s.code }}</small></td>
                  <td class="num down">{{ fmtPrice(s.price) }}</td>
                  <td class="num down"><b>{{ fmtPct(s.change_pct) }}</b></td>
                  <td class="num">{{ fmtAmount(s.seal_fund) }}</td>
                  <td class="num">{{ s.stat_days || 1 }} 天</td>
                  <td class="num">{{ fmtAmount(s.amount) }}</td>
                  <td class="num">{{ s.turnover }}%</td>
                </tr>
                <tr v-if="!limitDown.length"><td colspan="7"><div class="empty">今日无跌停个股</div></td></tr>
              </tbody>
            </table>
          </div>
        </template>
      </section>

      <section class="section-grid" v-if="ladder && ladder.levels.length">
        <div class="card ladder-card">
          <div class="card-head">
            <div>
              <div class="card-title">连板天梯</div>
              <div class="card-sub">按高度分层，点击个股查看K线</div>
            </div>
            <div class="right"><span class="tag gold">晋级率 {{ stats.promote_rate }}%</span></div>
          </div>
          <div class="ladder">
            <div class="ladder-row" v-for="level in ladder.levels.slice(0, 6)" :key="level.board">
              <div class="board-badge num">{{ level.label }}</div>
              <div class="chips">
                <span class="chip" v-for="s in level.stocks.slice(0, 10)" :key="s.code" @click="openStock(s.code)">
                  <b>{{ s.name }}</b><small class="num">{{ s.seal_time }}</small>
                </span>
                <span class="chip" v-if="level.stocks.length > 10" @click="go('short')">+{{ level.stocks.length - 10 }}</span>
              </div>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-head">
            <div>
              <div class="card-title">昨日连板断板表现</div>
              <div class="card-sub">未晋级个股今日涨跌，衡量接力亏钱效应</div>
            </div>
          </div>
          <div class="stat-grid" style="grid-template-columns:repeat(2,minmax(0,1fr))">
            <div class="stat">
              <div class="stat-label">断板均值</div>
              <div class="stat-value num" :class="tone(stats.broken_avg)">{{ fmtPct(stats.broken_avg) }}</div>
              <div class="stat-sub">共 {{ brokenStocks.length }} 只断板</div>
            </div>
            <div class="stat">
              <div class="stat-label">晋级 / 昨日连板</div>
              <div class="stat-value num gold">{{ stats.promoted }}<small>/{{ stats.prev_lianban }}</small></div>
              <div class="stat-sub">晋级率 {{ stats.promote_rate }}%</div>
            </div>
          </div>
          <div class="table-wrap" style="margin-top:10px">
            <table class="simple">
              <thead><tr><th>断板股</th><th>昨日高度</th><th>今日涨跌</th></tr></thead>
              <tbody>
                <tr v-for="s in brokenStocks.slice(0, 8)" :key="s.code" @click="openStock(s.code)" style="cursor:pointer">
                  <td class="name-cell">{{ s.name }}<small class="num">{{ s.code }}</small></td>
                  <td class="num muted">{{ boardText(s.board) }}</td>
                  <td class="num" :class="tone(s.change_pct)">{{ fmtPct(s.change_pct) }}</td>
                </tr>
                <tr v-if="!brokenStocks.length"><td colspan="3"><div class="empty">昨日无连板股断板</div></td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <div class="banner" style="margin-top:14px">
        涨停原因为系统依据个股所属概念与行业自动归纳，仅作结构化复盘参考；不构成任何投资建议。
      </div>
    </template>
  </div>`,
};

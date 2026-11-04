import { closeSector, openStock, state } from "../store.js";
import { fmtAmount, fmtFlow, fmtPct, fmtNumber, fmtPrice, tone } from "../utils.js";

export default {
  setup() {
    const { computed, ref } = Vue;
    const sector = computed(() => state.sector);
    const data = computed(() => state.sector.data);
    const sortKey = ref("change");

    const stocks = computed(() => {
      const items = (data.value?.stocks || []).slice();
      const sorters = {
        change: (a, b) => b.change_pct - a.change_pct,
        amount: (a, b) => (b.amount || 0) - (a.amount || 0),
        turnover: (a, b) => (b.turnover || 0) - (a.turnover || 0),
        flow: (a, b) => (b.main_flow || 0) - (a.main_flow || 0),
      };
      return items.sort(sorters[sortKey.value] || sorters.change);
    });

    const info = computed(() => data.value?.info || {});
    const leaders = computed(() => data.value?.leaders || []);

    function onMask(event) {
      if (event.target.classList.contains("drawer-mask")) closeSector();
    }

    return {
      state, sector, data, info, leaders, stocks, sortKey, closeSector, openStock, onMask,
      fmtAmount, fmtFlow, fmtPct, fmtNumber, fmtPrice, tone,
    };
  },
  template: `
  <div v-if="sector.open">
    <div class="drawer-mask" @click="onMask"></div>
    <aside class="drawer">
      <div class="drawer-head">
        <div>
          <div style="display:flex;align-items:center;gap:10px">
            <span class="modal-title">{{ data && info.name ? info.name : sector.name || '板块详情' }}</span>
            <span class="tag num">{{ sector.code }}</span>
          </div>
          <div class="card-sub" style="margin-top:6px" v-if="info.name">
            上涨 {{ info.up }} 家 · 下跌 {{ info.down }} 家 · 涨停 {{ data.limit_up_count }} 家
          </div>
        </div>
        <div style="margin-left:auto;display:flex;align-items:center;gap:14px">
          <div v-if="info.name" style="text-align:right">
            <div class="num" style="font-size:26px;font-weight:800" :class="tone(info.change_pct)">{{ fmtPct(info.change_pct) }}</div>
            <div class="small muted">成交 {{ fmtAmount(info.amount) }} · 换手 {{ info.turnover }}%</div>
          </div>
          <button class="modal-close" @click="closeSector">✕</button>
        </div>
      </div>

      <div class="drawer-body">
        <div v-if="sector.loading" class="skeleton" style="height:300px"></div>
        <div v-else-if="sector.error" class="banner error">{{ sector.error }}</div>
        <template v-else-if="data">
          <div class="eyebrow" style="margin-bottom:8px">领涨龙头</div>
          <div class="leader-row">
            <div class="leader" v-for="(s, i) in leaders" :key="s.code" @click="openStock(s.code)">
              <div class="k">TOP {{ i + 1 }}</div>
              <div class="n">{{ s.name }}</div>
              <div class="p num" :class="tone(s.change_pct)">{{ fmtPct(s.change_pct) }} · {{ fmtPrice(s.price) }}</div>
            </div>
            <div v-if="!leaders.length" class="empty" style="grid-column:1/-1">暂无龙头数据</div>
          </div>

          <div class="card-head">
            <div class="card-title" style="font-size:14px">成分股（{{ data.total }} 只）</div>
            <div class="right">
              <select class="select" v-model="sortKey">
                <option value="change">按涨跌幅</option>
                <option value="amount">按成交额</option>
                <option value="turnover">按换手率</option>
                <option value="flow">按主力净额</option>
              </select>
            </div>
          </div>

          <div class="table-wrap">
            <table class="simple">
              <thead>
                <tr><th>#</th><th>个股</th><th>现价</th><th>涨跌幅</th><th>成交额</th><th>换手</th><th>量比</th><th>主力净额</th></tr>
              </thead>
              <tbody>
                <tr v-for="(s, i) in stocks" :key="s.code" @click="openStock(s.code)" style="cursor:pointer">
                  <td><span class="rank" :class="{ top: i < 3 }">{{ i + 1 }}</span></td>
                  <td class="name-cell">{{ s.name }}<small class="num">{{ s.code }}</small></td>
                  <td class="num" :class="tone(s.change_pct)">{{ fmtPrice(s.price) }}</td>
                  <td class="num" :class="tone(s.change_pct)"><b>{{ fmtPct(s.change_pct) }}</b></td>
                  <td class="num">{{ fmtAmount(s.amount) }}</td>
                  <td class="num">{{ s.turnover }}%</td>
                  <td class="num">{{ s.volume_ratio }}</td>
                  <td class="num" :class="tone(s.main_flow)">{{ fmtFlow(s.main_flow) }}</td>
                </tr>
                <tr v-if="!stocks.length"><td colspan="8"><div class="empty">暂无成分股数据</div></td></tr>
              </tbody>
            </table>
          </div>
        </template>
      </div>
    </aside>
  </div>`,
};

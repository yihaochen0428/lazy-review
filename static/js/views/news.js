import { loadNews, state } from "../store.js";
import { api } from "../api.js";
import { fmtPct, tone } from "../utils.js";

export default {
  setup() {
    const { computed, onMounted, ref } = Vue;
    const group = ref("upcoming");
    const impactFilter = ref("全部");
    const scopeFilter = ref("全部");
    const selected = ref(null);
    const ai = ref(null);
    const aiLoading = ref(false);
    const aiError = ref("");
    const eventCat = ref("全部");

    onMounted(() => loadNews());

    const data = computed(() => state.news.data);

    function pick(item) {
      selected.value = item;
      ai.value = null;
      aiError.value = "";
    }

    function back() {
      selected.value = null;
      ai.value = null;
      aiError.value = "";
    }

    function filter(list) {
      return (list || []).filter((n) => {
        if (impactFilter.value !== "全部" && n.impact !== impactFilter.value) return false;
        if (scopeFilter.value !== "全部" && n.scope !== scopeFilter.value) return false;
        return true;
      });
    }

    const upcoming = computed(() => filter(data.value?.upcoming));
    const past = computed(() => filter(data.value?.past));
    const list = computed(() => (group.value === "upcoming" ? upcoming.value : past.value));

    const events = computed(() => data.value?.events || []);
    const hotspots = computed(() => data.value?.hotspots || []);
    const eventCats = computed(() => {
      const cats = [];
      events.value.forEach((e) => {
        if (e.category && !cats.includes(e.category)) cats.push(e.category);
      });
      return ["全部", ...cats];
    });
    const eventGroups = computed(() => {
      const source = eventCat.value === "全部"
        ? events.value
        : events.value.filter((e) => e.category === eventCat.value);
      const groups = [];
      source.forEach((e) => {
        let slot = groups.find((g) => g.date === e.date);
        if (!slot) {
          slot = { date: e.date, label: e.day_label, items: [] };
          groups.push(slot);
        }
        slot.items.push(e);
      });
      return groups;
    });

    function strengthClass(strength) {
      return strength === "强" ? "hot" : strength === "中" ? "gold" : "";
    }

    function impClass(importance) {
      return importance === "高" ? "imp-high" : importance === "中" ? "imp-mid" : "imp-low";
    }

    function impactClass(impact) {
      return impact === "利好" ? "up" : impact === "利空" ? "down" : "muted";
    }

    async function analyze() {
      if (!selected.value || aiLoading.value) return;
      aiLoading.value = true;
      aiError.value = "";
      try {
        ai.value = await api.newsAnalyze(selected.value);
      } catch (err) {
        aiError.value = err.message || "AI 分析失败";
      } finally {
        aiLoading.value = false;
      }
    }

    return {
      state, data, group, impactFilter, scopeFilter, selected, ai, aiLoading, aiError,
      upcoming, past, list, pick, back, impactClass, analyze, loadNews, fmtPct, tone,
      eventCat, events, hotspots, eventCats, eventGroups, strengthClass, impClass,
    };
  },
  template: `
  <div class="page">
    <div class="page-head">
      <div>
        <div class="page-title">新闻 · 消息面</div>
        <div class="page-desc">
          聚焦重要新闻、行业政策、行业龙头与未来事件，自动标注利好 / 利空
          <template v-if="data && data.updated_at"> · {{ data.updated_at }} 更新</template>
        </div>
      </div>
      <div v-if="data" style="display:flex;gap:8px">
        <span class="tag gold">未来 {{ data.counts.upcoming }} 条</span>
        <span class="tag">过去 {{ data.counts.past }} 条</span>
      </div>
    </div>

    <div v-if="state.news.error" class="banner error">{{ state.news.error }}</div>

    <template v-if="selected">
      <section class="card news-detail">
        <div class="card-head">
          <div>
            <div class="card-title">新闻详情</div>
            <div class="card-sub">{{ selected.time_text }} · {{ selected.source }}</div>
          </div>
          <div class="right">
            <button class="ghost-btn" @click="back">← 返回列表</button>
            <a v-if="selected.url" class="ghost-btn" :href="selected.url" target="_blank" rel="noopener">原文 ↗</a>
          </div>
        </div>
        <div class="news-detail-title">{{ selected.title }}</div>
        <div class="news-meta" style="margin-top:10px">
          <span class="impact" :class="impactClass(selected.impact)">{{ selected.impact }}</span>
          <span class="tag" :class="selected.scope === '国际' ? '' : 'gold'">{{ selected.scope }}</span>
          <span class="tag" v-for="tag in selected.tags" :key="tag">{{ tag }}</span>
          <span class="tag" :class="selected.upcoming ? 'hot' : ''">{{ selected.upcoming ? '未来事件' : '已发生' }}</span>
          <span class="news-level num" :class="'lv' + selected.level">L{{ selected.level }}</span>
        </div>
        <div class="news-detail-sum">{{ selected.summary || '该条快讯暂无正文摘要，可点击原文查看完整内容。' }}</div>

        <div class="ai-analyze">
          <div class="ai-analyze-head">
            <span class="eyebrow">AI 分析</span>
            <button class="refresh-btn" @click="analyze" :disabled="aiLoading">
              {{ aiLoading ? '分析中…' : ai ? '重新分析' : 'AI 分析这条新闻' }}
            </button>
          </div>
          <div v-if="aiError" class="banner error">{{ aiError }}</div>
          <template v-if="ai">
            <div class="ai-summary">{{ ai.summary }}</div>
            <div class="news-meta" style="margin:10px 0 4px">
              <span class="impact" :class="impactClass(ai.impact)">{{ ai.impact }}</span>
              <span class="tag" v-for="s in ai.sectors" :key="s" class="gold">{{ s }}</span>
              <span class="tag">{{ ai.upcoming ? '前瞻事件' : '已发生事件' }}</span>
            </div>
            <ul class="driver-list">
              <li v-for="d in ai.drivers" :key="d">{{ d }}</li>
            </ul>
            <div class="play-grid" v-if="(ai.leaders && ai.leaders.length) || (ai.small_caps && ai.small_caps.length)">
              <div class="play-col">
                <div class="play-head">产业龙头 · 业务维度</div>
                <div class="play-row" v-for="s in (ai.leaders || [])" :key="s.code">
                  <div class="play-name">
                    <b>{{ s.name }}</b>
                    <span class="tag num">{{ s.code }}</span>
                    <span class="num" :class="tone(s.change_5d)">{{ fmtPct(s.change_5d) }}</span>
                  </div>
                  <div class="play-reason">{{ s.reason }}</div>
                </div>
                <div v-if="!ai.leaders || !ai.leaders.length" class="muted small">暂无映射龙头</div>
              </div>
              <div class="play-col">
                <div class="play-head">小盘弹性 · 技术维度</div>
                <div class="play-row" v-for="s in (ai.small_caps || [])" :key="s.code">
                  <div class="play-name">
                    <b>{{ s.name }}</b>
                    <span class="tag num">{{ s.code }}</span>
                    <span class="num" :class="tone(s.change_5d)">{{ fmtPct(s.change_5d) }}</span>
                  </div>
                  <div class="play-reason">{{ s.reason }}</div>
                </div>
                <div v-if="!ai.small_caps || !ai.small_caps.length" class="muted small">
                  当前候选小盘股技术形态未走强，暂无合格弹性标的
                </div>
              </div>
            </div>
            <div class="muted small" style="margin-top:6px" v-if="ai.play_note">{{ ai.play_note }}</div>
            <div class="ai-block ai-playbook">
              <div class="ai-block-head">
                <span class="ai-index num">!</span>
                <span class="ai-block-title">应对提示</span>
              </div>
              <div class="ai-block-body">{{ ai.playbook }}</div>
            </div>
          </template>
          <div v-else-if="!aiLoading" class="empty" style="padding:18px 0">
            点击「AI 分析这条新闻」，自动拆解影响路径、关联板块与持续性
          </div>
        </div>
      </section>
    </template>

    <template v-else>
      <section class="card" v-if="data && data.analysis">
        <div class="card-head">
          <div>
            <div class="card-title">消息面速览</div>
            <div class="card-sub">按影响力聚合，国内 / 国际与流动性线索</div>
          </div>
          <div class="right">
            <button class="ghost-btn" @click="loadNews(true)">刷新</button>
          </div>
        </div>
        <div class="ai-summary">{{ data.analysis.summary }}</div>
        <div class="nv-liq">{{ data.analysis.liquidity }}</div>
        <div class="nv-industry" v-if="data.analysis.industry && data.analysis.industry.length">
          <span class="eyebrow">行业映射</span>
          <span class="tag gold" v-for="n in data.analysis.industry" :key="n.name">{{ n.name }} · {{ n.count }}条</span>
        </div>
      </section>

      <section class="card" style="margin-top:14px" v-if="events.length || hotspots.length">
        <div class="card-head">
          <div>
            <div class="card-title">未来事件日历 · 预期热点</div>
            <div class="card-sub">
              东财财经日历事件 + 同花顺概念映射，按重要性加权聚合
              <template v-if="data && data.event_window"> · {{ data.event_window }}</template>
            </div>
          </div>
          <div class="right">
            <span class="tag gold" v-if="events.length">未来 {{ events.length }} 个事件</span>
            <button class="ghost-btn" @click="loadNews(true)">刷新</button>
          </div>
        </div>

        <div class="eh-list" v-if="hotspots.length">
          <div class="eh" v-for="h in hotspots" :key="h.keyword" :class="strengthClass(h.strength)">
            <div class="eh-top">
              <span class="eh-name">{{ h.name }}</span>
              <span class="eh-strength" :class="strengthClass(h.strength)">{{ h.strength }}</span>
            </div>
            <div class="eh-stat">
              <span class="num">{{ h.count }}</span> 个事件 · 热度 <b class="num">{{ h.score }}</b>
            </div>
            <div class="eh-events">
              <span v-for="(e, i) in h.events" :key="i" :title="e">{{ e }}</span>
            </div>
          </div>
        </div>

        <div class="ev-filter">
          <div class="tabs">
            <button class="tab" v-for="c in eventCats" :key="c"
                    :class="{ active: eventCat === c }" @click="eventCat = c">{{ c }}</button>
          </div>
        </div>

        <div class="ev-list" v-if="eventGroups.length">
          <div class="ev-group" v-for="g in eventGroups" :key="g.date">
            <div class="ev-date">
              <span class="num">{{ g.date }}</span>
              <span class="ev-day">{{ g.label }}</span>
            </div>
            <div class="ev-rows">
              <div class="ev-row" v-for="(e, i) in g.items" :key="i">
                <span class="ev-imp num" :class="impClass(e.importance)">{{ e.importance }}</span>
                <div class="ev-main">
                  <div class="ev-name">
                    <a v-if="e.url" :href="e.url" target="_blank" rel="noopener">{{ e.name }}</a>
                    <template v-else>{{ e.name }}</template>
                  </div>
                  <div class="ev-meta">
                    <span class="tag">{{ e.category }}</span>
                    <span class="tag gold" v-for="r in e.related" :key="r">{{ r }}</span>
                    <span class="ev-place" v-if="e.city">{{ e.city }}</span>
                    <span class="ev-place" v-if="e.end_date">{{ e.date }} ~ {{ e.end_date }}</span>
                  </div>
                </div>
                <span class="ev-src">{{ e.source }}</span>
              </div>
            </div>
          </div>
        </div>
        <div v-else class="empty">该分类暂无未来事件</div>
      </section>

      <section class="card" style="margin-top:14px">
        <div class="card-head">
          <div class="tabs">
            <button class="tab" :class="{ active: group === 'upcoming' }" @click="group = 'upcoming'">
              未来将要发生 ({{ upcoming.length }})
            </button>
            <button class="tab" :class="{ active: group === 'past' }" @click="group = 'past'">
              过去已发生 ({{ past.length }})
            </button>
          </div>
          <div class="right">
            <div class="tabs">
              <button class="tab" v-for="t in ['全部', '利好', '利空']" :key="t"
                      :class="{ active: impactFilter === t }" @click="impactFilter = t">{{ t }}</button>
            </div>
            <select class="select" v-model="scopeFilter">
              <option value="全部">全部地区</option>
              <option value="国内">国内</option>
              <option value="国际">国际</option>
            </select>
          </div>
        </div>

        <div v-if="state.news.loading && !data" class="skeleton" style="height:320px"></div>
        <div class="news-list" v-else-if="list.length">
          <a class="news-item" v-for="n in list" :key="n.title" href="javascript:void(0)"
             @click.prevent="pick(n)">
            <span class="news-time num">{{ n.time_text }}</span>
            <span class="news-scope" :class="n.scope === '国际' ? 'intl' : ''">{{ n.scope }}</span>
            <div class="news-main">
              <div class="news-title">{{ n.title }}</div>
              <div class="news-meta">
                <span class="impact" :class="impactClass(n.impact)">{{ n.impact }}</span>
                <span class="tag" v-for="tag in n.tags" :key="tag">{{ tag }}</span>
                <span class="news-src">{{ n.source }}</span>
              </div>
            </div>
            <span class="news-level num" :class="'lv' + n.level">L{{ n.level }}</span>
          </a>
        </div>
        <div v-else class="empty">该分组暂无符合条件的新闻</div>
      </section>
    </template>
  </div>`,
};

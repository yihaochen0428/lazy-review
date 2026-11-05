import { areaPath, sparkPath } from "./utils.js";

const uid = () => Math.random().toString(36).slice(2, 8);

export const Chart = {
  props: {
    option: { type: Object, required: true },
    height: { type: String, default: "460px" },
    tall: { type: Boolean, default: false },
  },
  // tall 模式的尺寸交给 CSS（.chart-box.tall），便于移动端媒体查询降高；显式 height 才写内联
  template: `<div ref="el" class="chart-box" :class="{tall}" :style="tall ? null : { height }"></div>`,
  mounted() {
    this.chart = window.echarts.init(this.$refs.el, null, { renderer: "canvas" });
    this.chart.setOption(this.option, true);
    this.onResize = () => this.chart && this.chart.resize();
    window.addEventListener("resize", this.onResize);
  },
  watch: {
    option: {
      deep: true,
      handler(value) {
        if (this.chart) this.chart.setOption(value, true);
      },
    },
  },
  unmounted() {
    window.removeEventListener("resize", this.onResize);
    if (this.chart) this.chart.dispose();
  },
};

export const Sparkline = {
  props: {
    values: { type: Array, default: () => [] },
    width: { type: Number, default: 96 },
    height: { type: Number, default: 26 },
  },
  setup(props) {
    const gid = uid();
    return { gid, sparkPath, areaPath, props };
  },
  computed: {
    rising() {
      const v = this.values;
      return v.length < 2 ? true : v[v.length - 1] >= v[0];
    },
    color() {
      return this.rising ? "#ff5063" : "#14c39a";
    },
  },
  template: `
    <svg class="spark" :width="width" :height="height" :viewBox="'0 0 ' + width + ' ' + height" preserveAspectRatio="none">
      <defs>
        <linearGradient :id="gid" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" :stop-color="color" stop-opacity="0.30" />
          <stop offset="100%" :stop-color="color" stop-opacity="0" />
        </linearGradient>
      </defs>
      <path :d="areaPath(values, width, height)" :fill="'url(#' + gid + ')'" />
      <path class="line" :d="sparkPath(values, width, height)" :stroke="color" stroke-linecap="round" stroke-linejoin="round" />
    </svg>`,
};

export const MiniCandle = {
  props: {
    bars: { type: Array, default: () => [] },
    prevClose: { type: Number, default: null },
    height: { type: Number, default: 44 },
  },
  computed: {
    candles() {
      const bars = this.bars || [];
      if (!bars.length) return [];
      const values = [];
      bars.forEach((b) => {
        values.push(b.high, b.low);
      });
      if (this.prevClose) values.push(this.prevClose);
      const max = Math.max(...values);
      const min = Math.min(...values);
      const range = max - min || 1;
      const pad = 2;
      const inner = this.height - pad * 2;
      const y = (v) => pad + ((max - v) / range) * inner;
      const step = 240 / bars.length;
      const bodyW = Math.max(1.4, step * 0.62);
      return bars.map((b, i) => {
        const rising = b.close >= b.open;
        return {
          x: step * (i + 0.5),
          highY: y(b.high),
          lowY: y(b.low),
          bodyX: step * (i + 0.5) - bodyW / 2,
          bodyY: y(Math.max(b.open, b.close)),
          bodyW,
          bodyH: Math.max(0.8, Math.abs(y(b.close) - y(b.open))),
          color: rising ? "#ff5063" : "#14c39a",
          opacity: i === bars.length - 1 ? 1 : 0.6,
        };
      });
    },
    baseY() {
      if (!this.prevClose || !this.bars.length) return null;
      const values = [];
      this.bars.forEach((b) => values.push(b.high, b.low));
      values.push(this.prevClose);
      const max = Math.max(...values);
      const min = Math.min(...values);
      const range = max - min || 1;
      return 2 + ((max - this.prevClose) / range) * (this.height - 4);
    },
  },
  template: `
    <svg class="mini-candle" :viewBox="'0 0 240 ' + height" preserveAspectRatio="none">
      <line v-if="baseY !== null" class="mini-base" x1="0" :y1="baseY" x2="240" :y2="baseY" />
      <g v-for="(c, i) in candles" :key="i" :opacity="c.opacity">
        <line :x1="c.x" :x2="c.x" :y1="c.highY" :y2="c.lowY" :stroke="c.color" stroke-width="1" />
        <rect :x="c.bodyX" :y="c.bodyY" :width="c.bodyW" :height="c.bodyH" :fill="c.color" />
      </g>
    </svg>`,
};

export const SentimentGauge = {
  props: { score: { type: Number, default: 0 }, label: { type: String, default: "" } },
  setup() {
    return { radius: 56, cx: 74, cy: 74 };
  },
  computed: {
    circumference() {
      return 2 * Math.PI * this.radius;
    },
    arcLength() {
      return this.circumference * 0.75;
    },
    valueLength() {
      return Math.max(2, this.arcLength * (Math.min(Math.max(this.score, 0), 100) / 100));
    },
    color() {
      if (this.score >= 80) return "#ff5063";
      if (this.score >= 65) return "#f2b845";
      if (this.score >= 45) return "#7f8ea3";
      if (this.score >= 30) return "#35c7a7";
      return "#14c39a";
    },
  },
  template: `
    <svg class="gauge" viewBox="0 0 148 128">
      <circle cx="74" cy="74" r="56" fill="none" stroke="rgba(255,255,255,0.07)" stroke-width="10"
        :stroke-dasharray="arcLength + ' ' + circumference" transform="rotate(135 74 74)" stroke-linecap="round" />
      <circle cx="74" cy="74" r="56" fill="none" :stroke="color" stroke-width="10"
        :stroke-dasharray="valueLength + ' ' + circumference" transform="rotate(135 74 74)" stroke-linecap="round"
        style="transition: stroke-dasharray .6s ease; filter: drop-shadow(0 0 6px rgba(255,255,255,.15))" />
      <text x="74" y="72" text-anchor="middle" class="gauge-score num">{{ score.toFixed(1) }}</text>
      <text x="74" y="92" text-anchor="middle" fill="#6c7789" font-size="11" letter-spacing="2">情绪温度</text>
      <text x="74" y="112" text-anchor="middle" :fill="color" font-size="12" font-weight="600">{{ label }}</text>
    </svg>`,
};

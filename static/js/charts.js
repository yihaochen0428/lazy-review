const UP = "#ff5063";
const DOWN = "#14c39a";
const GOLD = "#f2b845";
const BLUE = "#5b8cff";
const PURPLE = "#b07cff";
const TEAL = "#2dd4bf";

const AXIS_LABEL = { color: "#6c7789", fontSize: 10, fontFamily: "Inter, PingFang SC" };
const SPLIT_LINE = { lineStyle: { color: "rgba(255,255,255,0.05)" } };

const AXIS = {
  axisLine: { lineStyle: { color: "rgba(255,255,255,0.10)" } },
  axisTick: { show: false },
  axisLabel: AXIS_LABEL,
  splitLine: SPLIT_LINE,
};

function axisPointer() {
  return {
    link: [{ xAxisIndex: "all" }],
    label: { backgroundColor: "rgba(20,25,35,.95)", borderColor: "rgba(255,255,255,.12)", color: "#dfe5ef", fontSize: 10 },
    lineStyle: { color: "rgba(255,255,255,.25)", type: "dashed" },
    crossStyle: { color: "rgba(255,255,255,.25)" },
  };
}

function bollish(lineWidth = 1) {
  return { type: "line", showSymbol: false, connectNulls: true, lineStyle: { width: lineWidth }, emphasis: { disabled: true } };
}

function indicatorSeries(series, indicator) {
  if (indicator === "KDJ") {
    return [
      { ...bollish(), name: "K", data: series.k, itemStyle: { color: GOLD }, lineStyle: { color: GOLD, width: 1.2 } },
      { ...bollish(), name: "D", data: series.d, itemStyle: { color: BLUE }, lineStyle: { color: BLUE, width: 1.2 } },
      { ...bollish(), name: "J", data: series.j, itemStyle: { color: UP }, lineStyle: { color: UP, width: 1.2 } },
    ];
  }
  if (indicator === "RSI") {
    return [
      {
        ...bollish(1.4), name: "RSI(14)", data: series.rsi, itemStyle: { color: GOLD },
        lineStyle: { color: GOLD, width: 1.4 },
        markLine: {
          silent: true, symbol: "none",
          lineStyle: { color: "rgba(255,255,255,.18)", type: "dashed", width: 1 },
          label: { color: "#6c7789", fontSize: 9, position: "insideEndTop" },
          data: [{ yAxis: 70, name: "70" }, { yAxis: 30, name: "30" }],
        },
      },
    ];
  }
  return [
    {
      type: "bar", name: "MACD", data: series.hist, barWidth: "58%",
      itemStyle: {
        color: (params) => (params.value >= 0 ? "rgba(255,80,99,.75)" : "rgba(20,195,154,.75)"),
      },
    },
    { ...bollish(), name: "DIF", data: series.dif, itemStyle: { color: GOLD }, lineStyle: { color: GOLD, width: 1.2 } },
    { ...bollish(), name: "DEA", data: series.dea, itemStyle: { color: BLUE }, lineStyle: { color: BLUE, width: 1.2 } },
  ];
}

function fibLines(fib) {
  if (!fib || !fib.length) return [];
  return fib
    .filter((level) => [0.0, 0.382, 0.5, 0.618, 1.0].includes(level.ratio))
    .map((level) => ({
      yAxis: level.price,
      name: level.ratio === 0 || level.ratio === 1 ? "" : `${(level.ratio * 100).toFixed(1)}%`,
    }));
}

function chanOverlay(bars, dates, chan) {
  if (!chan || !bars.length) return { series: [], markArea: null };
  const n = bars.length;
  const dateIndex = new Map(bars.map((b, i) => [String(b.date), i]));
  // 后端笔/中枢自带原始下标，优先用下标，缺失时再按日期回退，越界一律丢弃
  const idxOf = (item) => {
    if (!item) return -1;
    if (Number.isInteger(item.index) && item.index >= 0 && item.index < n) return item.index;
    const byDate = dateIndex.get(String(item.date));
    return byDate === undefined ? -1 : byDate;
  };
  const labelAt = (index) => (index >= 0 && index < n ? dates[index] : null);

  const sequence = [];
  (chan.strokes || []).forEach((stroke, i) => {
    const from = idxOf(stroke.from);
    const to = idxOf(stroke.to);
    if (from < 0 || to < 0) return;
    if (!sequence.length) sequence.push([from, stroke.from.price]);
    sequence.push([to, stroke.to.price]);
  });

  const centers = (chan.centers || [])
    .map((c) => {
      const start = labelAt(c.start_index);
      const end = labelAt(c.end_index);
      if (start === null || end === null) return null;
      return [{ xAxis: start, yAxis: c.zd }, { xAxis: end, yAxis: c.zg }];
    })
    .filter(Boolean);

  const points = (chan.points || []).map((p) => {
    const idx = idxOf(p);
    if (idx < 0) return null;
    const candidate = p.type && p.type.indexOf("候选") >= 0;
    return {
      value: [idx, p.price, p.type, p.reason],
      label: { position: p.side === "buy" ? "bottom" : "top", color: candidate ? GOLD : "#dfe5ef" },
      itemStyle: {
        color: candidate ? "rgba(242,184,69,.35)" : p.side === "buy" ? GOLD : BLUE,
        borderColor: candidate ? GOLD : "#0a0e15",
        borderWidth: candidate ? 1.5 : 1,
        borderType: candidate ? "dashed" : "solid",
      },
    };
  }).filter(Boolean);

  const series = [];
  if (sequence.length > 1) {
    series.push({
      type: "line",
      name: "缠论笔",
      data: sequence,
      showSymbol: false,
      lineStyle: { color: "rgba(242,184,69,.85)", width: 1.4 },
      emphasis: { disabled: true },
      z: 5,
    });
  }
  if (points.length) {
    series.push({
      type: "scatter",
      name: "缠论买卖点",
      data: points,
      symbolSize: 10,
      label: {
        show: true,
        color: "#dfe5ef",
        fontSize: 10,
        fontWeight: "600",
        backgroundColor: "rgba(10,14,21,.86)",
        borderColor: "rgba(255,255,255,.16)",
        borderWidth: 1,
        borderRadius: 4,
        padding: [2, 5],
        formatter: (p) => (p.value ? p.value[2] : ""),
      },
      tooltip: {
        formatter: (p) => `${p.value[2]} · ${p.value[1]}<br/><span style="color:#a3adc0;font-size:10.5px">${p.value[3] || ""}</span>`,
      },
      z: 8,
    });
  }
  return {
    series,
    markArea: centers.length
      ? {
          silent: true,
          itemStyle: {
            color: "rgba(242,184,69,.06)",
            borderColor: "rgba(242,184,69,.32)",
            borderWidth: 1,
            borderType: "dashed",
          },
          data: centers,
        }
      : null,
  };
}

function minToTime(m) {
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

// A股分时横轴：09:30-11:30 + 13:00-15:00，共 240 分钟，午休不占宽度
function intradaySlots(scale = 5) {
  const slots = ["09:30"];
  for (let m = 570 + scale; m <= 690; m += scale) slots.push(minToTime(m));
  for (let m = 780 + scale; m <= 900; m += scale) slots.push(minToTime(m));
  return slots;
}

export function intradayOption(data, indicator = "MACD", planLines = null, planMarks = null) {
  const intra = data.intraday || {};
  const bars = intra.bars || data.bars || [];
  const barTimes = bars.map((b) => b.time || String(b.date).slice(11, 16));
  const prevClose = intra.prev_close || (bars[0] && bars[0].open);
  const closes = bars.map((b) => b.close);
  const vwap = intra.vwap || [];
  const hasVwap = vwap.some((v) => v != null);
  const points = intra.t_points || [];
  const series = data.series || {};

  // 横轴固定铺满全天 240 分钟：已发生的 K 线落在对应槽位，未交易时段留空，
  // 避免盘中只有半日数据时被强行拉伸铺满整幅（与同花顺/东财版式一致）
  const scale = (() => {
    if (bars.length < 2) return 5;
    const mins = (t) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5));
    const d = mins(barTimes[1]) - mins(barTimes[0]);
    return d > 0 && d <= 60 ? d : 5;
  })();
  const slots = intradaySlots(scale);
  const slotIndex = new Map(slots.map((t, i) => [t, i]));
  const slotToBar = new Map();
  barTimes.forEach((t, i) => {
    const si = slotIndex.get(t);
    if (si !== undefined) slotToBar.set(si, i);
  });
  // 首根 K 线的开盘价即 09:30 集合竞价点，补出该点让分时线从最左端起步
  const openFill = bars.length && barTimes[0] !== "09:30"
    ? (bars[0].open != null ? bars[0].open : closes[0])
    : null;
  const place = (values, fillFirst = null) => {
    const out = new Array(slots.length).fill(null);
    if (fillFirst != null) out[0] = fillFirst;
    values.forEach((v, i) => {
      const si = slotIndex.get(barTimes[i]);
      if (si !== undefined) out[si] = v;
    });
    return out;
  };
  const closeLine = place(closes, openFill);
  const vwapLine = place(vwap);
  const volumeLine = place(bars.map((b) => b.volume));

  // 横轴刻度：09:30 / 10:30 / 11:30·13:00（午休合并） / 14:00 / 15:00
  const noonIdx = 1 + Math.round((690 - 570) / scale);
  const middaySlot = slots[noonIdx - 1];
  const labelSet = new Set(["09:30", "10:30", "15:00"]);
  [middaySlot, slots[noonIdx + Math.round((900 - 780) / scale / 2)]].forEach((t) => {
    if (t) labelSet.add(t);
  });
  const labelText = (t) => (t === middaySlot ? "11:30/13:00" : t);
  // 与主流软件一致：以昨收为中心，价格轴与右侧百分比轴共用对称空间
  const devMax = closes.reduce((m, c) => Math.max(m, Math.abs((c / prevClose - 1) * 100)), 0);
  const vwapMax = vwap.reduce((m, v) => (v == null ? m : Math.max(m, Math.abs((v / prevClose - 1) * 100))), 0);
  const limitPct = Math.max(1, Math.ceil(Math.max(devMax, vwapMax) * 1.15 * 2) / 2);

  // 做T点位：p.index 是 bars 下标，需换算成横轴槽位
  // 有加权计划时只标加权买点（planMarks），不再把原始信号全部铺在图上
  const markSource = Array.isArray(planMarks) ? planMarks : points;
  const tData = markSource
    .map((p) => {
      const si = slotIndex.get(barTimes[p.index]);
      if (si === undefined) return null;
      return {
        value: [si, p.price, p.mark || (p.type === "buy" ? "T买" : "T卖"), p.reason || "", p.strategy || ""],
        label: { position: p.type === "buy" ? "bottom" : "top" },
        itemStyle: {
          color: p.type === "buy" ? GOLD : BLUE,
          borderColor: "#0a0e15",
          borderWidth: 1,
        },
      };
    })
    .filter(Boolean);

  // 以昨收为界拆分分时线：水上红、水下绿，并在穿越点补点避免断线（空槽位跳过）
  const above = [];
  const below = [];
  closeLine.forEach((c, i) => {
    if (c == null) { above.push(null); below.push(null); return; }
    const up = prevClose ? c >= prevClose : true;
    above.push(up ? c : null);
    below.push(up ? null : c);
    if (i > 0 && prevClose) {
      let j = i - 1;
      while (j >= 0 && closeLine[j] == null) j -= 1;
      if (j >= 0 && (closeLine[j] >= prevClose) !== up) {
        above[j] = closeLine[j];
        below[j] = closeLine[j];
      }
    }
  });

  return {
    animation: false,
    backgroundColor: "transparent",
    textStyle: { fontFamily: "Inter, PingFang SC, Microsoft YaHei" },
    legend: {
      top: 0, left: 4, itemWidth: 10, itemHeight: 6, itemGap: 12,
      textStyle: { color: "#a3adc0", fontSize: 10 },
      data: ["价格", ...(hasVwap ? ["均价线"] : []), ...(tData.length ? ["做T点位"] : [])],
    },
    tooltip: {
      trigger: "axis",
      axisPointer: axisPointer(),
      backgroundColor: "rgba(12,16,24,.96)",
      borderColor: "rgba(255,255,255,.10)",
      padding: [8, 10],
      formatter: (params) => {
        if (!params || !params.length) return "";
        const slot = params[0].dataIndex;
        const bi = slotToBar.get(slot);
        if (bi === undefined) return "";  // 空槽位（未交易时段）不弹提示
        const i = bi;
        const b = bars[i] || {};
        const c = b.close;
        const p = prevClose ? ((c / prevClose - 1) * 100) : 0;
        const v = vwap[i];
        return `<div style="font-size:11px;color:#a3adc0;margin-bottom:5px">${b.time || ""}</div>
          <div style="font-size:11.5px;display:grid;grid-template-columns:auto auto;gap:2px 16px">
            <span style="color:#6c7789">价</span><span style="color:${p >= 0 ? UP : DOWN}">${c}</span>
            <span style="color:#6c7789">涨跌</span><span style="color:${p >= 0 ? UP : DOWN}">${p >= 0 ? "+" : ""}${p.toFixed(2)}%</span>
            <span style="color:#6c7789">均价</span><span>${v == null ? "-" : Number(v).toFixed(2)}</span>
            <span style="color:#6c7789">量</span><span>${b.volume ? (b.volume / 1e4).toFixed(1) + "万" : "-"}</span>
          </div>`;
      },
    },
    grid: [
      { left: 52, right: 46, top: 24, height: "50%" },
      { left: 52, right: 46, top: "62%", height: "12%" },
      { left: 52, right: 46, top: "78%", height: "15%" },
    ],
    xAxis: [
      { ...AXIS, type: "category", data: slots, gridIndex: 0, boundaryGap: false, axisLabel: { show: false }, splitLine: { show: false } },
      { ...AXIS, type: "category", data: slots, gridIndex: 1, boundaryGap: true, axisLabel: { show: false }, splitLine: { show: false } },
      {
        ...AXIS, type: "category", data: slots, gridIndex: 2, boundaryGap: true, splitLine: { show: false },
        axisLabel: {
          color: "#6c7789", fontSize: 10,
          interval: (index, value) => labelSet.has(value),
          formatter: (value) => labelText(value),
        },
      },
    ],
    yAxis: [
      {
        ...AXIS, gridIndex: 0, scale: false, splitNumber: 4,
        min: prevClose ? prevClose * (1 - limitPct / 100) : undefined,
        max: prevClose ? prevClose * (1 + limitPct / 100) : undefined,
        axisLabel: { ...AXIS_LABEL, formatter: (v) => Number(v).toFixed(2) },
      },
      {
        ...AXIS, gridIndex: 1, splitNumber: 2, splitLine: { show: false },
        axisLabel: { ...AXIS_LABEL, formatter: (v) => (v >= 1e8 ? `${(v / 1e8).toFixed(0)}亿` : v >= 1e4 ? `${(v / 1e4).toFixed(0)}万` : v) },
      },
      { ...AXIS, gridIndex: 2, splitNumber: 2, splitLine: { show: false }, axisLabel: { ...AXIS_LABEL, formatter: (v) => Number(v).toFixed(1) } },
      {
        ...AXIS, gridIndex: 0, position: "right", splitNumber: 4,
        min: -limitPct, max: limitPct,
        axisLine: { show: false }, splitLine: { show: false },
        axisLabel: { ...AXIS_LABEL, formatter: (v) => `${v > 0 ? "+" : ""}${Number(v).toFixed(2)}%` },
      },
    ],
    series: [
      {
        type: "line", name: "价格", data: above, showSymbol: false,
        lineStyle: { color: UP, width: 1.6 },
        areaStyle: {
          color: {
            type: "linear", x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: "rgba(255,80,99,.18)" },
              { offset: 1, color: "rgba(0,0,0,0)" },
            ],
          },
        },
        markLine: {
          silent: true, symbol: "none", animation: false,
          lineStyle: { color: "rgba(242,184,69,.55)", type: "dashed", width: 1 },
          label: { color: "rgba(242,184,69,.9)", fontSize: 9, position: "insideEndTop", formatter: "昨收" },
          data: prevClose ? [{ yAxis: prevClose }] : [],
        },
      },
      {
        type: "line", name: "水下价格", data: below, showSymbol: false,
        lineStyle: { color: DOWN, width: 1.6 },
        areaStyle: {
          color: {
            type: "linear", x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: "rgba(20,195,154,.18)" },
              { offset: 1, color: "rgba(0,0,0,0)" },
            ],
          },
        },
      },
      ...(hasVwap ? [{
        type: "line", name: "均价线", data: vwapLine, showSymbol: false,
        lineStyle: { color: GOLD, width: 1.1, type: "solid" },
      }] : []),
      ...(planLines && planLines.length ? [{
        type: "line", name: "预估T出", data: [], showSymbol: false,
        markLine: {
          silent: true, symbol: "none", animation: false,
          lineStyle: { color: BLUE, type: "dashed", width: 1.1, opacity: 0.9 },
          label: {
            color: BLUE, fontSize: 9, position: "insideEndTop",
            formatter: (p) => (p && p.name) || "",
          },
          data: planLines.map((l) => ({ yAxis: l.price, name: l.label })),
        },
        z: 7,
      }] : []),
      ...(points.length ? [{
        type: "scatter", name: "做T点位", data: tData, symbolSize: 9,
        label: {
          show: true, color: "#dfe5ef", fontSize: 10, fontWeight: "600",
          backgroundColor: "rgba(10,14,21,.86)", borderColor: "rgba(255,255,255,.16)",
          borderWidth: 1, borderRadius: 4, padding: [2, 4],
          formatter: (p) => (p.value ? p.value[2] : ""),
        },
        tooltip: {
          formatter: (p) => {
            const v = p.value || [];
            return `${v[2] || ""}${v[4] ? ` · ${v[4]}` : ""}<br/><span style="color:#a3adc0;font-size:10.5px">${v[3] || ""}</span>`;
          },
        },
        z: 8,
      }] : []),
      {
        type: "bar", name: "成交量", xAxisIndex: 1, yAxisIndex: 1, barWidth: "70%",
        data: volumeLine.map((v, si) => {
          const bi = slotToBar.get(si);
          if (v == null || bi === undefined) return null;
          const prev = bi > 0 ? bars[bi - 1].close : (prevClose || bars[bi].open);
          const down = bars[bi].close < prev;
          return { value: v, itemStyle: { color: down ? "rgba(20,195,154,.55)" : "rgba(255,80,99,.55)" } };
        }),
      },
      ...indicatorSeries(series, indicator).map((s) => ({
        ...s, data: place(s.data || []), xAxisIndex: 2, yAxisIndex: 2,
      })),
    ],
  };
}

function tooltipFormatter(upColorFirst = true) {
  return (params) => {
    if (!params || !params.length) return "";
    const candle = params.find((p) => p.seriesType === "candlestick");
    if (!candle) return "";
    const [open, close, low, high] = candle.data.slice(1);
    const pct = ((close - open) / open) * 100;
    const color = close >= open ? UP : DOWN;
    const idx = candle.dataIndex;
    const date = candle.axisValue;
    let html = `<div style="font-size:11px;color:#a3adc0;margin-bottom:6px">${date}</div>`;
    html += `<div style="display:grid;grid-template-columns:auto auto;gap:2px 18px;font-size:11.5px;font-variant-numeric:tabular-nums">
      <span style="color:#6c7789">开</span><span style="color:${color}">${open}</span>
      <span style="color:#6c7789">收</span><span style="color:${color}">${close}</span>
      <span style="color:#6c7789">高</span><span style="color:${color}">${high}</span>
      <span style="color:#6c7789">低</span><span style="color:${color}">${low}</span>
      <span style="color:#6c7789">涨跌</span><span style="color:${color}">${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%</span>
    </div>`;
    const extras = params.filter((p) => p.seriesType === "line" && p.seriesName && !["DIF", "DEA", "K", "D", "J", "RSI(14)"].includes(p.seriesName));
    if (extras.length) {
      html += `<div style="margin-top:6px;border-top:1px dashed rgba(255,255,255,.1);padding-top:5px;font-size:10.5px;color:#a3adc0">`;
      extras.forEach((p) => {
        html += `<div style="display:flex;justify-content:space-between;gap:14px"><span style="color:#6c7789">${p.marker}${p.seriesName}</span><span>${p.value == null ? "-" : Number(p.value).toFixed(2)}</span></div>`;
      });
      html += `</div>`;
    }
    return html;
  };
}

export function klineOption(data, indicator = "MACD", options = {}) {
  const bars = data.bars || [];
  const dates = bars.map((b) => String(b.date).slice(5));
  const series = data.series || {};
  const fib = options.fib || data.fib || [];
  const waves = options.waves || [];
  const overlay = chanOverlay(bars, dates, options.chan || null);
  const visible = Math.min(bars.length, 140);
  const start = bars.length > visible ? ((bars.length - visible) / bars.length) * 100 : 0;

  const candleData = bars.map((b) => [b.open, b.close, b.low, b.high]);
  const volumes = bars.map((b) => ({
    value: b.volume,
    itemStyle: { color: b.close >= b.open ? "rgba(255,80,99,.55)" : "rgba(20,195,154,.55)" },
  }));

  const mainSeries = [
    {
      type: "candlestick",
      name: "K线",
      data: candleData,
      itemStyle: {
        color: UP, color0: DOWN,
        borderColor: UP, borderColor0: DOWN,
        borderWidth: 1,
      },
      markLine: fibLines(fib).length
        ? {
            silent: true, symbol: "none", animation: false,
            lineStyle: { color: "rgba(242,184,69,.42)", type: "dashed", width: 1 },
            label: { color: "rgba(242,184,69,.9)", fontSize: 9, position: "insideEndTop" },
            data: fibLines(fib),
          }
        : undefined,
      markArea: overlay.markArea || undefined,
    },
    { ...bollish(), name: "MA5", data: series.ma5, itemStyle: { color: GOLD }, lineStyle: { color: GOLD, width: 1.1 } },
    { ...bollish(), name: "MA10", data: series.ma10, itemStyle: { color: BLUE }, lineStyle: { color: BLUE, width: 1.1 } },
    { ...bollish(), name: "MA20", data: series.ma20, itemStyle: { color: PURPLE }, lineStyle: { color: PURPLE, width: 1.1 } },
    { ...bollish(), name: "MA60", data: series.ma60, itemStyle: { color: TEAL }, lineStyle: { color: TEAL, width: 1.1 } },
    ...overlay.series,
  ];

  if (waves.length) {
    mainSeries.push({
      type: "scatter",
      name: "波段拐点",
      symbolSize: 7,
      itemStyle: { color: "#0a0e15", borderColor: GOLD, borderWidth: 1.5 },
      label: {
        show: true, position: "top", color: "rgba(242,184,69,.85)", fontSize: 9,
        formatter: (p) => (p.data[2] || "").slice(5),
      },
      data: waves.map((w) => [dates.indexOf(String(w.date).slice(5)), w.price, String(w.date)]),
      z: 6,
    });
  }

  const signals = options.signalOn === false ? [] : (options.signals || []);
  const sigSeries = (list, name, color, edge) => ({
    type: "scatter",
    name,
    symbolSize: (value, params) => {
      const score = params.data && params.data.value ? params.data.value[5] : 0;
      return score >= 65 ? 15 : (score >= 52 ? 12 : 9);
    },
    itemStyle: { color, borderColor: "rgba(255,255,255,.85)", borderWidth: 1 },
    label: {
      show: true, position: "inside", color: "#fff", fontSize: 9, fontWeight: 600,
      formatter: (p) => p.value[2],
    },
    tooltip: {
      formatter: (p) => {
        const v = p.value || [];
        return `${v[2]} · ${v[3]} · ${v[5]}分<br/><span style="color:#a3adc0;font-size:10.5px">${v[4]}</span>`;
      },
    },
    // 弱信号（<52）缩小淡化；强信号（>=65）金色外圈突出
    data: list.map((s) => {
      const weak = s.score < 52;
      const strong = s.score >= 65;
      return {
        value: [
          s.index,
          edge === "below" ? (s.low || s.price) * 0.985 : (s.high || s.price) * 1.015,
          s.side === "buy" ? "买" : "卖", s.type, s.reason || "", s.score,
        ],
        itemStyle: {
          opacity: weak ? 0.4 : 1,
          borderColor: strong ? GOLD : "rgba(255,255,255,.85)",
          borderWidth: strong ? 2 : 1,
          shadowBlur: strong ? 6 : 0,
          shadowColor: strong ? "rgba(242,184,69,.8)" : "transparent",
        },
      };
    }),
    z: 7,
  });
  const buySignals = signals.filter((s) => s.side === "buy");
  const sellSignals = signals.filter((s) => s.side === "sell");
  if (buySignals.length) mainSeries.push(sigSeries(buySignals, "量价买点", UP, "below"));
  if (sellSignals.length) mainSeries.push(sigSeries(sellSignals, "量价卖点", DOWN, "above"));

  return {
    animation: false,
    backgroundColor: "transparent",
    textStyle: { fontFamily: "Inter, PingFang SC, Microsoft YaHei" },
    legend: {
      top: 2, left: 4, itemWidth: 10, itemHeight: 6, itemGap: 12,
      textStyle: { color: "#a3adc0", fontSize: 10 },
      data: ["MA5", "MA10", "MA20", "MA60"],
    },
    tooltip: {
      trigger: "axis",
      axisPointer: axisPointer(),
      backgroundColor: "rgba(12,16,24,.96)",
      borderColor: "rgba(255,255,255,.10)",
      padding: [8, 10],
      formatter: tooltipFormatter(),
    },
    grid: [
      { left: 54, right: 14, top: 26, height: "48%" },
      { left: 54, right: 14, top: "61%", height: "12%" },
      { left: 54, right: 14, top: "77%", height: "16%" },
    ],
    xAxis: [
      { ...AXIS, type: "category", data: dates, gridIndex: 0, boundaryGap: true, axisLabel: { show: false }, splitLine: { show: false }, axisPointer: { z: 3 } },
      { ...AXIS, type: "category", data: dates, gridIndex: 1, boundaryGap: true, axisLabel: { show: false }, splitLine: { show: false } },
      { ...AXIS, type: "category", data: dates, gridIndex: 2, boundaryGap: true, axisLabel: { color: "#6c7789", fontSize: 10 }, splitLine: { show: false } },
    ],
    yAxis: [
      { ...AXIS, scale: true, gridIndex: 0, splitNumber: 4, axisLabel: { ...AXIS_LABEL, formatter: (v) => Number(v).toFixed(2) } },
      {
        ...AXIS, gridIndex: 1, splitNumber: 2, splitLine: { show: false },
        axisLabel: { ...AXIS_LABEL, formatter: (v) => (v >= 1e8 ? `${(v / 1e8).toFixed(0)}亿` : v >= 1e4 ? `${(v / 1e4).toFixed(0)}万` : v) },
      },
      { ...AXIS, gridIndex: 2, splitNumber: 2, splitLine: { show: false }, axisLabel: { ...AXIS_LABEL, formatter: (v) => Number(v).toFixed(1) } },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1, 2], start, end: 100, zoomLock: false },
    ],
    series: [
      ...mainSeries,
      { type: "bar", name: "成交量", data: volumes, xAxisIndex: 1, yAxisIndex: 1, barWidth: "62%" },
      ...indicatorSeries(series, indicator).map((s) => ({ ...s, xAxisIndex: 2, yAxisIndex: 2 })),
    ],
  };
}

export function miniTrendOption(values) {
  const up = (values[values.length - 1] || 0) >= (values[0] || 0);
  return {
    animation: false,
    grid: { left: 0, right: 0, top: 2, bottom: 0 },
    xAxis: { type: "category", show: false, data: values.map((_, i) => i) },
    yAxis: { type: "value", show: false, scale: true },
    series: [{
      type: "line", showSymbol: false, smooth: true, data: values,
      lineStyle: { width: 1.5, color: up ? UP : DOWN },
      areaStyle: {
        opacity: 0.9,
        color: {
          type: "linear", x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: up ? "rgba(255,80,99,.28)" : "rgba(20,195,154,.28)" },
            { offset: 1, color: "rgba(0,0,0,0)" },
          ],
        },
      },
    }],
  };
}

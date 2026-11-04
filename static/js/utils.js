export function fmtPct(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const n = Number(value);
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

export function fmtPrice(value, digits = 2) {
  if (value === null || value === undefined) return "-";
  return Number(value).toFixed(digits);
}

export function fmtAmount(value) {
  if (!value) return "-";
  const n = Number(value);
  if (n >= 1e12) return `${(n / 1e12).toFixed(2)}万亿`;
  if (n >= 1e8) return `${(n / 1e8).toFixed(0)}亿`;
  if (n >= 1e4) return `${(n / 1e4).toFixed(0)}万`;
  return String(Math.round(n));
}

export function fmtFlow(value) {
  if (value === null || value === undefined) return "-";
  const n = Number(value);
  const sign = n > 0 ? "+" : "";
  if (Math.abs(n) >= 1e8) return `${sign}${(n / 1e8).toFixed(2)}亿`;
  if (Math.abs(n) >= 1e4) return `${sign}${(n / 1e4).toFixed(0)}万`;
  return `${sign}${n.toFixed(0)}`;
}

export function fmtNumber(value, digits = 0) {
  if (value === null || value === undefined) return "-";
  return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

export function tone(value) {
  if (value === null || value === undefined) return "flat";
  return value > 0 ? "up" : value < 0 ? "down" : "flat";
}

export function shortName(name, max = 5) {
  if (!name) return "-";
  const trimmed = name.replace(/\s+/g, "");
  return trimmed.length > max ? `${trimmed.slice(0, max)}…` : trimmed;
}

export function boardText(board) {
  if (!board) return "-";
  return board === 1 ? "首板" : `${board}连板`;
}

export function sparkPath(values, width = 96, height = 26, pad = 2) {
  if (!values || values.length < 2) return "";
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = (width - pad * 2) / (values.length - 1);
  return values
    .map((v, i) => {
      const x = pad + i * step;
      const y = height - pad - ((v - min) / span) * (height - pad * 2);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

export function areaPath(values, width = 96, height = 26, pad = 2) {
  const line = sparkPath(values, width, height, pad);
  if (!line) return "";
  return `${line} L${width - pad},${height} L${pad},${height} Z`;
}

// 分时涨跌幅序列 → 0轴对称的红绿分段 SVG path（水上红 / 水下绿，穿越补点防断线）
export function intraPath(pct, width = 96, height = 26, pad = 2) {
  if (!pct || pct.length < 2) return null;
  const max = Math.max(...pct.map((v) => Math.abs(v)), 0.2);
  const step = (width - pad * 2) / (pct.length - 1);
  const yOf = (v) => height / 2 - (v / max) * (height / 2 - pad);
  const pts = pct.map((v, i) => [pad + i * step, yOf(v)]);
  const upSeg = [];
  const downSeg = [];
  pts.forEach(([x, y], i) => {
    const up = pct[i] >= 0;
    const p = `${x.toFixed(1)},${y.toFixed(1)}`;
    upSeg.push(up ? p : null);
    downSeg.push(up ? null : p);
    if (i > 0 && (pct[i - 1] >= 0) !== up) {
      const prev = `${pts[i - 1][0].toFixed(1)},${pts[i - 1][1].toFixed(1)}`;
      upSeg[i - 1] = prev;
      downSeg[i - 1] = prev;
    }
  });
  const toPath = (arr) => {
    let d = "";
    let started = false;
    arr.forEach((p) => {
      if (!p) { started = false; return; }
      d += (started ? "L" : "M") + p + " ";
      started = true;
    });
    return d.trim();
  };
  return { up: toPath(upSeg), down: toPath(downSeg), zero: yOf(0) };
}

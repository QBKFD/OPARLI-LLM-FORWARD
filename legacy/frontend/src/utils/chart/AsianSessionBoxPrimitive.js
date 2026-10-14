/**
 * AsianSessionBoxPrimitive — draws a price-bounded rectangle for each
 * Asian session (00:00–08:00 UTC), spanning low to high on the Y-axis.
 */

class AsianSessionBoxRenderer {
  constructor(source) {
    this._source = source;
  }

  draw(target) {
    const ts     = this._source.chart.timeScale();
    const series = this._source.series;

    // Pre-compute logical coordinates OUTSIDE useBitmapCoordinateSpace
    const ready = [];
    for (const box of this._source._boxes) {
      const x1 = ts.timeToCoordinate(box.startTime);
      const x2 = ts.timeToCoordinate(box.endTime);
      const y1 = series.priceToCoordinate(box.high);
      const y2 = series.priceToCoordinate(box.low);
      if (x1 == null || x2 == null || y1 == null || y2 == null) continue;
      ready.push({ x1, x2, y1, y2 });
    }

    if (ready.length === 0) return;

    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const pxR = scope.horizontalPixelRatio;
      const pyR = scope.verticalPixelRatio;

      for (const r of ready) {
        const bx1 = Math.round(r.x1 * pxR);
        const bx2 = Math.round(r.x2 * pxR);
        const by1 = Math.round(r.y1 * pyR);
        const by2 = Math.round(r.y2 * pyR);
        const bw  = bx2 - bx1;
        const bh  = by2 - by1;
        if (bw === 0 || bh === 0) continue;

        ctx.fillStyle = 'rgba(255, 200, 0, 0.08)';
        ctx.fillRect(bx1, by1, bw, bh);

        ctx.strokeStyle = 'rgba(255, 200, 0, 0.55)';
        ctx.lineWidth = 1;
        ctx.strokeRect(bx1 + 0.5, by1 + 0.5, bw - 1, bh - 1);
      }
    });
  }
}

class AsianSessionBoxPaneView {
  constructor(source) { this._source = source; }
  renderer() { return new AsianSessionBoxRenderer(this._source); }
  zOrder()   { return 'bottom'; }
}

export class AsianSessionBoxPrimitive {
  constructor(chart, series) {
    this.chart      = chart;
    this.series     = series;
    this._boxes     = [];
    this._paneViews = [new AsianSessionBoxPaneView(this)];
  }

  setFromBars(bars) {
    const byDate = {};
    for (const bar of bars) {
      const d    = new Date(bar.time * 1000);
      const hour = d.getUTCHours();
      if (hour >= 8) continue;
      const key  = `${d.getUTCFullYear()}-${d.getUTCMonth()}-${d.getUTCDate()}`;
      if (!byDate[key]) byDate[key] = [];
      byDate[key].push(bar);
    }

    this._boxes = [];
    for (const dayBars of Object.values(byDate)) {
      if (dayBars.length < 2) continue;
      const high      = Math.max(...dayBars.map(b => b.high));
      const low       = Math.min(...dayBars.map(b => b.low));
      const startTime = Math.min(...dayBars.map(b => b.time));
      const endTime   = Math.max(...dayBars.map(b => b.time)) + 3600;
      if (high - low < 0.5) continue;
      this._boxes.push({ startTime, endTime, high, low });
    }
    console.log(`[AsianBox] computed ${this._boxes.length} boxes from ${bars.length} bars`);
  }

  paneViews()      { return this._paneViews; }
  updateAllViews() { this._paneViews.forEach(pv => pv.update?.()); }
  attached(params) { this._requestUpdate = params.requestUpdate; }
  detached()       { this._requestUpdate = null; }
}

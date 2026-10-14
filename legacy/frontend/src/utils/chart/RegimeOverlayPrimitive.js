/**
 * RegimeOverlayPrimitive — draws full-height colored rectangles
 * behind candles to visualize market regime classification.
 *
 * Uses TradingView Lightweight Charts v5 ISeriesPrimitive API.
 */

class RegimeOverlayPaneView {
  constructor(source) {
    this._source = source;
  }

  update() {
    // Called when chart updates — we recalculate in renderer
  }

  renderer() {
    return new RegimeOverlayRenderer(this._source);
  }

  zOrder() {
    return 'bottom';
  }
}

class RegimeOverlayRenderer {
  constructor(source) {
    this._source = source;
  }

  draw(target) {
    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const blocks = this._source.getBlocks();
      const timeScale = this._source.chart.timeScale();

      if (!blocks || blocks.length === 0) return;

      const height = scope.bitmapSize.height;
      const pixelRatio = scope.horizontalPixelRatio;

      for (const block of blocks) {
        // Convert time coordinates to pixel x positions
        const x1 = timeScale.timeToCoordinate(block.startTime);
        const x2 = timeScale.timeToCoordinate(block.endTime);

        if (x1 === null || x2 === null) continue;

        // Convert logical to bitmap coordinates
        const bitmapX1 = Math.round(x1 * pixelRatio);
        const bitmapX2 = Math.round(x2 * pixelRatio);

        // Draw full-height rectangle
        ctx.fillStyle = block.color;
        ctx.fillRect(bitmapX1, 0, bitmapX2 - bitmapX1, height);
      }
    });
  }
}

export class RegimeOverlayPrimitive {
  constructor(chart) {
    this.chart = chart;
    this._blocks = [];
    this._paneViews = [new RegimeOverlayPaneView(this)];
  }

  /**
   * Set regime data and compute contiguous blocks.
   * @param {Array} regimes - [{time, regime, color}]
   */
  setData(regimes) {
    if (!regimes || regimes.length === 0) {
      this._blocks = [];
      return;
    }

    // Merge consecutive bars of the same regime into blocks
    const colorMap = {
      'TRENDING': 'rgba(76, 175, 80, 0.18)',
      'RANGING': 'rgba(33, 150, 243, 0.18)',
      'VOLATILE': 'rgba(244, 67, 54, 0.18)',
      'UNKNOWN': 'rgba(128, 128, 128, 0.06)',
    };

    const blocks = [];
    let currentBlock = null;

    for (const r of regimes) {
      if (!currentBlock || currentBlock.regime !== r.regime) {
        // Start new block
        if (currentBlock) {
          blocks.push(currentBlock);
        }
        currentBlock = {
          regime: r.regime,
          startTime: r.time,
          endTime: r.time,
          color: colorMap[r.regime] || colorMap['UNKNOWN'],
        };
      } else {
        // Extend current block
        currentBlock.endTime = r.time;
      }
    }

    if (currentBlock) {
      blocks.push(currentBlock);
    }

    this._blocks = blocks;
  }

  getBlocks() {
    return this._blocks;
  }

  paneViews() {
    return this._paneViews;
  }

  attached(params) {
    this._requestUpdate = params.requestUpdate;
  }

  detached() {
    this._requestUpdate = null;
  }

  requestUpdate() {
    if (this._requestUpdate) {
      this._requestUpdate();
    }
  }

  updateAllViews() {
    this._paneViews.forEach(pv => pv.update());
  }
}

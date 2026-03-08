/**
 * app.js — Alpine.js application for AI Forex Agent Dashboard v2.0
 *
 * Handles:
 *  - REST API polling (portfolio, system, trades, analyses)
 *  - WebSocket real-time updates (auto-reconnect)
 *  - Chart.js equity curve
 *  - UI state management
 */

function dashboard() {
  return {
    // --- State ---
    portfolio: {
      balance: 10000,
      initial_balance: 10000,
      high_water_mark: 10000,
      daily_start_balance: 10000,
      floating_pnl: 0,
      effective_balance: 10000,
      daily_drawdown_pct: 0,
      total_drawdown_pct: 0,
      max_daily_drawdown: 0.05,
      max_total_drawdown: 0.15,
      is_halted: false,
      halt_reason: '',
      mode: 'demo',
      active_trades: [],
      active_count: 0,
      max_concurrent: 2,
      correlation_status: {},
      challenge_mode: 'none',
      position_sizing_mode: 'risk_percent',
      fixed_lot_size: 0.01,
      drawdown_guard_enabled: true,
      active_revalidation_enabled: true,
      active_revalidation_interval_minutes: 90,
    },
    system: {
      mode: 'demo',
      is_halted: false,
      scheduler_jobs: [],
      api_status: { oanda: false, gemini: false },
      uptime_seconds: 0,
    },
    runtimeConfig: {},
    configForm: {
      challenge_mode: 'none',
      position_sizing_mode: 'risk_percent',
      fixed_lot_size: 0.01,
      drawdown_guard_enabled: true,
      active_revalidation_enabled: true,
      active_revalidation_interval_minutes: 90,
      cent_sl_multiplier: 1.5,
      cent_tp_multiplier: 1.5,
    },
    balanceForm: {
      balance: 10000,
      reset_hwm: true,
      reset_daily_start: true,
      update_initial_balance: true,
    },
    saveMsg: '',
    manualCloseBusy: {},
    stats: {
      total: 0, wins: 0, losses: 0, winrate: 0, total_pips: 0,
    },
    trades: [],
    analyses: [],
    events: [],
    equityPoints: [],

    // UI state
    activeTab: 'positions',
    wsConnected: false,
    clockUTC: '--:--:--',
    clockWIB: '--:--:--',
    showPostMortem: false,
    showAnalysis: false,
    selectedTrade: null,
    selectedAnalysis: null,

    // Internals
    _ws: null,
    _wsRetryDelay: 1000,
    _equityChart: null,
    _pollInterval: null,

    // --- Init ---
    async init() {
      // Fetch initial data
      await Promise.all([
        this.fetchPortfolio(),
        this.fetchSystem(),
        this.fetchSystemConfig(),
        this.fetchTrades(),
        this.fetchAnalyses(),
        this.fetchEquity(),
        this.fetchEvents(),
      ]);

      // Start WebSocket
      this.connectWs();

      // Start clock
      this.updateClock();
      setInterval(() => this.updateClock(), 1000);

      // Polling: refresh every 30s
      this._pollInterval = setInterval(() => {
        this.fetchPortfolio();
        this.fetchSystem();
        this.fetchSystemConfig();
        this.fetchTrades();
        this.fetchAnalyses();
        this.fetchEquity();
      }, 30000);

      // Init chart after DOM ready
      this.$nextTick(() => {
        this.initEquityChart();
        // Re-init Lucide icons after Alpine renders
        if (window.lucide) lucide.createIcons();
      });
    },

    // --- REST API ---
    async fetchPortfolio() {
      try {
        const r = await fetch('/api/portfolio');
        if (r.ok) {
          const data = await r.json();
          // Preserve _expanded flags on active trades
          if (data.active_trades) {
            data.active_trades.forEach(t => { t._expanded = false; });
          }
          this.portfolio = data;
          if (data.runtime_config) {
            this.runtimeConfig = data.runtime_config;
            this.syncConfigForms();
          }
        }
      } catch (e) { console.warn('Portfolio fetch failed:', e); }
    },

    async fetchSystem() {
      try {
        const r = await fetch('/api/system/status');
        if (r.ok) {
          const data = await r.json();
          this.system = data;
          if (data.runtime_config) {
            this.runtimeConfig = data.runtime_config;
            this.syncConfigForms();
          }
        }
      } catch (e) { console.warn('System fetch failed:', e); }
    },

    syncConfigForms() {
      const c = this.runtimeConfig || {};
      this.configForm.challenge_mode = c.challenge_mode || 'none';
      this.configForm.position_sizing_mode = c.position_sizing_mode || 'risk_percent';
      this.configForm.fixed_lot_size = c.fixed_lot_size ?? 0.01;
      this.configForm.drawdown_guard_enabled = c.drawdown_guard_enabled ?? true;
      this.configForm.active_revalidation_enabled = c.active_revalidation_enabled ?? true;
      this.configForm.active_revalidation_interval_minutes = c.active_revalidation_interval_minutes ?? 90;
      this.configForm.cent_sl_multiplier = c.cent_sl_multiplier ?? 1.5;
      this.configForm.cent_tp_multiplier = c.cent_tp_multiplier ?? 1.5;
      this.balanceForm.balance = c.balance ?? this.portfolio.balance ?? 10000;
    },

    async fetchSystemConfig() {
      try {
        const r = await fetch('/api/system/config');
        if (!r.ok) return;
        const data = await r.json();
        if (!data.success) return;
        this.runtimeConfig = data.config || {};
        this.syncConfigForms();
      } catch (e) { console.warn('System config fetch failed:', e); }
    },

    async saveSystemConfig() {
      this.saveMsg = '';
      try {
        const payload = {
          challenge_mode: this.configForm.challenge_mode,
          position_sizing_mode: this.configForm.position_sizing_mode,
          fixed_lot_size: Number(this.configForm.fixed_lot_size),
          drawdown_guard_enabled: !!this.configForm.drawdown_guard_enabled,
          active_revalidation_enabled: !!this.configForm.active_revalidation_enabled,
          active_revalidation_interval_minutes: Number(this.configForm.active_revalidation_interval_minutes),
          cent_sl_multiplier: Number(this.configForm.cent_sl_multiplier),
          cent_tp_multiplier: Number(this.configForm.cent_tp_multiplier),
        };
        const r = await fetch('/api/system/config', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await r.json();
        if (!r.ok || !data.success) throw new Error(data.error || 'Failed to save config');
        this.runtimeConfig = data.config || {};
        this.syncConfigForms();
        this.fetchPortfolio();
        this.fetchEquity();
        this.saveMsg = 'Config updated';
      } catch (e) {
        this.saveMsg = 'Config update failed';
        console.error('Save config failed:', e);
      }
    },

    async setCustomBalance() {
      this.saveMsg = '';
      try {
        const payload = {
          balance: Number(this.balanceForm.balance),
          reset_hwm: !!this.balanceForm.reset_hwm,
          reset_daily_start: !!this.balanceForm.reset_daily_start,
          update_initial_balance: !!this.balanceForm.update_initial_balance,
        };
        const r = await fetch('/api/system/balance/set', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await r.json();
        if (!r.ok || !data.success) throw new Error(data.error || 'Failed to set balance');
        this.runtimeConfig = data.config || {};
        this.syncConfigForms();
        await this.fetchPortfolio();
        await this.fetchEquity();
        this.saveMsg = 'Balance updated';
      } catch (e) {
        this.saveMsg = 'Balance update failed';
        console.error('Set balance failed:', e);
      }
    },

    async resetSystemDefaults() {
      this.saveMsg = '';
      try {
        const r = await fetch('/api/system/config/reset-default', { method: 'POST' });
        const data = await r.json();
        if (!r.ok || !data.success) throw new Error(data.error || 'Reset failed');
        this.runtimeConfig = data.config || {};
        this.syncConfigForms();
        await this.fetchPortfolio();
        await this.fetchEquity();
        this.saveMsg = 'Defaults restored';
      } catch (e) {
        this.saveMsg = 'Reset failed';
        console.error('Reset config failed:', e);
      }
    },

    async fetchTrades() {
      try {
        const r = await fetch('/api/trades?limit=20');
        if (r.ok) this.trades = await r.json();
      } catch (e) { console.warn('Trades fetch failed:', e); }
    },

    async fetchAnalyses() {
      try {
        const r = await fetch('/api/analysis/live');
        if (r.ok) this.analyses = await r.json();
      } catch (e) { console.warn('Analyses fetch failed:', e); }
    },

    async fetchEquity() {
      try {
        const r = await fetch('/api/portfolio/equity');
        if (r.ok) {
          const data = await r.json();
          this.equityPoints = data.points || [];
          this.updateEquityChart();
        }
      } catch (e) { console.warn('Equity fetch failed:', e); }
    },

    async fetchEvents() {
      try {
        const r = await fetch('/api/events?limit=100');
        if (r.ok) {
          const data = await r.json();
          if (data.length) this.events = data;
        }
      } catch (e) { console.warn('Events fetch failed:', e); }
    },

    async unhaltSystem() {
      try {
        const r = await fetch('/api/system/unhalt', { method: 'POST' });
        if (r.ok) {
          const data = await r.json();
          if (data.success) {
            this.portfolio.is_halted = false;
            this.portfolio.halt_reason = '';
            this.addEvent('SYSTEM', { pair: '—' }, data.message);
          }
        }
      } catch (e) { console.error('Unhalt failed:', e); }
    },

    async manualCloseTrade(trade) {
      if (!trade || !trade.trade_id) return;
      const pair = trade.pair || '';
      const direction = (trade.direction || '').toUpperCase();
      const ok = window.confirm(`Close ${pair} ${direction} manually?`);
      if (!ok) return;

      const tradeId = trade.trade_id;
      this.manualCloseBusy[tradeId] = true;
      this.saveMsg = '';
      try {
        const payload = {
          reason: `Manual close via dashboard (${new Date().toISOString()})`,
        };
        const r = await fetch(`/api/positions/${encodeURIComponent(tradeId)}/close`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await r.json();
        if (!r.ok || !data.success) {
          throw new Error(data.error || 'Manual close failed');
        }
        this.saveMsg = `Manual close executed: ${pair}`;
        await this.fetchPortfolio();
        await this.fetchTrades();
        await this.fetchEquity();
      } catch (e) {
        this.saveMsg = `Manual close failed: ${pair}`;
        console.error('Manual close failed:', e);
      } finally {
        delete this.manualCloseBusy[tradeId];
      }
    },

    isManualClosing(tradeId) {
      return !!this.manualCloseBusy[tradeId];
    },

    // --- WebSocket ---
    connectWs() {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const url = `${proto}//${location.host}/ws`;
      this._ws = new WebSocket(url);

      this._ws.onopen = () => {
        this.wsConnected = true;
        this._wsRetryDelay = 1000;
        // Keepalive ping
        this._wsPing = setInterval(() => {
          try { this._ws.send('ping'); } catch (e) {}
        }, 15000);
      };

      this._ws.onclose = () => {
        this.wsConnected = false;
        clearInterval(this._wsPing);
        // Exponential backoff reconnect
        setTimeout(() => this.connectWs(), this._wsRetryDelay);
        this._wsRetryDelay = Math.min(this._wsRetryDelay * 1.5, 30000);
      };

      this._ws.onerror = () => {};

      this._ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          this.handleWsMessage(msg);
        } catch (e) {}
      };
    },

    handleWsMessage(msg) {
      const { type, data } = msg;

      if (type === 'ANALYSIS_UPDATE') {
        const pair = (data.pair || '').toUpperCase();
        if (pair) {
          // Update or add analysis
          const idx = this.analyses.findIndex(a => (a.pair || '').toUpperCase() === pair);
          if (idx >= 0) {
            this.analyses[idx] = data;
          } else {
            this.analyses.push(data);
          }
        }
        this.addEvent(type, data, this.getWsSummary(type, data));
      }
      else if (type === 'STATE_CHANGE') {
        this.addEvent(type, data, `${data.old_state} → ${data.new_state}`);
      }
      else if (type === 'TRADE_CLOSED') {
        this.trades.unshift(data);
        if (this.trades.length > 50) this.trades.pop();
        this.addEvent(type, data,
          `${data.result} ${(data.pips || 0).toFixed(1)} pips $${(data.pnl || 0).toFixed(2)}`);
        // Refresh portfolio
        this.fetchPortfolio();
        this.fetchEquity();
      }
      else if (type === 'PORTFOLIO_UPDATE') {
        // Merge portfolio-level fields
        this.portfolio.balance = data.balance ?? this.portfolio.balance;
        this.portfolio.floating_pnl = data.floating_pnl ?? this.portfolio.floating_pnl;
        this.portfolio.active_count = data.active_count ?? this.portfolio.active_count;
        this.portfolio.is_halted = data.is_halted ?? this.portfolio.is_halted;
        // Merge per-trade floating data into active_trades
        if (data.trade_floats && this.portfolio.active_trades) {
          for (const tf of data.trade_floats) {
            const t = this.portfolio.active_trades.find(t => t.pair === tf.pair);
            if (t) {
              t.current_price = tf.current_price;
              t.floating_pips = tf.floating_pips;
              t.floating_dollar = tf.floating_dollar;
              t.rr_current = tf.rr_current;
            }
          }
        }
        this.fetchEquity();
      }

      // Re-render icons after template changes
      this.$nextTick(() => { if (window.lucide) lucide.createIcons(); });
    },

    getWsSummary(type, data) {
      if (type === 'ANALYSIS_UPDATE') {
        const s = data.plan?.primary_setup;
        return s ? `${s.direction} score=${s.confluence_score}` : (data.error || 'no plan');
      }
      return '';
    },

    addEvent(type, data, summary) {
      const now = new Date();
      this.events.unshift({
        time: now.toLocaleTimeString('en-GB', { hour12: false }),
        type: type,
        pair: data?.pair || '—',
        summary: summary || '',
      });
      // Cap at 100
      if (this.events.length > 100) this.events.pop();
    },

    // --- Chart.js Equity Curve ---
    initEquityChart() {
      const canvas = document.getElementById('equityChart');
      if (!canvas) return;
      const ctx = canvas.getContext('2d');

      // Gradient fill
      const gradient = ctx.createLinearGradient(0, 0, 0, 200);
      gradient.addColorStop(0, 'rgba(8, 145, 178, 0.15)');
      gradient.addColorStop(1, 'rgba(8, 145, 178, 0.01)');

      this._equityChart = new Chart(ctx, {
        type: 'line',
        data: {
          labels: [],
          datasets: [
            {
              label: 'Balance',
              data: [],
              borderColor: '#0891B2',
              backgroundColor: gradient,
              borderWidth: 2,
              fill: true,
              tension: 0.3,
              pointRadius: 3,
              pointBackgroundColor: '#0891B2',
            },
            {
              label: 'HWM',
              data: [],
              borderColor: '#A5F3FC',
              borderWidth: 1,
              borderDash: [5, 4],
              fill: false,
              tension: 0,
              pointRadius: 0,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { intersect: false, mode: 'index' },
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: (ctx) => `${ctx.dataset.label}: $${ctx.parsed.y.toFixed(2)}`
              }
            },
          },
          scales: {
            x: {
              grid: { display: false },
              ticks: { font: { size: 10, family: 'Inter' }, color: '#155E75' },
            },
            y: {
              grid: { color: '#ECFEFF' },
              ticks: {
                font: { size: 10, family: 'Inter' },
                color: '#155E75',
                callback: (val) => '$' + val.toLocaleString(),
              },
            },
          },
        },
      });

      this.updateEquityChart();
    },

    updateEquityChart() {
      if (!this._equityChart || !this.equityPoints.length) return;
      const points = [...this.equityPoints].sort((a, b) => {
        const ta = new Date(a.timestamp || a.date || 0).getTime();
        const tb = new Date(b.timestamp || b.date || 0).getTime();
        return ta - tb;
      });
      this._equityChart.data.labels = points.map(p => p.label || p.timestamp || p.date);
      this._equityChart.data.datasets[0].data = points.map(p => p.balance);
      this._equityChart.data.datasets[1].data = points.map(p => p.hwm);
      this._equityChart.update('none');
    },

    // --- Clock ---
    updateClock() {
      const now = new Date();
      this.clockUTC = now.toLocaleTimeString('en-GB', { hour12: false, timeZone: 'UTC' });
      this.clockWIB = now.toLocaleTimeString('en-GB', { hour12: false, timeZone: 'Asia/Jakarta' });
    },

    // --- Helpers ---
    fmtNum(n) {
      if (n === undefined || n === null) return '0.00';
      return Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    },

    fmtPct(n) {
      if (n === undefined || n === null) return '0.0%';
      return (Number(n) * 100).toFixed(1) + '%';
    },

    fmtPrice(price, pair) {
      if (price === undefined || price === null) return '—';
      const p = pair || '';
      if (p.includes('JPY') || p.includes('XAU')) {
        return Number(price).toFixed(p.includes('XAU') ? 2 : 3);
      }
      return Number(price).toFixed(5);
    },

    fmtTime(iso) {
      if (!iso) return '—';
      try {
        return new Date(iso).toLocaleString('en-GB', {
          hour12: false, month: 'short', day: 'numeric',
          hour: '2-digit', minute: '2-digit',
        });
      } catch (e) { return iso; }
    },

    activePairNames() {
      const trades = this.portfolio.active_trades || [];
      if (trades.length === 0) return 'No positions';
      return trades.map(t => t.pair).join(', ');
    },

    challengeModeLabel(mode) {
      const m = (mode || 'none').toLowerCase();
      if (m === 'challenge_extreme') return 'Extreme';
      if (m === 'challenge_cent') return 'Cent';
      return 'Off';
    },

    // Drawdown colors
    ddColor(current, max) {
      const ratio = max > 0 ? current / max : 0;
      if (ratio >= 0.8) return 'text-danger';
      if (ratio >= 0.5) return 'text-warning';
      return 'text-success';
    },
    ddBarColor(current, max) {
      const ratio = max > 0 ? current / max : 0;
      if (ratio >= 0.8) return 'bg-danger';
      if (ratio >= 0.5) return 'bg-warning';
      return 'bg-success';
    },
    ddBarWidth(current, max) {
      if (!max || max <= 0) return 0;
      return Math.min(100, (current / max) * 100);
    },

    // Score helpers
    scoreColor(score) {
      if (score >= 10) return 'text-success';
      if (score >= 5) return 'text-warning';
      return 'text-danger';
    },
    scoreBarColor(score) {
      if (score >= 10) return 'bg-success';
      if (score >= 5) return 'bg-warning';
      return 'bg-danger';
    },

    // Event color
    eventColor(type) {
      const map = {
        'ANALYSIS_UPDATE': 'text-secondary',
        'STATE_CHANGE': 'text-warning',
        'TRADE_CLOSED': 'text-success',
        'PORTFOLIO_UPDATE': 'text-primary',
        'SYSTEM': 'text-text-primary',
      };
      return map[type] || 'text-text-muted';
    },

    // Analysis helpers
    getScore(a) {
      return a.plan?.primary_setup?.confluence_score || 0;
    },
    getDir(a) {
      const dir = a.plan?.primary_setup?.direction || a.direction || '—';
      return dir.toUpperCase();
    },
    dirBadgeClass(a) {
      const dir = this.getDir(a);
      if (dir === 'BUY') return 'bg-success/10 text-success';
      if (dir === 'SELL') return 'bg-danger/10 text-danger';
      return 'bg-gray-100 text-text-muted';
    },
    getRadarSummary(a) {
      if (a.error) return a.error;
      const s = a.plan?.primary_setup;
      if (s) {
        const rec = s.recommended_entry ? `  |  📌 Rec: ${s.recommended_entry.toFixed(5)}` : '';
        return `Entry: ${s.entry_zone_low?.toFixed(5) || '—'} – ${s.entry_zone_high?.toFixed(5) || '—'}${rec}  |  SL: ${s.stop_loss?.toFixed(5) || '—'}  |  ${s.strategy_mode || ''}`;
      }
      return 'No setup found';
    },
    sortedAnalyses() {
      return [...this.analyses].sort((a, b) => this.getScore(b) - this.getScore(a));
    },

    // Trade result badge
    resultBadge(result) {
      if (!result) return 'bg-gray-100 text-text-muted';
      if (result.includes('TP')) return 'bg-success/10 text-success';
      if (result === 'SL_HIT') return 'bg-danger/10 text-danger';
      if (result === 'BE_HIT') return 'bg-primary/10 text-primary';
      return 'bg-gray-100 text-text-muted';
    },

    // Scheduler helpers
    defaultJobs() {
      return [
        { name: 'Asian Session', id: 'asian', next_run: null },
        { name: 'London Session', id: 'london', next_run: null },
        { name: 'Pre-NY Session', id: 'preny', next_run: null },
        { name: 'Daily Wrap-Up', id: 'wrapup', next_run: null },
      ];
    },
    jobPassed(job) {
      if (!job.next_run) return false;
      try {
        return new Date(job.next_run) > new Date();
      } catch (e) { return false; }
    },
    fmtJobTime(job) {
      if (!job.next_run) return '—';
      try {
        return new Date(job.next_run).toLocaleTimeString('en-GB', {
          hour12: false, hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Jakarta',
        }) + ' WIB';
      } catch (e) { return '—'; }
    },

    // Modals
    showPostMortemModal(trade) {
      this.selectedTrade = trade;
      // If no post_mortem loaded yet, try fetching from API
      if (!trade.post_mortem && trade.trade_id) {
        fetch(`/api/trades/${trade.trade_id}`)
          .then(r => r.json())
          .then(data => {
            if (data.post_mortem) {
              trade.post_mortem = data.post_mortem;
              this.selectedTrade = { ...trade };
            }
          })
          .catch(() => {});
      }
      this.showPostMortem = true;
    },

    showAnalysisModal(analysis) {
      this.selectedAnalysis = analysis;
      this.showAnalysis = true;
    },
  };
}

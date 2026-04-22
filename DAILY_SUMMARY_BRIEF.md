# Daily Summary Feature — Implementation Brief

## Overview
Add a new "Daily Summary" tab to the Pulsation page showing:
1. Aggregated day-wise data table (all domains combined)
2. Two trend charts with Daily/Weekly/Monthly toggle
3. classdojo.com exclusion checkbox on delivery rate chart

---

## STEP 1 — BACKUP FIRST (before any changes)

```bash
cp /Users/pankaj/pani/blueshift_observatory/frontend/index.html \
   /Users/pankaj/pani/blueshift_observatory/frontend/index.html.bak_daily_summary

cp /Users/pankaj/pani/blueshift_observatory/backend/pulsation_service.py \
   /Users/pankaj/pani/blueshift_observatory/backend/pulsation_service.py.bak_daily_summary

cp /Users/pankaj/pani/blueshift_observatory/backend/app.py \
   /Users/pankaj/pani/blueshift_observatory/backend/app.py.bak_daily_summary
```

---

## STEP 2 — Backend: pulsation_service.py

Add this new function at the bottom (before the last `if __name__` block):

```python
def get_daily_summary(mode: str = 'daily') -> dict:
    """
    Get aggregated pulsation data grouped by day/week/month.
    mode: 'daily' | 'weekly' | 'monthly'
    Returns two datasets:
      - 'all': includes classdojo.com
      - 'excluding_classdojo': excludes classdojo.com
    """
    import sqlite3

    DB_PATH = '/Users/pankaj/pani/data/deliverability_history.db'

    if mode == 'weekly':
        date_expr = "strftime('%Y-W%W', report_date)"
        label_expr = "strftime('%Y-W%W', report_date)"
    elif mode == 'monthly':
        date_expr = "strftime('%Y-%m', report_date)"
        label_expr = "strftime('%Y-%m', report_date)"
    else:  # daily
        date_expr = "report_date"
        label_expr = "report_date"

    def run_query(conn, exclude_classdojo=False):
        where = "WHERE from_domain != 'classdojo.com'" if exclude_classdojo else ""
        sql = f"""
            SELECT
                {label_expr} as period,
                SUM(sent) as total_sent,
                SUM(delivered) as total_delivered,
                SUM(bounces) as total_bounces,
                SUM(spam_report) as total_spam,
                SUM(unsubscribe) as total_unsub,
                ROUND(SUM(delivered) * 100.0 / NULLIF(SUM(sent), 0), 2) as delivery_rate,
                ROUND(SUM(bounces) * 100.0 / NULLIF(SUM(sent), 0), 2) as bounce_rate,
                ROUND(SUM(spam_report) * 100.0 / NULLIF(SUM(sent), 0), 3) as spam_rate
            FROM daily_metrics
            {where}
            GROUP BY {date_expr}
            ORDER BY {date_expr} ASC
        """
        cursor = conn.cursor()
        cursor.execute(sql)
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    conn = sqlite3.connect(DB_PATH)
    all_data = run_query(conn, exclude_classdojo=False)
    excl_data = run_query(conn, exclude_classdojo=True)
    conn.close()

    return {
        'mode': mode,
        'all': all_data,
        'excluding_classdojo': excl_data
    }
```

---

## STEP 3 — Backend: app.py

### 3a. Add import at top (in the `from pulsation_service import (...)` block):
```python
get_daily_summary,
```

### 3b. Add new endpoint (after the existing pulsation endpoints, around line with `/api/pulsation/available-dates`):
```python
@app.get('/api/pulsation/daily-summary')
async def get_pulsation_daily_summary(mode: str = 'daily'):
    """
    Get aggregated pulsation data grouped by day/week/month.
    Query params:
        mode: 'daily' | 'weekly' | 'monthly'
    Returns both all-domains and classdojo-excluded datasets.
    """
    try:
        if mode not in ['daily', 'weekly', 'monthly']:
            raise HTTPException(status_code=400, detail='Invalid mode. Must be daily, weekly, or monthly')
        result = get_daily_summary(mode)
        return {
            'status': 'success',
            **result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching daily summary: {str(e)}')
```

---

## STEP 4 — Frontend: index.html

### 4a. Vue data() — add these new variables inside `data()` return object:

```javascript
// Daily Summary
dailySummaryTab: 'daily',       // 'daily' | 'weekly' | 'monthly'
dailySummaryData: null,
dailySummaryLoading: false,
excludeClassdojo: true,         // default: classdojo excluded
dailySummaryCharts: {
    sent: null,
    deliveryRate: null
},
```

### 4b. Vue methods — add these new methods:

```javascript
async loadDailySummary() {
    this.dailySummaryLoading = true;
    try {
        const response = await fetch(`${this.API_BASE}/api/pulsation/daily-summary?mode=${this.dailySummaryTab}`);
        const result = await response.json();
        if (result.status === 'success') {
            this.dailySummaryData = result;
            this.$nextTick(() => {
                this.renderDailySummaryCharts();
            });
        }
    } catch (err) {
        this.showToast('error', 'Load Error', err.message);
    } finally {
        this.dailySummaryLoading = false;
    }
},

switchDailySummaryMode(mode) {
    this.dailySummaryTab = mode;
    this.loadDailySummary();
},

toggleClassdojo() {
    this.renderDailySummaryCharts();
},

renderDailySummaryCharts() {
    if (!this.dailySummaryData) return;

    const data = this.dailySummaryData.all;
    const dataExcl = this.dailySummaryData.excluding_classdojo;

    // Destroy existing
    if (this.dailySummaryCharts.sent) this.dailySummaryCharts.sent.destroy();
    if (this.dailySummaryCharts.deliveryRate) this.dailySummaryCharts.deliveryRate.destroy();

    const labels = data.map(d => d.period);

    // Chart 1 — Total Sent
    const sentCtx = document.getElementById('dailySentChart');
    if (sentCtx) {
        this.dailySummaryCharts.sent = new Chart(sentCtx, {
            type: 'line',
            data: {
                labels,
                datasets: [
                    {
                        label: 'Total Sent',
                        data: data.map(d => d.total_sent),
                        borderColor: '#3b82f6',
                        backgroundColor: 'rgba(59,130,246,0.08)',
                        tension: 0.4,
                        fill: true,
                        borderWidth: 3,
                        pointRadius: 3
                    },
                    {
                        label: 'Total Delivered',
                        data: data.map(d => d.total_delivered),
                        borderColor: '#10b981',
                        backgroundColor: 'rgba(16,185,129,0.05)',
                        tension: 0.4,
                        fill: false,
                        borderWidth: 2,
                        pointRadius: 0
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: '#1e293b',
                        padding: 12,
                        mode: 'index',
                        intersect: false,
                        callbacks: {
                            label: ctx => {
                                const v = ctx.parsed.y;
                                return ctx.dataset.label + ': ' + (v >= 1000000 ? (v/1000000).toFixed(1)+'M' : v >= 1000 ? (v/1000).toFixed(0)+'K' : v);
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        ticks: {
                            color: '#64748b',
                            callback: v => v >= 1000000 ? (v/1000000).toFixed(1)+'M' : v >= 1000 ? (v/1000).toFixed(0)+'K' : v
                        },
                        grid: { color: '#e2e8f0' }
                    },
                    x: {
                        ticks: { color: '#64748b', maxRotation: 45, autoSkip: true, maxTicksLimit: 15 },
                        grid: { display: false }
                    }
                }
            }
        });
    }

    // Chart 2 — Delivery Rate (with/without classdojo)
    const activeData = this.excludeClassdojo ? dataExcl : data;
    const delivCtx = document.getElementById('dailyDeliveryChart');
    if (delivCtx) {
        this.dailySummaryCharts.deliveryRate = new Chart(delivCtx, {
            type: 'line',
            data: {
                labels: activeData.map(d => d.period),
                datasets: [{
                    label: 'Delivery Rate %',
                    data: activeData.map(d => d.delivery_rate),
                    borderColor: '#8b5cf6',
                    backgroundColor: 'rgba(139,92,246,0.08)',
                    tension: 0.4,
                    fill: true,
                    borderWidth: 3,
                    pointRadius: 3
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: '#1e293b',
                        padding: 12,
                        callbacks: {
                            label: ctx => 'Delivery Rate: ' + ctx.parsed.y + '%'
                        }
                    }
                },
                scales: {
                    y: {
                        min: 80,
                        max: 100,
                        ticks: { color: '#64748b', callback: v => v + '%' },
                        grid: { color: '#e2e8f0' }
                    },
                    x: {
                        ticks: { color: '#64748b', maxRotation: 45, autoSkip: true, maxTicksLimit: 15 },
                        grid: { display: false }
                    }
                }
            }
        });
    }
},
```

### 4c. HTML — Add new tab button in the existing tabs section:

Find this in the tabs section (around the "Trend" tab button):
```html
<button class="tab" :class="{ active: pulsationTab === 'charts' }" @click="pulsationTab = 'charts'">
  Trend
</button>
```

Add AFTER it:
```html
<button class="tab" :class="{ active: pulsationTab === 'daily_summary' }" @click="pulsationTab = 'daily_summary'; loadDailySummary()">
  Daily Summary
</button>
```

### 4d. HTML — Add the Daily Summary tab content section:

Find this section (it's the last tab section, after the charts/trend section):
```html
<!-- Empty State -->
<div v-if="!pulsationData" style="text-align: center; padding: 60px 20px;">
```

Add BEFORE the empty state div:

```html
<!-- Daily Summary Tab -->
<div v-if="pulsationData && pulsationTab === 'daily_summary'">

  <!-- Toggle: Daily / Weekly / Monthly -->
  <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 20px;">
    <span style="font-size: 13px; font-weight: 600; color: #64748b;">View By:</span>
    <div style="display: flex; gap: 4px; padding: 4px; background: #f1f5f9; border-radius: 10px;">
      <button class="btn btn-sm" :class="dailySummaryTab === 'daily' ? 'btn-primary' : 'btn-secondary'" @click="switchDailySummaryMode('daily')">Daily</button>
      <button class="btn btn-sm" :class="dailySummaryTab === 'weekly' ? 'btn-primary' : 'btn-secondary'" @click="switchDailySummaryMode('weekly')">Weekly</button>
      <button class="btn btn-sm" :class="dailySummaryTab === 'monthly' ? 'btn-primary' : 'btn-secondary'" @click="switchDailySummaryMode('monthly')">Monthly</button>
    </div>
    <div v-if="dailySummaryLoading" style="font-size: 13px; color: #64748b;">Loading...</div>
  </div>

  <div v-if="dailySummaryData">

    <!-- Chart 1: Total Sent Volume -->
    <div class="card-section" style="margin-bottom: 20px;">
      <div class="card-header">
        <h3 class="card-title">Total Send Volume</h3>
        <div style="display: flex; gap: 16px; font-size: 12px; color: #64748b;">
          <span style="display: flex; align-items: center; gap: 4px;">
            <span style="width: 10px; height: 10px; border-radius: 2px; background: #3b82f6; display: inline-block;"></span> Total Sent
          </span>
          <span style="display: flex; align-items: center; gap: 4px;">
            <span style="width: 10px; height: 10px; border-radius: 2px; background: #10b981; display: inline-block;"></span> Total Delivered
          </span>
        </div>
      </div>
      <div style="padding: 20px 22px;">
        <div style="position: relative; width: 100%; height: 260px;">
          <canvas id="dailySentChart"></canvas>
        </div>
      </div>
    </div>

    <!-- Chart 2: Delivery Rate -->
    <div class="card-section" style="margin-bottom: 20px;">
      <div class="card-header">
        <h3 class="card-title">Delivery Rate Trend</h3>
        <div style="display: flex; align-items: center; gap: 8px;">
          <label style="display: flex; align-items: center; gap: 6px; font-size: 13px; color: #475569; cursor: pointer;">
            <input
              type="checkbox"
              v-model="excludeClassdojo"
              @change="toggleClassdojo"
              style="width: 15px; height: 15px; cursor: pointer;"
            />
            Exclude classdojo.com
          </label>
          <span style="font-size: 11px; color: #94a3b8;">(0% delivery domain)</span>
        </div>
      </div>
      <div style="padding: 20px 22px;">
        <div style="position: relative; width: 100%; height: 260px;">
          <canvas id="dailyDeliveryChart"></canvas>
        </div>
      </div>
    </div>

    <!-- Table -->
    <div class="card-section">
      <div class="card-header">
        <h3 class="card-title">
          {{ dailySummaryTab === 'daily' ? 'Day-wise' : dailySummaryTab === 'weekly' ? 'Week-wise' : 'Month-wise' }} Summary
        </h3>
        <span class="card-meta">{{ dailySummaryData.all.length }} records</span>
      </div>
      <div class="table-wrapper">
        <table class="data-table">
          <thead>
            <tr>
              <th>{{ dailySummaryTab === 'daily' ? 'Date' : dailySummaryTab === 'weekly' ? 'Week' : 'Month' }}</th>
              <th>Total Sent</th>
              <th>Total Delivered</th>
              <th>Delivery %</th>
              <th>Total Bounces</th>
              <th>Bounce %</th>
              <th>Total Spam</th>
              <th>Spam %</th>
              <th>Total Unsub</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in dailySummaryData.all" :key="row.period">
              <td style="font-weight: 600; color: #1e293b;">{{ row.period }}</td>
              <td class="text-mono">{{ formatNumber(row.total_sent) }}</td>
              <td class="text-mono">{{ formatNumber(row.total_delivered) }}</td>
              <td>
                <span :style="{ color: row.delivery_rate >= 95 ? '#10b981' : row.delivery_rate >= 85 ? '#f59e0b' : '#ef4444', fontWeight: 600 }">
                  {{ row.delivery_rate }}%
                </span>
              </td>
              <td class="text-mono">{{ formatNumber(row.total_bounces) }}</td>
              <td class="text-mono" style="color: #f97316;">{{ row.bounce_rate }}%</td>
              <td class="text-mono">{{ formatNumber(row.total_spam) }}</td>
              <td class="text-mono" style="color: #ef4444;">{{ row.spam_rate }}%</td>
              <td class="text-mono">{{ formatNumber(row.total_unsub) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

  </div>

  <!-- Loading state -->
  <div v-if="dailySummaryLoading && !dailySummaryData" style="text-align: center; padding: 60px 20px;">
    <div style="font-size: 14px; color: #64748b;">Loading daily summary data...</div>
  </div>

  <!-- Empty state -->
  <div v-if="!dailySummaryLoading && !dailySummaryData" style="text-align: center; padding: 60px 20px;">
    <div style="font-size: 48px; margin-bottom: 16px;">📊</div>
    <div style="font-size: 14px; color: #64748b;">Click the Daily Summary tab to load data</div>
  </div>

</div>
```

---

## REVERT INSTRUCTIONS (if something breaks)

```bash
cp /Users/pankaj/pani/blueshift_observatory/frontend/index.html.bak_daily_summary \
   /Users/pankaj/pani/blueshift_observatory/frontend/index.html

cp /Users/pankaj/pani/blueshift_observatory/backend/pulsation_service.py.bak_daily_summary \
   /Users/pankaj/pani/blueshift_observatory/backend/pulsation_service.py

cp /Users/pankaj/pani/blueshift_observatory/backend/app.py.bak_daily_summary \
   /Users/pankaj/pani/blueshift_observatory/backend/app.py
```

---

## DESIGN NOTES
- Same CSS classes used: `.card-section`, `.card-header`, `.card-title`, `.data-table`, `.text-mono`, `.btn`, `.btn-primary`, `.btn-secondary`, `.btn-sm`
- Chart colors match existing: `#3b82f6` (blue), `#10b981` (green), `#8b5cf6` (purple for delivery rate)
- Chart.js config matches existing: `tension: 0.4`, tooltip `backgroundColor: '#1e293b'`, `padding: 12`
- No new CSS needed
- classdojo.com checkbox: default CHECKED (excluded) — clean data by default

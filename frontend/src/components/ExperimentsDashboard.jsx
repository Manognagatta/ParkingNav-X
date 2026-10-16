import React, { useState } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer, RadarChart, Radar, PolarGrid,
  PolarAngleAxis, PolarRadiusAxis
} from 'recharts'
import { api } from '../api'

const STRATEGIES = ['B1', 'B2', 'B3', 'B4']
const SCENARIOS = [
  'peak_hour', 'E1_normal_day', 'E2_event_placement', 'E3_lot_closure', 'E4_gate_closure'
]
const STRATEGY_COLORS = { B1: '#94a3b8', B2: '#60a5fa', B3: '#a78bfa', B4: '#10b981' }
const METRICS = [
  { key: 'avg_search_time_min', label: 'Avg Search Time (min)', lower: true },
  { key: 'avg_wait_time_min',   label: 'Avg Wait Time (min)',   lower: true },
  { key: 'avg_gate_queue',      label: 'Avg Gate Queue',        lower: true },
  { key: 'overflow_events',     label: 'Overflow Events',       lower: true },
]

export default function ExperimentsDashboard() {
  const [results, setResults] = useState({})   // { scenario: { B1: metrics, B2: ..., ... } }
  const [loading, setLoading] = useState(false)
  const [selectedScenario, setSelectedScenario] = useState('peak_hour')
  const [selectedMetric, setSelectedMetric] = useState('avg_search_time_min')
  const [progress, setProgress] = useState([])

  const runExperiments = async () => {
    setLoading(true)
    setProgress([])
    const newResults = {}

    for (const scenario of SCENARIOS) {
      try {
        const res = await api.compareStrategies(scenario)
        newResults[scenario] = res.data.comparison
        setProgress(p => [...p, `✓ ${scenario}`])
        setResults({ ...newResults })
      } catch (e) {
        setProgress(p => [...p, `✗ ${scenario}: ${e.message}`])
      }
    }

    setLoading(false)
  }

  // Build chart data for selected scenario
  const scenarioData = results[selectedScenario] || {}
  const chartData = METRICS.map(m => ({
    metric: m.label,
    B1: scenarioData.B1?.[m.key] ?? 0,
    B2: scenarioData.B2?.[m.key] ?? 0,
    B3: scenarioData.B3?.[m.key] ?? 0,
    B4: scenarioData.B4?.[m.key] ?? 0,
  }))

  // Radar data (normalized) for selected scenario
  const metricMaxes = METRICS.map(m => Math.max(
    ...STRATEGIES.map(s => scenarioData[s]?.[m.key] ?? 0), 1
  ))
  const radarData = STRATEGIES.map(strategy => {
    const point = { strategy }
    METRICS.forEach((m, i) => {
      const val = scenarioData[strategy]?.[m.key] ?? 0
      // lower is better → invert for radar (higher = better)
      point[m.label] = m.lower ? 1 - val / metricMaxes[i] : val / metricMaxes[i]
    })
    return point
  })

  // Summary table: all scenarios × all strategies
  const hasResults = Object.keys(results).length > 0

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-800">Experiments Dashboard</h2>
          <p className="text-sm text-gray-500">
            Run E1–E5 × B1–B4 strategy comparison. Confirms ParkingNav-X superiority.
          </p>
        </div>
        <button
          onClick={runExperiments}
          disabled={loading}
          className="px-4 py-2 bg-vitap-blue text-white rounded-md text-sm font-medium hover:bg-blue-800 disabled:opacity-50"
        >
          {loading ? '⏳ Running Experiments...' : '🔬 Run All Experiments'}
        </button>
      </div>

      {/* Progress log */}
      {progress.length > 0 && (
        <div className="bg-gray-900 text-green-400 rounded-lg p-3 text-xs font-mono space-y-0.5 max-h-32 overflow-y-auto">
          {progress.map((line, i) => <div key={i}>{line}</div>)}
          {loading && <div className="animate-pulse">Running...</div>}
        </div>
      )}

      {/* Controls */}
      {hasResults && (
        <div className="bg-white rounded-lg shadow p-4">
          <div className="flex gap-4 items-center flex-wrap">
            <div>
              <label className="text-xs font-medium text-gray-600 block mb-1">Scenario</label>
              <select
                value={selectedScenario}
                onChange={e => setSelectedScenario(e.target.value)}
                className="border border-gray-300 rounded px-2 py-1 text-sm"
              >
                {SCENARIOS.filter(s => results[s]).map(s => (
                  <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-xs font-medium text-gray-600 block mb-1">Primary Metric</label>
              <select
                value={selectedMetric}
                onChange={e => setSelectedMetric(e.target.value)}
                className="border border-gray-300 rounded px-2 py-1 text-sm"
              >
                {METRICS.map(m => <option key={m.key} value={m.key}>{m.label}</option>)}
              </select>
            </div>
          </div>
        </div>
      )}

      {hasResults && (
        <>
          {/* Strategy Comparison Bar Chart */}
          <div className="bg-white rounded-lg shadow p-4">
            <h3 className="text-sm font-semibold text-gray-700 mb-3">
              Strategy Comparison — {selectedScenario.replace(/_/g, ' ')}
            </h3>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={chartData} margin={{ top: 5, right: 30, left: 0, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="metric" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Legend />
                {STRATEGIES.map(s => (
                  <Bar key={s} dataKey={s} fill={STRATEGY_COLORS[s]} />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Summary Table (all scenarios) */}
          <div className="bg-white rounded-lg shadow p-4 overflow-x-auto">
            <h3 className="text-sm font-semibold text-gray-700 mb-3">Results Summary Table</h3>
            <table className="min-w-full text-xs">
              <thead>
                <tr className="border-b bg-gray-50">
                  <th className="text-left py-2 px-3 text-gray-500">Scenario</th>
                  <th className="text-left py-2 px-3 text-gray-500">Strategy</th>
                  <th className="text-right py-2 px-3 text-gray-500">Search (min)</th>
                  <th className="text-right py-2 px-3 text-gray-500">Wait (min)</th>
                  <th className="text-right py-2 px-3 text-gray-500">Avg Queue</th>
                  <th className="text-right py-2 px-3 text-gray-500">Overflow</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(results).flatMap(([scenario, stratMap]) =>
                  STRATEGIES.map((s, si) => {
                    const m = stratMap[s] || {}
                    const isB4 = s === 'B4'
                    return (
                      <tr key={`${scenario}-${s}`} className={`border-b ${isB4 ? 'bg-green-50 font-semibold' : ''} ${si === 0 ? 'border-t-2 border-gray-300' : ''}`}>
                        <td className="py-1.5 px-3 text-gray-600">{si === 0 ? scenario.replace(/_/g, ' ') : ''}</td>
                        <td className="py-1.5 px-3">
                          <span className={`px-1.5 py-0.5 rounded text-xs font-bold ${isB4 ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-700'}`}>{s}</span>
                        </td>
                        <td className="text-right py-1.5 px-3">{m.avg_search_time_min ?? '—'}</td>
                        <td className="text-right py-1.5 px-3">{m.avg_wait_time_min ?? '—'}</td>
                        <td className="text-right py-1.5 px-3">{m.avg_gate_queue ?? '—'}</td>
                        <td className="text-right py-1.5 px-3">{m.overflow_events ?? '—'}</td>
                      </tr>
                    )
                  })
                )}
              </tbody>
            </table>
          </div>
        </>
      )}

      {!hasResults && !loading && (
        <div className="text-center py-20 bg-white rounded-lg shadow">
          <div className="text-4xl mb-3">🔬</div>
          <div className="text-gray-500">Click "Run All Experiments" to compare B1–B4 strategies across E1–E5 scenarios</div>
          <div className="text-xs text-gray-400 mt-2">Each experiment runs a full simulation (~10–30s per scenario)</div>
        </div>
      )}
    </div>
  )
}

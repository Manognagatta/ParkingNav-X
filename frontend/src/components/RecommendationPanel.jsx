import React, { useEffect, useState } from 'react'
import { api } from '../api'

const PRIORITY_STYLES = {
  CRITICAL: 'bg-red-50 border-red-300 text-red-900',
  HIGH:     'bg-orange-50 border-orange-300 text-orange-900',
  INFO:     'bg-blue-50 border-blue-200 text-blue-900',
}

const PRIORITY_ICONS = {
  CRITICAL: '🚨',
  HIGH:     '⚠️',
  INFO:     'ℹ️',
}

const WEIGHT_PROFILES = [
  { id: 'P1', label: 'P1 — Minimize Wait Time', desc: 'Prioritizes reducing gate queue and wait time' },
  { id: 'P2', label: 'P2 — Balanced',           desc: 'Equal weight across all objectives' },
  { id: 'P3', label: 'P3 — Minimize Overflow',  desc: 'Heavily penalizes overflow events' },
]

function CostBar({ label, value }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-36 text-gray-500 shrink-0">{label}</span>
      <div className="flex-1 bg-gray-200 rounded-full h-2">
        <div
          className="h-2 rounded-full bg-blue-500 transition-all"
          style={{ width: `${Math.min(100, value * 100)}%` }}
        />
      </div>
      <span className="w-10 text-right text-gray-700">{(value * 100).toFixed(0)}%</span>
    </div>
  )
}

export default function RecommendationPanel() {
  const [recs, setRecs] = useState([])
  const [profile, setProfile] = useState('P1')
  const [loading, setLoading] = useState(false)
  const [lastUpdated, setLastUpdated] = useState(null)

  const fetchRecs = async (wp = profile) => {
    setLoading(true)
    try {
      const res = await api.optimize('peak_hour', wp)
      setRecs(res.data.recommendations || [])
      setLastUpdated(new Date().toLocaleTimeString())
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchRecs() }, [])

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-800">Recommendations</h2>
          <p className="text-sm text-gray-500">Proactive parking recommendations from the multi-objective optimizer</p>
        </div>
        {lastUpdated && <span className="text-xs text-gray-400">Updated: {lastUpdated}</span>}
      </div>

      {/* Weight Profile Selector */}
      <div className="bg-white rounded-lg shadow p-4">
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Optimization Weight Profile
        </label>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {WEIGHT_PROFILES.map(wp => (
            <label
              key={wp.id}
              className={`flex items-start gap-2 p-3 border rounded-lg cursor-pointer transition-colors ${
                profile === wp.id ? 'border-blue-500 bg-blue-50' : 'border-gray-200 hover:border-gray-300'
              }`}
            >
              <input
                type="radio"
                name="profile"
                value={wp.id}
                checked={profile === wp.id}
                onChange={e => {
                  setProfile(e.target.value)
                  fetchRecs(e.target.value)
                }}
                className="mt-0.5"
              />
              <div>
                <div className="text-sm font-medium text-gray-800">{wp.label}</div>
                <div className="text-xs text-gray-500">{wp.desc}</div>
              </div>
            </label>
          ))}
        </div>
      </div>

      {/* Recommendation Cards */}
      {loading ? (
        <div className="text-center py-12 text-gray-400">Generating recommendations...</div>
      ) : (
        <div className="space-y-3">
          {recs.map((rec) => (
            <div key={rec.id} className={`border rounded-lg p-4 ${PRIORITY_STYLES[rec.priority] || 'bg-gray-50 border-gray-200'}`}>
              <div className="flex items-start justify-between mb-2">
                <div className="flex items-center gap-2">
                  <span className="text-lg">{PRIORITY_ICONS[rec.priority] || '📋'}</span>
                  <div>
                    <span className="text-xs font-bold uppercase tracking-wide">{rec.priority}</span>
                    <span className="mx-1 text-xs text-gray-400">·</span>
                    <span className="text-xs text-gray-500">{rec.type?.replace(/_/g, ' ')}</span>
                  </div>
                </div>
                <span className="text-xs text-gray-400">#{rec.id}</span>
              </div>

              <p className="text-sm font-medium mb-2">{rec.message}</p>

              {/* Proactive alert details */}
              {rec.type === 'PROACTIVE_REROUTE' && (
                <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                  <div className="bg-white bg-opacity-60 rounded p-2">
                    <div className="text-gray-500">Current fill</div>
                    <div className="font-bold">{rec.current_fill ? `${(rec.current_fill * 100).toFixed(0)}%` : '—'}</div>
                  </div>
                  <div className="bg-white bg-opacity-60 rounded p-2">
                    <div className="text-gray-500">15-min forecast</div>
                    <div className="font-bold text-red-600">{rec.forecast_fill_15min ? `${(rec.forecast_fill_15min * 100).toFixed(0)}%` : '—'}</div>
                  </div>
                </div>
              )}

              {/* Alternative lots */}
              {rec.alternatives && rec.alternatives.length > 0 && (
                <div className="mt-2">
                  <div className="text-xs text-gray-500 mb-1">Suggested alternatives:</div>
                  <div className="flex gap-2">
                    {rec.alternatives.map(alt => (
                      <div key={alt.lot_id} className="bg-white bg-opacity-70 rounded px-2 py-1 text-xs">
                        <span className="font-medium">{alt.lot_name}</span>
                        <span className="text-gray-500 ml-1">({(alt.forecast_fill * 100).toFixed(0)}% full)</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Lot ranking */}
              {rec.type === 'LOT_RANKING' && rec.ranked_lots && (
                <div className="mt-2 space-y-1.5">
                  {rec.ranked_lots.map((lot, i) => (
                    <div key={lot.lot_id} className={`flex items-center gap-2 text-xs p-2 rounded ${
                      lot.recommended ? 'bg-green-50' : 'bg-white bg-opacity-50'
                    }`}>
                      <span className="text-gray-400 font-bold w-4">#{i + 1}</span>
                      <span className="flex-1 font-medium">{lot.lot_name}</span>
                      <span className="text-gray-500">{lot.available_spaces} spaces free</span>
                      <span className={`font-bold ${lot.forecast_fill_rate_15min >= 0.9 ? 'text-red-600' : lot.forecast_fill_rate_15min >= 0.7 ? 'text-orange-500' : 'text-green-600'}`}>
                        {(lot.forecast_fill_rate_15min * 100).toFixed(0)}%
                      </span>
                      {lot.recommended && <span className="text-green-600 font-bold">✓ Recommend</span>}
                    </div>
                  ))}
                </div>
              )}

              {/* Cost function */}
              {rec.cost_function && (
                <div className="mt-3 p-3 bg-white bg-opacity-60 rounded">
                  <div className="text-xs font-semibold text-gray-600 mb-2">
                    Objective J = {rec.cost_function.J?.toFixed(3) ?? '—'} (profile: {rec.weight_profile || profile})
                  </div>
                  <div className="space-y-1">
                    {[
                      ['w1 · Waiting Time', rec.cost_function.waiting_time],
                      ['w2 · Queue Cost', rec.cost_function.queue_cost],
                      ['w3 · Search Travel', rec.cost_function.search_travel],
                      ['w4 · Util. Imbalance', rec.cost_function.utilization_imbalance],
                      ['w5 · Overflow Penalty', rec.cost_function.overflow_penalty],
                    ].map(([label, val]) => (
                      <CostBar key={label} label={label} value={val ?? 0} />
                    ))}
                  </div>
                </div>
              )}
            </div>
          ))}

          {recs.length === 0 && !loading && (
            <div className="text-center py-12 text-gray-400">
              No active recommendations. Connect to the backend to generate them.
            </div>
          )}
        </div>
      )}
    </div>
  )
}

import React, { useEffect, useState, useCallback } from 'react'
import {
  MapContainer, TileLayer, Marker, Popup, Circle,
  Tooltip, Polyline, useMap,
} from 'react-leaflet'
import L from 'leaflet'
import { api } from '../api'
import 'leaflet/dist/leaflet.css'

// Fix Leaflet default icon
delete L.Icon.Default.prototype._getIconUrl
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
})

// VIT-AP approximate center
const VITAP_CENTER = [16.4935, 80.4995]

function fillColor(rate) {
  if (rate >= 0.90) return '#ef4444'
  if (rate >= 0.70) return '#f97316'
  if (rate >= 0.50) return '#eab308'
  return '#22c55e'
}

function categoryColor(cat) {
  if (!cat) return '#6b7280'
  if (cat.includes('academic')) return '#3b82f6'
  if (cat.includes('hostel')) return '#8b5cf6'
  if (cat.includes('cafeteria') || cat.includes('food')) return '#f59e0b'
  if (cat.includes('sports')) return '#10b981'
  if (cat.includes('admin')) return '#6366f1'
  return '#6b7280'
}

function categoryIcon(cat, name) {
  const color = categoryColor(cat)
  const emoji = cat?.includes('hostel') ? '🏠'
    : cat?.includes('academic') ? '🎓'
    : cat?.includes('food') || cat?.includes('cafeteria') ? '🍽️'
    : cat?.includes('sports') ? '⚽'
    : cat?.includes('admin') ? '🏛️'
    : cat?.includes('library') ? '📚'
    : '📍'
  return L.divIcon({
    html: `<div style="background:${color};color:white;padding:2px 6px;border-radius:10px;font-size:10px;font-weight:bold;white-space:nowrap;box-shadow:0 1px 4px rgba(0,0,0,0.3);">${emoji} ${name.length > 12 ? name.slice(0, 12) + '…' : name}</div>`,
    className: '',
    iconAnchor: [40, 10],
  })
}

function gateIcon(name, status) {
  return L.divIcon({
    html: `<div style="background:#003087;color:white;padding:3px 8px;border-radius:5px;font-size:11px;font-weight:bold;white-space:nowrap;box-shadow:0 2px 6px rgba(0,0,0,0.4);">${status === 'open' ? '🚗' : '🚫'} ${name}</div>`,
    className: '',
    iconAnchor: [40, 12],
  })
}

function FitBounds({ campus }) {
  const map = useMap()
  useEffect(() => {
    if (!campus) return
    const allPts = [
      ...(campus.gates || []).map(g => [g.lat, g.lng]),
      ...(campus.parking_lots || []).map(l => [l.lat, l.lng]),
      ...(campus.destinations || []).map(d => [d.lat, d.lng]),
    ].filter(([la, lo]) => la && lo)
    if (allPts.length > 0) {
      map.fitBounds(allPts, { padding: [30, 30] })
    }
  }, [campus, map])
  return null
}

export default function CampusMap({ onNavigateTo }) {
  const [campus, setCampus] = useState(null)
  const [predictions, setPredictions] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [showRoads, setShowRoads] = useState(true)
  const [showDests, setShowDests] = useState(true)
  const [highlightRoute, setHighlightRoute] = useState(null) // [{lat,lng}]

  const fetchData = useCallback(async () => {
    setLoading(true)

    try {
      const [campusResult, predictionResult] = await Promise.allSettled([
        api.getCampus(),
        api.predictAll(15),
      ])

      const errors = []

      if (campusResult.status === 'fulfilled') {
        setCampus(campusResult.value.data)
      } else {
        errors.push(
          `Campus: ${
            campusResult.reason.response?.data?.detail ||
            campusResult.reason.message ||
            'Request failed'
          }`
        )
      }

      if (predictionResult.status === 'fulfilled') {
        setPredictions(predictionResult.value.data.predictions ?? [])
      } else {
        setPredictions([])
        errors.push(
          `Predictions: ${
            predictionResult.reason.response?.data?.detail ||
            predictionResult.reason.message ||
            'Request failed'
          }`
        )
      }

      setError(errors.length ? errors.join(' | ') : null)
      setLastUpdated(new Date().toLocaleTimeString())
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 30000)
    return () => clearInterval(interval)
  }, [fetchData])

  const predMap = {}
  predictions.forEach(p => { predMap[p.lot_id] = p })

  // Build road polylines from campus data
  const roadLines = (campus?.roads || []).filter(r => r.status === 'open' && r.geometry?.length >= 2)

  return (
    <div>
      {/* Header + controls */}
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div>
          <h2 className="text-lg font-bold text-gray-800">VIT-AP Campus Digital Twin</h2>
          <p className="text-sm text-gray-500">15-min occupancy forecast · Live road network · Click any destination</p>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <label className="flex items-center gap-1.5 text-sm text-gray-600 cursor-pointer">
            <input type="checkbox" checked={showRoads} onChange={e => setShowRoads(e.target.checked)} />
            Roads
          </label>
          <label className="flex items-center gap-1.5 text-sm text-gray-600 cursor-pointer">
            <input type="checkbox" checked={showDests} onChange={e => setShowDests(e.target.checked)} />
            Destinations
          </label>
          {lastUpdated && <span className="text-xs text-gray-400">Updated: {lastUpdated}</span>}
          <button onClick={fetchData} className="px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700">
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-3 p-3 bg-red-50 border border-red-200 rounded text-red-700 text-sm">
          ⚠️ {error}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Map */}
        <div className="lg:col-span-2 rounded-lg overflow-hidden border border-gray-200 shadow" style={{ height: 480 }}>
          <MapContainer center={VITAP_CENTER} zoom={16} style={{ height: '100%', width: '100%' }}>
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org">OpenStreetMap</a>'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            {campus && <FitBounds campus={campus} />}

            {/* Road network */}
            {showRoads && roadLines.map(road => {
              const positions = road.geometry.map(pt => [pt.lat, pt.lng])
              const isDrive = road.is_driveable
              return (
                <Polyline
                  key={road.id}
                  positions={positions}
                  pathOptions={{
                    color: isDrive ? '#3b82f6' : '#94a3b8',
                    weight: isDrive ? 3 : 1.5,
                    opacity: isDrive ? 0.7 : 0.5,
                    dashArray: isDrive ? null : '4 4',
                  }}
                >
                  <Tooltip sticky>
                    <span className="text-xs">{road.name} · {road.length_meters}m · {Math.round(road.travel_time_seconds/60)}min</span>
                  </Tooltip>
                </Polyline>
              )
            })}

            {/* Highlighted route */}
            {highlightRoute && highlightRoute.length >= 2 && (
              <Polyline
                positions={highlightRoute.map(pt => [pt.lat, pt.lng])}
                pathOptions={{ color: '#f59e0b', weight: 5, opacity: 0.9 }}
              />
            )}

            {/* Parking lot circles */}
            {(campus?.parking_lots || []).map(lot => {
              const pred = predMap[lot.id]
              const fill = pred ? pred.predicted_fill_rate : 0.4
              return (
                <Circle
                  key={lot.id}
                  center={[lot.lat, lot.lng]}
                  radius={55}
                  pathOptions={{
                    fillColor: fillColor(fill),
                    fillOpacity: 0.8,
                    color: fill >= 0.90 ? '#dc2626' : '#1d4ed8',
                    weight: 2.5,
                  }}
                >
                  <Tooltip permanent direction="top" offset={[0, -22]}>
                    <div className="text-xs font-semibold">
                      {lot.name.replace(' Lot', '').replace(' Parking', '')}<br />
                      <span style={{ color: fillColor(fill) }}>{(fill * 100).toFixed(0)}%</span>
                    </div>
                  </Tooltip>
                  <Popup>
                    <div className="text-sm min-w-40">
                      <strong className="text-blue-800">{lot.name}</strong><br />
                      <span className="text-gray-500">Status: </span>
                      <span className={lot.status === 'open' ? 'text-green-600' : 'text-red-600'}>{lot.status}</span><br />
                      Usable: <strong>{lot.usable_capacity}</strong> spaces<br />
                      Forecast (15m): <strong style={{ color: fillColor(fill) }}>{(fill * 100).toFixed(1)}%</strong><br />
                      {pred && (
                        <>
                          90% CI: [{(pred.lower_90 * 100).toFixed(0)}%, {(pred.upper_90 * 100).toFixed(0)}%]<br />
                          Model: <em>{pred.model || '—'}</em><br />
                          {pred.proactive_alert && <span className="text-red-600 font-bold">⚠️ OVERFLOW RISK</span>}
                        </>
                      )}
                      <div className="mt-1 text-xs text-gray-400">[SYNTHETIC data — not real occupancy]</div>
                    </div>
                  </Popup>
                </Circle>
              )
            })}

            {/* Gate markers */}
            {(campus?.gates || []).map(gate => (
              <Marker
                key={gate.id}
                position={[gate.lat, gate.lng]}
                icon={gateIcon(gate.name, gate.status)}
              >
                <Popup>
                  <div className="text-sm">
                    <strong>{gate.name}</strong><br />
                    Throughput: {gate.capacity} veh/min<br />
                    Status: <span className={gate.status === 'open' ? 'text-green-600' : 'text-red-600'}>{gate.status}</span>
                  </div>
                </Popup>
              </Marker>
            ))}

            {/* Destination markers */}
            {showDests && (campus?.destinations || []).map(dest => (
              <Marker
                key={dest.id}
                position={[dest.lat, dest.lng]}
                icon={categoryIcon(dest.category, dest.name)}
              >
                <Popup>
                  <div className="text-sm min-w-36">
                    <strong className="text-gray-800">{dest.name}</strong><br />
                    <span className="text-gray-500 capitalize">{dest.category?.replace(/_/g, ' ')}</span><br />
                    {dest.nearest_lots?.length > 0 && (
                      <span className="text-xs text-gray-400">Nearest lots: {dest.nearest_lots.length}</span>
                    )}
                    {onNavigateTo && (
                      <button
                        onClick={() => onNavigateTo(dest)}
                        className="mt-2 w-full px-2 py-1 bg-blue-600 text-white text-xs rounded hover:bg-blue-700"
                      >
                        🅿️ Find Parking Here
                      </button>
                    )}
                  </div>
                </Popup>
              </Marker>
            ))}
          </MapContainer>
        </div>

        {/* Sidebar */}
        <div className="space-y-3 overflow-y-auto" style={{ maxHeight: 480 }}>
          {/* Stats summary */}
          {campus && (
            <div className="p-3 bg-blue-50 rounded-lg border border-blue-200">
              <div className="text-xs font-bold text-blue-700 mb-2">Campus Overview</div>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="text-center p-2 bg-white rounded border">
                  <div className="text-lg font-bold text-blue-700">{campus.parking_lots?.length || 0}</div>
                  <div className="text-gray-500">Parking Zones</div>
                </div>
                <div className="text-center p-2 bg-white rounded border">
                  <div className="text-lg font-bold text-green-700">
                    {campus.parking_lots?.reduce((s, l) => s + (l.usable_capacity || 0), 0)}
                  </div>
                  <div className="text-gray-500">Total Spaces</div>
                </div>
                <div className="text-center p-2 bg-white rounded border">
                  <div className="text-lg font-bold text-purple-700">{campus.destinations?.length || 0}</div>
                  <div className="text-gray-500">Destinations</div>
                </div>
                <div className="text-center p-2 bg-white rounded border">
                  <div className="text-lg font-bold text-gray-700">{campus.roads?.length || 0}</div>
                  <div className="text-gray-500">Road Segments</div>
                </div>
              </div>
            </div>
          )}

          {/* Lot cards */}
          <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Parking Lots</div>
          {loading && <div className="text-sm text-gray-400">Loading…</div>}
          {(campus?.parking_lots || []).map(lot => {
            const pred = predMap[lot.id]
            const fill = pred ? pred.predicted_fill_rate : 0
            return (
              <div
                key={lot.id}
                className={`p-3 border rounded-lg text-sm ${
                  fill >= 0.90 ? 'border-red-300 bg-red-50'
                  : fill >= 0.70 ? 'border-orange-200 bg-orange-50'
                  : 'border-gray-200 bg-white'
                }`}
              >
                <div className="flex justify-between items-start mb-1">
                  <div className="font-medium text-gray-800 text-xs leading-tight">{lot.name}</div>
                  {pred?.proactive_alert && <span className="text-red-600 text-xs font-bold ml-1">⚠️</span>}
                </div>
                <div className="flex justify-between text-xs text-gray-500 mb-1">
                  <span>15-min forecast</span>
                  <span className="font-semibold" style={{ color: fillColor(fill) }}>
                    {fill ? (fill * 100).toFixed(0) : '—'}%
                  </span>
                </div>
                <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full"
                    style={{ width: `${(fill || 0) * 100}%`, backgroundColor: fillColor(fill) }}
                  />
                </div>
                <div className="text-xs text-gray-400 mt-1">
                  {lot.usable_capacity} spaces · {lot.status}
                </div>
              </div>
            )
          })}

          {/* Legend */}
          <div className="p-2 bg-gray-50 rounded border border-gray-200 text-xs">
            <div className="font-semibold text-gray-500 mb-1.5">Map Legend</div>
            <div className="space-y-1">
              {[['< 50%', '#22c55e'], ['50–70%', '#eab308'], ['70–90%', '#f97316'], ['> 90%', '#ef4444']].map(([l, c]) => (
                <div key={l} className="flex items-center gap-2 text-gray-600">
                  <div className="w-3 h-3 rounded-full flex-shrink-0" style={{ background: c }} />
                  {l} full
                </div>
              ))}
              <div className="flex items-center gap-2 text-gray-600 mt-1">
                <div className="w-6 h-0.5 flex-shrink-0" style={{ background: '#3b82f6' }} />
                Driveable road
              </div>
              <div className="flex items-center gap-2 text-gray-600">
                <div className="w-6 border-t border-dashed border-gray-400 flex-shrink-0" />
                Walking path
              </div>
            </div>
          </div>
          <div className="text-xs text-gray-400 italic">
            [SYNTHETIC] Forecast from XGBoost model. Not real occupancy data.
          </div>
        </div>
      </div>
    </div>
  )
}

import React, { useEffect, useState } from 'react'
import { MapContainer, TileLayer, Marker, Popup, Circle, Tooltip } from 'react-leaflet'
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

const VITAP_CENTER = [16.4912, 80.5005]

const LOT_COORDS = {
  'vitap-lot-academic-main':  [16.4935, 80.4995],
  'vitap-lot-hostel':         [16.4915, 80.5008],
  'vitap-lot-admin-visitor':  [16.4928, 80.4982],
  'vitap-lot-sports':         [16.4952, 80.4990],
  'vitap-lot-overflow':       [16.4900, 80.4970],
}

const GATE_COORDS = {
  'vitap-gate-main':    [16.4912, 80.5018],
  'vitap-gate-visitor': [16.4895, 80.4960],
}

function fillColor(rate) {
  if (rate >= 0.90) return '#ef4444'  // red
  if (rate >= 0.70) return '#f97316'  // orange
  if (rate >= 0.50) return '#eab308'  // yellow
  return '#22c55e'                     // green
}

function gateIcon(status) {
  return L.divIcon({
    html: `<div style="background:#003087;color:white;padding:3px 7px;border-radius:4px;font-size:11px;font-weight:bold;white-space:nowrap;">${status === 'open' ? '🚗 OPEN' : '🚫 CLOSED'}</div>`,
    className: '',
    iconAnchor: [30, 10],
  })
}

export default function CampusMap() {
  const [campus, setCampus] = useState(null)
  const [predictions, setPredictions] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [lastUpdated, setLastUpdated] = useState(null)

  const fetchData = async () => {
    try {
      const [campusRes, predRes] = await Promise.all([
        api.getCampus(),
        api.predictAll(15),
      ])
      setCampus(campusRes.data)
      setPredictions(predRes.data.predictions || [])
      setLastUpdated(new Date().toLocaleTimeString())
      setError(null)
    } catch (e) {
      setError('Cannot connect to backend. Start the API server first.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 30000) // refresh every 30s
    return () => clearInterval(interval)
  }, [])

  const predMap = {}
  predictions.forEach(p => { predMap[p.lot_id] = p })

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-lg font-bold text-gray-800">VIT-AP Campus Digital Twin</h2>
          <p className="text-sm text-gray-500">Real-time parking map with 15-minute occupancy forecast</p>
        </div>
        <div className="flex items-center gap-3">
          {lastUpdated && <span className="text-xs text-gray-400">Updated: {lastUpdated}</span>}
          <button onClick={fetchData} className="px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700">
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded text-red-700 text-sm">
          ⚠️ {error}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Map */}
        <div className="lg:col-span-2 h-96 rounded-lg overflow-hidden border border-gray-200 shadow">
          <MapContainer center={VITAP_CENTER} zoom={16} style={{ height: '100%', width: '100%' }}>
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org">OpenStreetMap</a>'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />

            {/* Parking lot circles */}
            {Object.entries(LOT_COORDS).map(([lotId, coords]) => {
              const pred = predMap[lotId]
              const fill = pred ? pred.predicted_fill_rate : 0.4
              const lot = campus?.parking_lots?.find(l => l.id === lotId)
              return (
                <React.Fragment key={lotId}>
                  <Circle
                    center={coords}
                    radius={50}
                    pathOptions={{
                      fillColor: fillColor(fill),
                      fillOpacity: 0.75,
                      color: fill >= 0.90 ? '#dc2626' : '#1d4ed8',
                      weight: 2,
                    }}
                  >
                    <Tooltip permanent direction="top" offset={[0, -20]}>
                      <div className="text-xs font-medium">
                        {(lot?.name || lotId.replace('vitap-lot-', ''))}<br/>
                        <span style={{ color: fillColor(fill) }}>{(fill * 100).toFixed(0)}% full</span>
                      </div>
                    </Tooltip>
                    <Popup>
                      <div className="text-sm">
                        <strong>{lot?.name || lotId}</strong><br/>
                        Capacity: {lot?.usable_capacity || '?'} spaces<br/>
                        Forecast fill (15m): <strong>{(fill * 100).toFixed(1)}%</strong><br/>
                        {pred && (
                          <>
                            90% CI: [{(pred.lower_90 * 100).toFixed(0)}%, {(pred.upper_90 * 100).toFixed(0)}%]<br/>
                            {pred.proactive_alert && <span className="text-red-600 font-bold">⚠️ OVERFLOW ALERT</span>}
                          </>
                        )}
                      </div>
                    </Popup>
                  </Circle>
                </React.Fragment>
              )
            })}

            {/* Gate markers */}
            {Object.entries(GATE_COORDS).map(([gateId, coords]) => {
              const gate = campus?.gates?.find(g => g.id === gateId)
              return (
                <Marker key={gateId} position={coords} icon={gateIcon(gate?.status || 'open')}>
                  <Popup>
                    <div className="text-sm">
                      <strong>{gate?.name || gateId}</strong><br/>
                      Throughput: {gate?.capacity || '?'} veh/min<br/>
                      Status: {gate?.status || 'open'}
                    </div>
                  </Popup>
                </Marker>
              )
            })}
          </MapContainer>
        </div>

        {/* Lot Cards */}
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-gray-600 uppercase tracking-wide">Parking Lots</h3>
          {campus?.parking_lots?.map(lot => {
            const pred = predMap[lot.id]
            const fill = pred ? pred.predicted_fill_rate : 0
            return (
              <div key={lot.id} className={`p-3 border rounded-lg text-sm ${
                fill >= 0.90 ? 'border-red-300 bg-red-50' :
                fill >= 0.70 ? 'border-orange-300 bg-orange-50' :
                'border-gray-200 bg-white'
              }`}>
                <div className="flex justify-between items-start">
                  <div className="font-medium text-gray-800 text-xs">{lot.name}</div>
                  {pred?.proactive_alert && <span className="text-red-600 text-xs font-bold">⚠️ ALERT</span>}
                </div>
                <div className="mt-1.5">
                  <div className="flex justify-between text-xs text-gray-500 mb-1">
                    <span>Forecast fill (15m)</span>
                    <span className={fill >= 0.9 ? 'text-red-600 font-bold' : fill >= 0.7 ? 'text-orange-500 font-semibold' : 'text-green-600'}>
                      {fill ? (fill * 100).toFixed(0) : '—'}%
                    </span>
                  </div>
                  <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all"
                      style={{ width: `${(fill || 0) * 100}%`, backgroundColor: fillColor(fill || 0) }}
                    />
                  </div>
                  <div className="text-xs text-gray-400 mt-1">Capacity: {lot.usable_capacity} spaces</div>
                </div>
              </div>
            )
          }) || (loading ? (
            <div className="text-sm text-gray-400">Loading campus data...</div>
          ) : null)}

          {/* Legend */}
          <div className="mt-3 p-2 bg-gray-50 rounded border border-gray-200">
            <div className="text-xs font-semibold text-gray-500 mb-1">Fill Rate Legend</div>
            {[['< 50%', '#22c55e'], ['50–70%', '#eab308'], ['70–90%', '#f97316'], ['> 90%', '#ef4444']].map(([label, color]) => (
              <div key={label} className="flex items-center gap-1.5 text-xs text-gray-600">
                <div className="w-3 h-3 rounded-full" style={{ backgroundColor: color }} />
                {label}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

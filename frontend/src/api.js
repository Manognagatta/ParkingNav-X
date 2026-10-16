import axios from 'axios'

const BASE = '/api'

export const api = {
  getRoot:          () => axios.get(`${BASE}/`),
  getCampus:        () => axios.get(`${BASE}/campus`),
  getScenarios:     () => axios.get(`${BASE}/scenarios`),
  getStrategies:    () => axios.get(`${BASE}/strategies`),
  getEvents:        () => axios.get(`${BASE}/events`),

  simulate:         (scenario, strategy) =>
    axios.post(`${BASE}/simulate`, { scenario, strategy }),

  compareStrategies: (scenario) =>
    axios.get(`${BASE}/compare/${scenario}`),

  predictOccupancy: (lot_id, horizon_minutes = 15) =>
    axios.get(`${BASE}/predict/occupancy`, { params: { lot_id, horizon_minutes } }),

  predictAll:       (horizon_minutes = 15) =>
    axios.get(`${BASE}/predict/all`, { params: { horizon_minutes } }),

  getRiskCurrent:   () => axios.get(`${BASE}/risk/current`),
  getRiskForecast:  (horizon = 15) =>
    axios.get(`${BASE}/risk/forecast`, { params: { horizon } }),

  optimize:         (scenario = 'peak_hour', weight_profile = 'P1') =>
    axios.post(`${BASE}/optimize`, { scenario, weight_profile }),

  getRecommendations: () => axios.get(`${BASE}/recommendations`),
}

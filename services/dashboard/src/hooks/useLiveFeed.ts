import { useEffect, useRef, useState } from 'react'
import { wsUrl } from '../lib/api'
import type { AlertEvent, ModelMetricsEvent, WsMessage } from '../types'

const MAX_ALERTS = 300

export interface LiveFeed {
  alerts: AlertEvent[]
  modelMetrics: Record<string, ModelMetricsEvent>
  connected: boolean
}

/** One WebSocket for the whole app: backlog + live alerts + live
 * model-metrics all arrive on api-gateway's single `/ws` (see
 * services/api-gateway/app/main.py). Reconnects with capped exponential
 * backoff + jitter so a gateway restart doesn't produce a reconnect storm. */
export function useLiveFeed(): LiveFeed {
  const [alerts, setAlerts] = useState<AlertEvent[]>([])
  const [modelMetrics, setModelMetrics] = useState<Record<string, ModelMetricsEvent>>({})
  const [connected, setConnected] = useState(false)
  const attemptRef = useRef(0)

  useEffect(() => {
    let socket: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let cancelled = false

    function connect() {
      socket = new WebSocket(wsUrl())

      socket.onopen = () => {
        attemptRef.current = 0
        setConnected(true)
      }

      socket.onmessage = (event) => {
        const msg = JSON.parse(event.data) as WsMessage
        if (msg.type === 'backlog') {
          setAlerts(msg.data.slice(-MAX_ALERTS).reverse())
        } else if (msg.type === 'alert') {
          setAlerts((prev) => [msg.data, ...prev].slice(0, MAX_ALERTS))
        } else if (msg.type === 'model_metrics') {
          setModelMetrics((prev) => ({ ...prev, [msg.data.model_id]: msg.data }))
        }
      }

      socket.onclose = () => {
        setConnected(false)
        if (cancelled) return
        const attempt = attemptRef.current++
        const delay = Math.min(500 * 2 ** attempt, 15_000) * (0.75 + Math.random() * 0.5)
        reconnectTimer = setTimeout(connect, delay)
      }

      socket.onerror = () => {
        socket?.close()
      }
    }

    connect()
    return () => {
      cancelled = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [])

  return { alerts, modelMetrics, connected }
}

import axios from 'axios'

export const client = axios.create({ baseURL: '/api', timeout: 30_000 })

client.interceptors.response.use(
  (resp) => {
    const body = resp.data
    if (body?.code === 0) return { ...resp, data: body.data }
    const err: any = new Error(body?.message || 'API error')
    err.code = body?.code
    err.detail = body?.detail
    throw err
  },
  (err) => { throw err },
)

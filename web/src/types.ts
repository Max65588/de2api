export type Account = {
  id: string
  user_id: string
  user_name: string
  country_code: string
  language: string
  is_real_name: boolean
  created_at: string
  updated_at: string
  active: boolean
}

export type SessionState = {
  enabled: boolean
  authenticated: boolean
}

export type CallLog = {
  created_at: string
  request_id: string
  endpoint: string
  method: string
  account_id?: string
  api_key_name?: string
  model?: string
  stream?: boolean
  status_code: number
  duration_ms: number
  model_count?: number
  error?: string
}

export type ApiKeyConfig = {
  name: string
  key: string
  enabled: boolean
}

export type AdminConfig = {
  admin_password_set: boolean
  account_strategy: "round_robin" | "sticky"
  theme: "dark" | "light"
  api_keys: ApiKeyConfig[]
}

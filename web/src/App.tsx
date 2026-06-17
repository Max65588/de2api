import {
  Activity,
  Copy,
  Download,
  KeyRound,
  LogOut,
  Moon,
  Plus,
  RefreshCw,
  Save,
  Settings,
  ShieldCheck,
  Sun,
  Trash2,
  Upload,
  UserRoundCheck,
  Users,
} from "lucide-react"
import { FormEvent, useEffect, useRef, useState } from "react"

import { api } from "@/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import type { Account, AdminConfig, ApiKeyConfig, CallLog, SessionState } from "@/types"

type Page = "accounts" | "keys" | "config" | "logs"

function formatTime(value: string) {
  if (!value) return "-"
  return new Date(value).toLocaleString()
}

function applyTheme(theme: "dark" | "light") {
  document.documentElement.classList.toggle("dark", theme === "dark")
}

function LoginView({ onLogin }: { onLogin: () => void }) {
  const [password, setPassword] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setLoading(true)
    setError("")
    try {
      await api<{ ok: boolean }>("/admin/api/login", {
        method: "POST",
        body: JSON.stringify({ password }),
      })
      onLogin()
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败")
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="grid min-h-screen place-items-center bg-muted/40 px-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5" />
            hm-api 管理后台
          </CardTitle>
          <CardDescription>输入管理员密码后管理账户和调用日志。</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={submit}>
            <Input
              autoFocus
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="管理员密码"
            />
            {error ? <p className="text-sm text-destructive">{error}</p> : null}
            <Button className="w-full" disabled={loading || !password}>
              {loading ? "验证中..." : "登录"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </main>
  )
}

function AccountsPage() {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [oauthDialogOpen, setOauthDialogOpen] = useState(false)
  const [oauthLoginUrl, setOauthLoginUrl] = useState("")
  const [callbackUrl, setCallbackUrl] = useState("")
  const importInputRef = useRef<HTMLInputElement>(null)

  async function load() {
    setError("")
    try {
      const data = await api<{ accounts: Account[] }>("/admin/api/accounts")
      setAccounts(data.accounts)
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败")
    }
  }

  useEffect(() => {
    void load()
  }, [])

  async function addUser() {
    setBusy(true)
    setError("")
    try {
      const data = await api<{ login_url: string }>("/admin/api/oauth/start", { method: "POST" })
      setOauthLoginUrl(data.login_url)
      setCallbackUrl("")
      setOauthDialogOpen(true)
      window.open(data.login_url, "_blank", "noopener,noreferrer")
    } catch (err) {
      setError(err instanceof Error ? err.message : "启动登录失败")
    } finally {
      setBusy(false)
    }
  }

  async function completeOAuth() {
    const value = callbackUrl.trim()
    if (!value) {
      setError("请粘贴浏览器地址栏里的 callback URL")
      return
    }
    setBusy(true)
    setError("")
    try {
      await api<{ account: Account }>("/admin/api/oauth/complete", {
        method: "POST",
        body: JSON.stringify({ callback_url: value }),
      })
      setOauthDialogOpen(false)
      setCallbackUrl("")
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : "完成登录失败")
    } finally {
      setBusy(false)
    }
  }

  async function activate(accountId: string) {
    await api<{ account: Account }>("/admin/api/accounts/active", {
      method: "POST",
      body: JSON.stringify({ account_id: accountId }),
    })
    await load()
  }

  async function remove(account: Account) {
    const title = account.user_name || account.user_id || account.id
    if (!window.confirm(`删除账户 ${title}？`)) return
    await api<{ ok: boolean }>(`/admin/api/accounts/${encodeURIComponent(account.id)}`, { method: "DELETE" })
    await load()
  }

  async function copyHeader(accountId: string) {
    await navigator.clipboard.writeText(`x-hm-account-id: ${accountId}`)
  }

  async function exportAccountFile() {
    setBusy(true)
    setError("")
    try {
      const response = await fetch("/admin/api/accounts/export", {
        credentials: "same-origin",
      })
      if (!response.ok) {
        const data = await response.json().catch(() => ({}))
        throw new Error(typeof data.detail === "string" ? data.detail : "导出失败")
      }
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = url
      link.download = "hm-api-accounts-export.json"
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(err instanceof Error ? err.message : "导出失败")
    } finally {
      setBusy(false)
    }
  }

  async function importAccountFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ""
    if (!file) return
    setBusy(true)
    setError("")
    try {
      const payload = JSON.parse(await file.text())
      const data = await api<{ imported: number; accounts: Account[] }>("/admin/api/accounts/import", {
        method: "POST",
        body: JSON.stringify({ payload }),
      })
      setAccounts(data.accounts)
    } catch (err) {
      setError(err instanceof Error ? err.message : "导入失败，请确认文件格式正确")
    } finally {
      setBusy(false)
    }
  }

  const active = accounts.find((account) => account.active)

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">账户管理</h1>
          <p className="text-sm text-muted-foreground">管理 DevEco 多账户，默认使用活跃账户。</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => void load()}>
            <RefreshCw className="h-4 w-4" />
            刷新
          </Button>
          <Button variant="outline" disabled={busy || !accounts.length} onClick={() => void exportAccountFile()}>
            <Download className="h-4 w-4" />
            导出账户
          </Button>
          <Button variant="outline" disabled={busy} onClick={() => importInputRef.current?.click()}>
            <Upload className="h-4 w-4" />
            导入账户
          </Button>
          <Button disabled={busy} onClick={addUser}>
            <Plus className="h-4 w-4" />
            添加用户
          </Button>
        </div>
        <input ref={importInputRef} className="hidden" type="file" accept="application/json,.json" onChange={importAccountFile} />
      </div>

      <Dialog open={oauthDialogOpen} onOpenChange={setOauthDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>完成 DevEco 登录</DialogTitle>
            <DialogDescription>
              已打开登录页。若登录后停在 localhost callback，把浏览器地址栏中的完整 URL 粘贴到下方。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="rounded-md border bg-muted/40 p-3 text-sm text-muted-foreground">
              远程服务器场景下，DevEco 会回调到你本机的 localhost，服务器无法自动收到结果；粘贴 callback URL 后可由服务器继续完成保存。
            </div>
            <Input
              value={callbackUrl}
              onChange={(event) => setCallbackUrl(event.target.value)}
              placeholder="http://localhost:10101/callback?code=...&tempToken=...&siteId=1"
            />
          </div>
          <DialogFooter>
            {oauthLoginUrl ? (
              <Button variant="outline" onClick={() => window.open(oauthLoginUrl, "_blank", "noopener,noreferrer")}>
                重新打开登录页
              </Button>
            ) : null}
            <Button disabled={busy || !callbackUrl.trim()} onClick={() => void completeOAuth()}>
              完成添加
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {error ? <div className="rounded-md border border-destructive/20 bg-destructive/10 p-3 text-sm text-destructive">{error}</div> : null}

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>账户总数</CardDescription>
            <CardTitle>{accounts.length}</CardTitle>
          </CardHeader>
        </Card>
        <Card className="md:col-span-2">
          <CardHeader className="pb-2">
            <CardDescription>当前活跃账户</CardDescription>
            <CardTitle className="break-all">{active?.user_name || active?.user_id || "无"}</CardTitle>
          </CardHeader>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>账户列表</CardTitle>
          <CardDescription>可复制账户请求头，在 API 请求中指定账户。</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="py-3 pr-4 font-medium">账户</th>
                  <th className="py-3 pr-4 font-medium">账户 ID</th>
                  <th className="py-3 pr-4 font-medium">地区</th>
                  <th className="py-3 pr-4 font-medium">状态</th>
                  <th className="py-3 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((account) => (
                  <tr key={account.id} className="border-b last:border-0">
                    <td className="py-3 pr-4 font-medium">{account.user_name || account.user_id || "未命名账户"}</td>
                    <td className="max-w-[260px] truncate py-3 pr-4 font-mono text-xs text-muted-foreground">{account.id}</td>
                    <td className="py-3 pr-4">{account.country_code || "CN"}</td>
                    <td className="py-3 pr-4">{account.active ? <Badge variant="success">活跃</Badge> : <Badge variant="outline">备用</Badge>}</td>
                    <td className="py-3">
                      <div className="flex justify-end gap-2">
                        {!account.active ? (
                          <Button size="sm" variant="outline" onClick={() => void activate(account.id)}>
                            <UserRoundCheck className="h-4 w-4" />
                            设为活跃
                          </Button>
                        ) : null}
                        <Button size="sm" variant="outline" onClick={() => void copyHeader(account.id)}>
                          <Copy className="h-4 w-4" />
                          请求头
                        </Button>
                        <Button size="sm" variant="ghost" className="text-destructive" onClick={() => void remove(account)}>
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
                {!accounts.length ? (
                  <tr>
                    <td className="py-10 text-center text-muted-foreground" colSpan={5}>
                      暂无账户。点击“添加用户”开始登录。
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function KeysPage() {
  const [keys, setKeys] = useState<ApiKeyConfig[]>([])
  const [name, setName] = useState("")
  const [customKey, setCustomKey] = useState("")
  const [open, setOpen] = useState(false)
  const [error, setError] = useState("")

  async function load() {
    setError("")
    try {
      const data = await api<AdminConfig>("/admin/api/config")
      setKeys(data.api_keys)
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败")
    }
  }

  useEffect(() => {
    void load()
  }, [])

  async function createKey(random: boolean) {
    if (!name.trim()) {
      setError("Key 名称不能为空")
      return
    }
    setError("")
    try {
      await api<{ key: ApiKeyConfig }>("/admin/api/keys", {
        method: "POST",
        body: JSON.stringify({
          name: name.trim(),
          key: random ? null : customKey.trim(),
          enabled: true,
        }),
      })
      setName("")
      setCustomKey("")
      setOpen(false)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败")
    }
  }

  async function updateKey(item: ApiKeyConfig, patch: Partial<ApiKeyConfig>) {
    await api<{ key: ApiKeyConfig }>(`/admin/api/keys/${encodeURIComponent(item.name)}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    })
    await load()
  }

  async function deleteKey(item: ApiKeyConfig) {
    if (!window.confirm(`删除 API Key ${item.name}？`)) return
    await api<{ ok: boolean }>(`/admin/api/keys/${encodeURIComponent(item.name)}`, { method: "DELETE" })
    await load()
  }

  async function copyKey(value: string) {
    await navigator.clipboard.writeText(value)
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Key 管理</h1>
          <p className="text-sm text-muted-foreground">管理多个 API key。随机生成的 key 使用 `sk-` 前缀。</p>
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="h-4 w-4" />
              新增 Key
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>新增 API Key</DialogTitle>
              <DialogDescription>可以随机生成 `sk-...`，也可以填入自定义 key 内容。</DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">名称</label>
                <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="如 desktop / server" />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">自定义 Key</label>
                <Input
                  value={customKey}
                  onChange={(event) => setCustomKey(event.target.value)}
                  placeholder="留空时可点击随机生成"
                />
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setOpen(false)}>
                取消
              </Button>
              <Button variant="outline" onClick={() => void createKey(true)}>
                随机生成
              </Button>
              <Button onClick={() => void createKey(false)}>自定义添加</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      {error ? <div className="rounded-md border border-destructive/20 bg-destructive/10 p-3 text-sm text-destructive">{error}</div> : null}

      <Card>
        <CardHeader>
          <CardTitle>Key 列表</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="py-3 pr-4 font-medium">名称</th>
                  <th className="py-3 pr-4 font-medium">Key</th>
                  <th className="py-3 pr-4 font-medium">状态</th>
                  <th className="py-3 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {keys.map((item) => (
                  <tr key={item.name} className="border-b last:border-0">
                    <td className="py-3 pr-4 font-medium">{item.name}</td>
                    <td className="max-w-[420px] truncate py-3 pr-4 font-mono text-xs">{item.key}</td>
                    <td className="py-3 pr-4">{item.enabled ? <Badge variant="success">启用</Badge> : <Badge variant="outline">停用</Badge>}</td>
                    <td className="py-3">
                      <div className="flex justify-end gap-2">
                        <Button size="sm" variant="outline" onClick={() => void copyKey(item.key)}>
                          <Copy className="h-4 w-4" />
                          复制
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => void updateKey(item, { enabled: !item.enabled })}>
                          {item.enabled ? "停用" : "启用"}
                        </Button>
                        <Button size="sm" variant="ghost" className="text-destructive" onClick={() => void deleteKey(item)}>
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
                {!keys.length ? (
                  <tr>
                    <td className="py-10 text-center text-muted-foreground" colSpan={4}>
                      暂无 API Key。
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function ConfigPage() {
  const [config, setConfig] = useState<AdminConfig | null>(null)
  const [adminPassword, setAdminPassword] = useState("")
  const [error, setError] = useState("")

  async function load() {
    setError("")
    try {
      const data = await api<AdminConfig>("/admin/api/config")
      setConfig(data)
      applyTheme(data.theme)
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败")
    }
  }

  useEffect(() => {
    void load()
  }, [])

  async function save(patch: Partial<AdminConfig> & { admin_password?: string }) {
    setError("")
    try {
      const data = await api<AdminConfig>("/admin/api/config", {
        method: "PATCH",
        body: JSON.stringify(patch),
      })
      setConfig(data)
      applyTheme(data.theme)
      setAdminPassword("")
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败")
    }
  }

  if (!config) {
    return <Card className="p-6">加载中...</Card>
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">配置管理</h1>
        <p className="text-sm text-muted-foreground">设置账户分配策略、主题和管理员密码。</p>
      </div>

      {error ? <div className="rounded-md border border-destructive/20 bg-destructive/10 p-3 text-sm text-destructive">{error}</div> : null}

      <Card>
        <CardHeader>
          <CardTitle>多账户策略</CardTitle>
          <CardDescription>`轮询` 会在账户间依次分配；`粘性` 会按 API key 名称稳定映射账户。</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Button
            variant={config.account_strategy === "sticky" ? "default" : "outline"}
            onClick={() => void save({ account_strategy: "sticky" })}
          >
            粘性策略
          </Button>
          <Button
            variant={config.account_strategy === "round_robin" ? "default" : "outline"}
            onClick={() => void save({ account_strategy: "round_robin" })}
          >
            轮询策略
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>主题</CardTitle>
          <CardDescription>系统默认黑色主题，可切换白色或黑色。</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Button variant={config.theme === "dark" ? "default" : "outline"} onClick={() => void save({ theme: "dark" })}>
            <Moon className="h-4 w-4" />
            黑色
          </Button>
          <Button variant={config.theme === "light" ? "default" : "outline"} onClick={() => void save({ theme: "light" })}>
            <Sun className="h-4 w-4" />
            白色
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>管理员密码</CardTitle>
          <CardDescription>留空不会修改当前密码。</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 sm:flex-row">
          <Input
            type="password"
            value={adminPassword}
            onChange={(event) => setAdminPassword(event.target.value)}
            placeholder="新的管理员密码"
          />
          <Button disabled={!adminPassword.trim()} onClick={() => void save({ admin_password: adminPassword })}>
            <Save className="h-4 w-4" />
            保存
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}

function LogsPage() {
  const [logs, setLogs] = useState<CallLog[]>([])
  const [error, setError] = useState("")

  async function load() {
    setError("")
    try {
      const data = await api<{ logs: CallLog[] }>("/admin/api/logs?limit=300")
      setLogs(data.logs)
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败")
    }
  }

  useEffect(() => {
    void load()
  }, [])

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">调用日志</h1>
          <p className="text-sm text-muted-foreground">仅记录调用元数据，不记录消息正文。</p>
        </div>
        <Button variant="outline" onClick={() => void load()}>
          <RefreshCw className="h-4 w-4" />
          刷新
        </Button>
      </div>

      {error ? <div className="rounded-md border border-destructive/20 bg-destructive/10 p-3 text-sm text-destructive">{error}</div> : null}

      <Card>
        <CardHeader>
          <CardTitle>最近调用</CardTitle>
          <CardDescription>展示最近 300 条 `/v1/models` 和 `/v1/chat/completions` 调用。</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="py-3 pr-4 font-medium">时间</th>
                  <th className="py-3 pr-4 font-medium">接口</th>
                  <th className="py-3 pr-4 font-medium">Key</th>
                  <th className="py-3 pr-4 font-medium">模型</th>
                  <th className="py-3 pr-4 font-medium">账户</th>
                  <th className="py-3 pr-4 font-medium">状态</th>
                  <th className="py-3 pr-4 font-medium">耗时</th>
                  <th className="py-3 font-medium">错误</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => (
                  <tr key={`${log.created_at}-${log.request_id}`} className="border-b last:border-0">
                    <td className="whitespace-nowrap py-3 pr-4">{formatTime(log.created_at)}</td>
                    <td className="whitespace-nowrap py-3 pr-4">{log.endpoint}</td>
                    <td className="whitespace-nowrap py-3 pr-4">{log.api_key_name || "-"}</td>
                    <td className="py-3 pr-4">{log.model || "-"}</td>
                    <td className="max-w-[180px] truncate py-3 pr-4 font-mono text-xs text-muted-foreground">{log.account_id || "-"}</td>
                    <td className="py-3 pr-4">
                      <Badge variant={log.status_code >= 400 ? "outline" : "success"}>{log.status_code}</Badge>
                    </td>
                    <td className="whitespace-nowrap py-3 pr-4">{log.duration_ms} ms</td>
                    <td className="max-w-[320px] truncate py-3 text-muted-foreground">{log.error || "-"}</td>
                  </tr>
                ))}
                {!logs.length ? (
                  <tr>
                    <td className="py-10 text-center text-muted-foreground" colSpan={8}>
                      暂无调用日志。
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function AdminLayout({ onLogout }: { onLogout: () => void }) {
  const [page, setPage] = useState<Page>("accounts")

  useEffect(() => {
    void api<AdminConfig>("/admin/api/config").then((data) => applyTheme(data.theme))
  }, [])

  return (
    <div className="min-h-screen bg-muted/40">
      <aside className="fixed inset-y-0 left-0 z-10 hidden w-64 border-r bg-background md:block">
        <div className="flex h-14 items-center border-b px-6 font-semibold">hm-api</div>
        <nav className="space-y-1 p-3">
          <Button className="w-full justify-start" variant={page === "accounts" ? "secondary" : "ghost"} onClick={() => setPage("accounts")}>
            <Users className="h-4 w-4" />
            账户管理
          </Button>
          <Button className="w-full justify-start" variant={page === "keys" ? "secondary" : "ghost"} onClick={() => setPage("keys")}>
            <KeyRound className="h-4 w-4" />
            Key 管理
          </Button>
          <Button className="w-full justify-start" variant={page === "config" ? "secondary" : "ghost"} onClick={() => setPage("config")}>
            <Settings className="h-4 w-4" />
            配置管理
          </Button>
          <Button className="w-full justify-start" variant={page === "logs" ? "secondary" : "ghost"} onClick={() => setPage("logs")}>
            <Activity className="h-4 w-4" />
            调用日志
          </Button>
        </nav>
      </aside>
      <div className="md:pl-64">
        <header className="sticky top-0 z-10 flex h-14 items-center justify-between border-b bg-background px-4 md:px-6">
          <div className="flex gap-2 md:hidden">
            <Button size="sm" variant={page === "accounts" ? "secondary" : "outline"} onClick={() => setPage("accounts")}>
              账户
            </Button>
            <Button size="sm" variant={page === "keys" ? "secondary" : "outline"} onClick={() => setPage("keys")}>
              Key
            </Button>
            <Button size="sm" variant={page === "config" ? "secondary" : "outline"} onClick={() => setPage("config")}>
              配置
            </Button>
            <Button size="sm" variant={page === "logs" ? "secondary" : "outline"} onClick={() => setPage("logs")}>
              日志
            </Button>
          </div>
          <div className="hidden text-sm text-muted-foreground md:block">OpenAI-compatible DevEco proxy</div>
          <Button variant="ghost" onClick={onLogout}>
            <LogOut className="h-4 w-4" />
            退出
          </Button>
        </header>
        <main className="p-4 md:p-6">
          {page === "accounts" ? <AccountsPage /> : null}
          {page === "keys" ? <KeysPage /> : null}
          {page === "config" ? <ConfigPage /> : null}
          {page === "logs" ? <LogsPage /> : null}
        </main>
      </div>
    </div>
  )
}

export default function App() {
  const [ready, setReady] = useState(false)
  const [session, setSession] = useState<SessionState>({ enabled: true, authenticated: false })

  async function check() {
    const data = await api<SessionState>("/admin/api/session")
    setSession(data)
    setReady(true)
  }

  useEffect(() => {
    void check().catch(() => setReady(true))
  }, [])

  if (!ready) {
    return (
      <main className="grid min-h-screen place-items-center bg-muted/40">
        <Card className="p-6">加载中...</Card>
      </main>
    )
  }

  if (!session.enabled) {
    return (
      <main className="grid min-h-screen place-items-center bg-muted/40 px-4">
        <Card className="max-w-md">
          <CardHeader>
            <CardTitle>管理界面未启用</CardTitle>
            <CardDescription>
              启动服务时传入 <code>--admin-password</code>，或设置 <code>HM_API_ADMIN_PASSWORD</code>。
            </CardDescription>
          </CardHeader>
        </Card>
      </main>
    )
  }

  return session.authenticated ? (
    <AdminLayout onLogout={() => setSession({ enabled: true, authenticated: false })} />
  ) : (
    <LoginView onLogin={() => setSession({ enabled: true, authenticated: true })} />
  )
}

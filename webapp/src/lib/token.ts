/** token 的唯一來源:網址參數優先(訪客連結會帶),否則用 localStorage。 */
const KEY = 'maple_token'

export function getToken(): string {
  const q = new URLSearchParams(location.search).get('token')
  if (q) { localStorage.setItem(KEY, q); return q }
  return localStorage.getItem(KEY) ?? ''
}
export const setToken = (t: string) => localStorage.setItem(KEY, t)
export const clearToken = () => localStorage.removeItem(KEY)
export const hasToken = () => !!getToken()

/**
 * 股票代码规范化：用户只需输入 6 位代码，自动推断 .SH/.SZ/.BJ 后缀。
 *
 * A 股个股号段规则（成熟稳定，各行情软件通用）：
 * - 6 / 5 / 9 开头 → 上交所 (SH)：60=主板, 68=科创板, 5=沪市基金/ETF, 9=沪B
 * - 0 / 1 / 2 / 3 开头 → 深交所 (SZ)：00=主板, 30=创业板, 1=深市基金/债, 2=深B
 * - 4 / 8 开头 → 北交所 (BJ)
 *
 * 关键：**始终按前 6 位数字重新推断后缀**，忽略输入里已有的后缀。
 * 这样用户把 300001 改成 600001 时，后缀会从 SZ 实时变成 SH，
 * 而不会残留旧后缀（A 股 code 与 market 一一对应，数字唯一决定后缀）。
 */

/** 从输入提取后缀字母（SH/SZ/BJ）；无法识别时返回空串。 */
export function symbolSuffix(raw: string): string {
  const s = (raw ?? '').trim().toUpperCase()
  const m = s.match(/^(\d{6})/)
  if (!m) return ''
  const head = m[1][0]
  if (head === '6' || head === '5' || head === '9') return 'SH'
  if (head === '4' || head === '8') return 'BJ'
  return 'SZ' // 0/1/2/3
}

/**
 * 输入 → 完整代码（如 ``000001.SZ``）。
 * 前 6 位是数字 → 按数字推断后缀拼接；否则原样大写返回（交给后端校验）。
 */
export function normalizeSymbol(raw: string): string {
  const s = (raw ?? '').trim().toUpperCase()
  const m = s.match(/^(\d{6})/)
  if (!m) return s
  const code = m[1]
  return `${code}.${symbolSuffix(code)}`
}

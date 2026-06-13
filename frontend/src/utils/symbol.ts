/**
 * 股票代码规范化：用户只需输入 6 位代码，自动补全 .SH/.SZ/.BJ 后缀。
 *
 * A 股个股号段规则（成熟稳定，各行情软件通用）：
 * - 6 / 5 / 9 开头 → 上交所 (SH)：60=主板, 68=科创板, 5=沪市基金/ETF, 9=沪B
 * - 0 / 1 / 2 / 3 开头 → 深交所 (SZ)：00=主板, 30=创业板, 1=深市基金/债, 2=深B
 * - 4 / 8 开头 → 北交所 (BJ)
 *
 * 已带后缀的输入原样返回（仅大写化）；非 6 位纯数字的输入也原样返回（容错）。
 */
export function normalizeSymbol(raw: string): string {
  const s = (raw ?? '').trim().toUpperCase()
  if (!s) return s
  // 已带后缀 → 直接用
  if (/\.(SH|SZ|BJ)$/.test(s)) return s
  // 纯 6 位数字 → 按首位推断市场
  if (/^\d{6}$/.test(s)) {
    const head = s[0]
    if (head === '6' || head === '5' || head === '9') return `${s}.SH`
    if (head === '4' || head === '8') return `${s}.BJ`
    return `${s}.SZ` // 0/1/2/3
  }
  // 其它形态（带小数点但后缀非法、或长度异常）→ 原样返回，交给后端校验
  return s
}

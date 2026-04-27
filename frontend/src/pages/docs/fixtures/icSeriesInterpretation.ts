export interface IcSeriesPayload {
  dates: string[]
  values: (number | null)[]
}

export interface IcSeriesInterpretationFixture {
  good: IcSeriesPayload
  bad: IcSeriesPayload
}

const mockDates = Array.from({ length: 60 }, (_, i) => {
  // 60 个交易日，从 2025-01-02 起算工作日近似
  const base = new Date(2025, 0, 2)
  base.setDate(base.getDate() + i + Math.floor(i / 5) * 2) // 跳过周末
  return base.toISOString().slice(0, 10)
})

// 好图：日 IC 多数为正、围绕 0.04 浮动；少数日异常值在 ±0.06；累计稳定上行。
const goodValues = [
  0.045, 0.038, 0.052, 0.028, 0.041, 0.058, 0.033, 0.047, 0.025, 0.044,
  0.039, 0.051, 0.061, -0.012, 0.043, 0.029, 0.037, 0.054, 0.041, 0.018,
  0.036, 0.049, 0.027, 0.045, 0.058, 0.04, 0.022, 0.046, 0.038, 0.05,
  0.035, 0.048, -0.006, 0.043, 0.029, 0.041, 0.052, 0.038, 0.026, 0.045,
  0.042, 0.034, 0.057, 0.029, 0.04, 0.05, 0.026, 0.038, 0.045, 0.033,
  0.041, 0.048, 0.022, 0.039, 0.046, 0.034, 0.05, 0.041, 0.037, 0.044,
]

// 坏图：日 IC 在 ±0.06 间高频震荡，正负各半；累计 IC 横盘。
const badValues = [
  0.038, -0.041, 0.025, -0.033, 0.05, -0.028, -0.045, 0.04, -0.018, 0.033,
  -0.05, 0.022, 0.045, -0.036, 0.014, -0.04, 0.028, -0.022, 0.038, -0.045,
  0.018, -0.03, 0.041, -0.025, -0.04, 0.035, -0.038, 0.022, 0.045, -0.028,
  -0.035, 0.04, -0.022, 0.038, -0.045, 0.025, -0.04, 0.033, -0.018, -0.038,
  0.029, -0.045, 0.022, 0.04, -0.025, -0.038, 0.045, -0.022, 0.025, -0.04,
  0.033, -0.028, -0.045, 0.04, -0.018, 0.025, -0.038, 0.022, 0.045, -0.03,
]

export const icSeriesInterpretationFixture: IcSeriesInterpretationFixture = {
  good: { dates: mockDates, values: goodValues },
  bad: { dates: mockDates, values: badValues },
}

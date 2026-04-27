export interface TurnoverPayload {
  dates: string[]
  values: (number | null)[]
}

export interface TurnoverInterpretationFixture {
  good: TurnoverPayload
  bad: TurnoverPayload
}

const mockDates = Array.from({ length: 60 }, (_, i) => {
  const base = new Date(2025, 0, 2)
  base.setDate(base.getDate() + i + Math.floor(i / 5) * 2)
  return base.toISOString().slice(0, 10)
})

// 好图：日均 ~35%，标准差小，无明显尖峰；典型周频/低频信号特征。
const goodValues = [
  0.32, 0.35, 0.33, 0.36, 0.34, 0.31, 0.37, 0.35, 0.33, 0.38,
  0.34, 0.36, 0.32, 0.35, 0.37, 0.33, 0.34, 0.36, 0.35, 0.32,
  0.38, 0.34, 0.36, 0.33, 0.35, 0.37, 0.34, 0.36, 0.32, 0.35,
  0.34, 0.37, 0.35, 0.33, 0.36, 0.34, 0.38, 0.32, 0.35, 0.34,
  0.37, 0.36, 0.33, 0.35, 0.34, 0.36, 0.37, 0.35, 0.32, 0.34,
  0.36, 0.35, 0.33, 0.37, 0.34, 0.36, 0.35, 0.33, 0.36, 0.35,
]

// 坏图：日均 ~85%，剧烈震荡且有 95%+ 的尖峰；高频反转因子典型特征。
const badValues = [
  0.78, 0.92, 0.65, 0.88, 0.94, 0.72, 0.85, 0.96, 0.68, 0.82,
  0.95, 0.88, 0.74, 0.92, 0.85, 0.69, 0.94, 0.81, 0.97, 0.76,
  0.88, 0.92, 0.71, 0.85, 0.96, 0.79, 0.92, 0.84, 0.97, 0.73,
  0.86, 0.95, 0.82, 0.74, 0.94, 0.88, 0.71, 0.92, 0.85, 0.96,
  0.79, 0.83, 0.97, 0.72, 0.88, 0.94, 0.81, 0.76, 0.95, 0.87,
  0.92, 0.74, 0.86, 0.96, 0.82, 0.78, 0.93, 0.85, 0.97, 0.80,
]

export const turnoverInterpretationFixture: TurnoverInterpretationFixture = {
  good: { dates: mockDates, values: goodValues },
  bad: { dates: mockDates, values: badValues },
}

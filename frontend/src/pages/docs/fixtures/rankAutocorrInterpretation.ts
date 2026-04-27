export interface RankAutocorrPayload {
  dates: string[]
  values: (number | null)[]
}

export interface RankAutocorrInterpretationFixture {
  good: RankAutocorrPayload
  bad: RankAutocorrPayload
}

const mockDates = Array.from({ length: 60 }, (_, i) => {
  const base = new Date(2025, 0, 2)
  base.setDate(base.getDate() + i + Math.floor(i / 5) * 2)
  return base.toISOString().slice(0, 10)
})

// 好图：autocorr 稳定在 0.75-0.88 之间，波动小，无负值；
// 信号慢变，意味着持仓相对稳定。
const goodValues = [
  0.82, 0.85, 0.78, 0.83, 0.81, 0.86, 0.79, 0.84, 0.82, 0.85,
  0.78, 0.83, 0.86, 0.82, 0.79, 0.84, 0.81, 0.85, 0.78, 0.83,
  0.82, 0.86, 0.79, 0.84, 0.81, 0.85, 0.78, 0.83, 0.86, 0.82,
  0.79, 0.84, 0.81, 0.85, 0.78, 0.83, 0.82, 0.86, 0.79, 0.84,
  0.81, 0.85, 0.78, 0.83, 0.82, 0.86, 0.79, 0.84, 0.81, 0.85,
  0.78, 0.83, 0.82, 0.86, 0.79, 0.84, 0.81, 0.85, 0.78, 0.83,
]

// 坏图：autocorr 在 -0.2 ~ 0.5 之间剧烈跳变，常常逼近 0；
// 排名每日大幅重洗，对应高换手与短半衰期。
const badValues = [
  0.32, -0.05, 0.28, 0.45, -0.12, 0.18, 0.38, -0.08, 0.25, 0.42,
  -0.15, 0.22, 0.36, -0.02, 0.31, 0.48, -0.18, 0.15, 0.34, -0.1,
  0.28, 0.45, -0.05, 0.22, 0.38, -0.15, 0.32, 0.48, -0.08, 0.25,
  0.42, -0.12, 0.18, 0.36, -0.02, 0.28, 0.45, -0.18, 0.22, 0.38,
  -0.05, 0.32, 0.48, -0.15, 0.25, 0.42, -0.08, 0.18, 0.36, -0.12,
  0.28, 0.45, -0.02, 0.22, 0.38, -0.15, 0.32, 0.48, -0.05, 0.25,
]

export const rankAutocorrInterpretationFixture: RankAutocorrInterpretationFixture = {
  good: { dates: mockDates, values: goodValues },
  bad: { dates: mockDates, values: badValues },
}

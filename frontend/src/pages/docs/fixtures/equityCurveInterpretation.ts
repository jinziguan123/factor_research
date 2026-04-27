export interface EquityCurvePayload {
  dates: string[]
  values: (number | null)[]
}

export interface EquityCurveInterpretationFixture {
  good: EquityCurvePayload
  bad: EquityCurvePayload
}

const mockDates = Array.from({ length: 60 }, (_, i) => {
  const base = new Date(2025, 0, 2)
  base.setDate(base.getDate() + i + Math.floor(i / 5) * 2)
  return base.toISOString().slice(0, 10)
})

// 好图：从 1 起步稳定上升到 ~1.42，回撤段（如第 25-32 行）较浅且快速恢复。
const goodValues = [
  1.00, 1.012, 1.025, 1.038, 1.05, 1.058, 1.07, 1.085, 1.094, 1.105,
  1.118, 1.13, 1.142, 1.15, 1.165, 1.178, 1.19, 1.205, 1.215, 1.228,
  1.24, 1.252, 1.265, 1.275, 1.262, 1.245, 1.232, 1.225, 1.218, 1.232,
  1.245, 1.258, 1.272, 1.285, 1.298, 1.31, 1.322, 1.335, 1.348, 1.36,
  1.372, 1.358, 1.345, 1.358, 1.372, 1.385, 1.398, 1.41, 1.422, 1.408,
  1.395, 1.408, 1.422, 1.435, 1.418, 1.402, 1.418, 1.432, 1.42, 1.418,
]

// 坏图：先涨到 1.18，第 25 天起断崖式下跌至 0.78，剩余横盘震荡，最终 ~0.84。
const badValues = [
  1.00, 1.015, 1.028, 1.042, 1.055, 1.062, 1.078, 1.09, 1.105, 1.118,
  1.13, 1.142, 1.155, 1.168, 1.175, 1.18, 1.172, 1.165, 1.158, 1.15,
  1.142, 1.135, 1.128, 1.115, 1.07, 1.025, 0.98, 0.93, 0.892, 0.85,
  0.825, 0.802, 0.788, 0.78, 0.795, 0.81, 0.825, 0.808, 0.79, 0.815,
  0.832, 0.815, 0.798, 0.825, 0.842, 0.82, 0.802, 0.83, 0.848, 0.825,
  0.808, 0.835, 0.852, 0.832, 0.815, 0.838, 0.852, 0.835, 0.82, 0.84,
]

export const equityCurveInterpretationFixture: EquityCurveInterpretationFixture = {
  good: { dates: mockDates, values: goodValues },
  bad: { dates: mockDates, values: badValues },
}

export interface CorrHeatmapPayload {
  factor_ids: string[]
  values: (number | null)[][]
}

export interface CorrHeatmapInterpretationFixture {
  good: CorrHeatmapPayload
  bad: CorrHeatmapPayload
}

// 好图：5 个互补因子，对角 1.0，非对角元素绝对值多在 0.2 以下，最高 0.32。
// 这种相关性结构下，等权 / 正交合成都能带来叠加效益。
const goodFactorIds = ['momentum_n', 'reversal_n', 'realized_vol', 'turnover_ratio', 'roe_pit']
const goodValues: number[][] = [
  [1.0, -0.18, 0.05, 0.12, -0.08],
  [-0.18, 1.0, 0.22, -0.15, 0.06],
  [0.05, 0.22, 1.0, 0.32, -0.04],
  [0.12, -0.15, 0.32, 1.0, -0.11],
  [-0.08, 0.06, -0.04, -0.11, 1.0],
]

// 坏图：5 个高度相关的因子（同源动量 + ROE），非对角元素普遍 0.65+，几个达 0.85。
// 等权合成 ≈ 几乎只用一个因子，多因子设计无意义。
const badFactorIds = ['momentum_20', 'momentum_60', 'momentum_120', 'mom_skip5_20', 'mom_skip5_60']
const badValues: number[][] = [
  [1.0, 0.85, 0.72, 0.78, 0.68],
  [0.85, 1.0, 0.88, 0.74, 0.82],
  [0.72, 0.88, 1.0, 0.66, 0.86],
  [0.78, 0.74, 0.66, 1.0, 0.81],
  [0.68, 0.82, 0.86, 0.81, 1.0],
]

export const corrHeatmapInterpretationFixture: CorrHeatmapInterpretationFixture = {
  good: { factor_ids: goodFactorIds, values: goodValues },
  bad: { factor_ids: badFactorIds, values: badValues },
}

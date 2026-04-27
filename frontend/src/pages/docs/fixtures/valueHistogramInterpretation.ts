export interface ValueHistogramPayload {
  bins: number[]
  counts: number[]
}

export interface ValueHistogramInterpretationFixture {
  good: ValueHistogramPayload
  bad: ValueHistogramPayload
}

// 好图：近似正态分布 N(0, 1)；20 个 bin 从 -3 到 +3 均匀切分。
// counts 来自 ~3000 只票的 z-score 后因子值理论分布。
const goodBins = Array.from({ length: 21 }, (_, i) => -3 + i * 0.3)
const goodCounts = [
  4, 12, 28, 65, 132, 220, 332, 412, 462, 470,
  430, 348, 250, 152, 78, 32, 12, 4, 1, 0,
]

// 坏图：双峰 + 长尾。中间稀疏（因为非线性变换裂出两个 mode），右尾极厚。
const badBins = Array.from({ length: 21 }, (_, i) => -3 + i * 0.3)
const badCounts = [
  2, 8, 38, 128, 256, 312, 168, 48, 12, 35,
  98, 178, 252, 198, 132, 78, 42, 25, 18, 12,
]

export const valueHistogramInterpretationFixture: ValueHistogramInterpretationFixture = {
  good: { bins: goodBins, counts: goodCounts },
  bad: { bins: badBins, counts: badCounts },
}

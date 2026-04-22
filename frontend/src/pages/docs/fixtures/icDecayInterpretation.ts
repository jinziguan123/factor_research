export interface PeriodSeries {
  dates: string[]
  values: (number | null)[]
}

export interface IcDecayMockPayload {
  ic: Record<string, PeriodSeries>
  rankIc: Record<string, PeriodSeries>
}

export interface IcDecayInterpretationFixture {
  good: IcDecayMockPayload
  bad: IcDecayMockPayload
}

const mockDates = [
  '2025-01-03',
  '2025-01-10',
  '2025-01-17',
  '2025-01-24',
  '2025-01-31',
  '2025-02-07',
  '2025-02-14',
  '2025-02-21',
]

export const icDecayInterpretationFixture: IcDecayInterpretationFixture = {
  good: {
    ic: {
      '1': {
        dates: mockDates,
        values: [0.058, 0.053, 0.05, 0.047, 0.044, 0.041, 0.038, 0.036],
      },
      '3': {
        dates: mockDates,
        values: [0.047, 0.044, 0.042, 0.039, 0.036, 0.034, 0.032, 0.03],
      },
      '5': {
        dates: mockDates,
        values: [0.038, 0.035, 0.033, 0.031, 0.029, 0.027, 0.025, 0.024],
      },
      '10': {
        dates: mockDates,
        values: [0.028, 0.026, 0.024, 0.022, 0.021, 0.019, 0.018, 0.017],
      },
      '20': {
        dates: mockDates,
        values: [0.019, 0.018, 0.017, 0.016, 0.015, 0.014, 0.013, 0.012],
      },
    },
    rankIc: {
      '1': {
        dates: mockDates,
        values: [0.061, 0.058, 0.055, 0.052, 0.048, 0.045, 0.042, 0.04],
      },
      '3': {
        dates: mockDates,
        values: [0.051, 0.048, 0.045, 0.043, 0.04, 0.037, 0.035, 0.033],
      },
      '5': {
        dates: mockDates,
        values: [0.041, 0.039, 0.036, 0.034, 0.032, 0.03, 0.028, 0.026],
      },
      '10': {
        dates: mockDates,
        values: [0.031, 0.029, 0.027, 0.025, 0.023, 0.022, 0.021, 0.019],
      },
      '20': {
        dates: mockDates,
        values: [0.022, 0.021, 0.02, 0.019, 0.017, 0.016, 0.015, 0.014],
      },
    },
  },
  bad: {
    ic: {
      '1': {
        dates: mockDates,
        values: [0.085, -0.012, 0.062, -0.018, 0.041, -0.01, 0.028, -0.006],
      },
      '3': {
        dates: mockDates,
        values: [0.018, -0.009, 0.014, -0.011, 0.009, -0.008, 0.006, -0.01],
      },
      '5': {
        dates: mockDates,
        values: [0.007, -0.012, 0.004, -0.014, 0.003, -0.011, 0.002, -0.013],
      },
      '10': {
        dates: mockDates,
        values: [-0.004, -0.015, -0.006, -0.018, -0.005, -0.014, -0.007, -0.016],
      },
      '20': {
        dates: mockDates,
        values: [-0.011, -0.019, -0.014, -0.022, -0.013, -0.02, -0.015, -0.023],
      },
    },
    rankIc: {
      '1': {
        dates: mockDates,
        values: [0.071, -0.008, 0.055, -0.012, 0.039, -0.009, 0.025, -0.007],
      },
      '3': {
        dates: mockDates,
        values: [0.016, -0.007, 0.011, -0.009, 0.007, -0.008, 0.005, -0.009],
      },
      '5': {
        dates: mockDates,
        values: [0.006, -0.01, 0.003, -0.012, 0.002, -0.01, 0.001, -0.011],
      },
      '10': {
        dates: mockDates,
        values: [-0.003, -0.012, -0.005, -0.015, -0.004, -0.013, -0.006, -0.014],
      },
      '20': {
        dates: mockDates,
        values: [-0.009, -0.016, -0.011, -0.019, -0.01, -0.017, -0.012, -0.02],
      },
    },
  },
}

import type { GlobalThemeOverrides } from 'naive-ui'

export const binanceThemeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#F0B90B',
    primaryColorHover: '#FFD000',
    primaryColorPressed: '#D0980B',
    primaryColorSuppl: '#F0B90B',
    successColor: '#0ECB81',
    errorColor: '#F6465D',
    warningColor: '#FFD000',
    infoColor: '#1EAEDB',
    textColorBase: '#1E2026',
    textColor1: '#1E2026',
    textColor2: '#32313A',
    textColor3: '#848E9C',
    bodyColor: '#FFFFFF',
    cardColor: '#FFFFFF',
    borderColor: '#E6E8EA',
    fontFamily: 'Inter, "BinancePlex", Arial, sans-serif',
    borderRadius: '8px',
    borderRadiusSmall: '6px',
  },
  Button: {
    borderRadiusMedium: '6px',
    heightMedium: '36px',
  },
  Card: {
    borderRadius: '12px',
    paddingMedium: '20px',
  },
}

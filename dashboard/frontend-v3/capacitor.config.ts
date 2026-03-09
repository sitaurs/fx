import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'me.ecosystech.fxagent',
  appName: 'FX Agent',
  webDir: 'dist',
  server: {
    // For development: point to VPS backend
    // url: 'https://fxrv.ecosystech.me/v3',
    // For production: serve from local dist + API calls to server
    androidScheme: 'https',
  },
  plugins: {
    SplashScreen: {
      launchAutoHide: true,
      launchShowDuration: 1500,
      backgroundColor: '#0a0e17',
      showSpinner: true,
      spinnerColor: '#0ea5e9',
    },
    StatusBar: {
      style: 'DARK',
      backgroundColor: '#0a0e17',
    },
  },
  android: {
    allowMixedContent: true,
  },
};

export default config;

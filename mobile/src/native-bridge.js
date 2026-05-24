/* AI Pathshala — Capacitor native bridge.
 *
 * Loaded by the Capacitor shell BEFORE the SPA. Exposes a single
 * `window.AIPathshalaNative` object the SPA can call. When the SPA
 * is running in a browser (PWA), `AIPathshalaNative === null` and
 * features gracefully fall back to web equivalents (Web Share API,
 * Web Push, etc.).
 *
 * Bridges I3 push notifications to the native registration flow.
 * After permission grant + token receipt, we POST the token to
 * /api/users/me/push-tokens which is wired in web.py.
 */

import { Capacitor } from '@capacitor/core';
import { App } from '@capacitor/app';
import { PushNotifications } from '@capacitor/push-notifications';
import { Share } from '@capacitor/share';
import { Device } from '@capacitor/device';
import { StatusBar } from '@capacitor/status-bar';

const isNative = Capacitor.isNativePlatform();
const platform = Capacitor.getPlatform(); // 'ios' | 'android' | 'web'

async function bootstrap() {
  if (!isNative) return;

  // Lock the status bar to our brand color on cold start
  try {
    await StatusBar.setBackgroundColor({ color: '#5E60CE' });
  } catch (e) {
    console.warn('[native] status bar setup failed', e);
  }

  // Push registration — only when the user is logged in (the SPA
  // dispatches an 'auth:ready' event after successful login)
  document.addEventListener('auth:ready', registerForPush, { once: false });

  // Deep links: aipathshala://lesson/<id> or
  // https://app.aipathshala.in/lesson/<id> → SPA routes
  App.addListener('appUrlOpen', (event) => {
    if (!event.url) return;
    const url = new URL(event.url);
    window.location.hash = '#' + url.pathname + (url.search || '');
  });
}

async function registerForPush() {
  try {
    const perm = await PushNotifications.requestPermissions();
    if (perm.receive !== 'granted') return;
    await PushNotifications.register();

    PushNotifications.addListener('registration', async (token) => {
      const device = await Device.getInfo();
      await fetch('/api/users/me/push-tokens', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'Authorization': 'Bearer ' + (localStorage.getItem('token') || ''),
        },
        body: new URLSearchParams({
          platform: platform === 'ios' ? 'apns' : 'fcm',
          token: token.value,
          device_id: device.identifier || '',
          app_version: '1.4.0',
        }).toString(),
      });
    });

    PushNotifications.addListener('registrationError', (err) => {
      console.warn('[push] registration error', err);
    });

    PushNotifications.addListener('pushNotificationActionPerformed', (action) => {
      const data = action.notification?.data || {};
      if (data.log_id) {
        // Open beacon for the I3 open-rate metric
        fetch(`/api/push/${data.log_id}/opened`, { method: 'POST' });
      }
      if (data.link_url) {
        window.location.hash = '#' + data.link_url;
      }
    });
  } catch (e) {
    console.warn('[push] setup failed', e);
  }
}

// Native-bridge surface for the SPA to call
window.AIPathshalaNative = isNative ? {
  platform,
  async share({ title, text, url }) {
    try {
      await Share.share({ title, text, url, dialogTitle: 'Share lesson' });
    } catch (e) {
      console.warn('[share] failed', e);
    }
  },
  async openExternal(url) {
    const { Browser } = await import('@capacitor/browser');
    await Browser.open({ url });
  },
  async deviceInfo() {
    return Device.getInfo();
  },
} : null;

bootstrap();

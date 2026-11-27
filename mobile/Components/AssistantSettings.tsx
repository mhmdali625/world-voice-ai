import { useState } from 'react';

import {
  AccessibilityInfo,
  Pressable,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';

import { fetch } from 'expo/fetch';

import { BACKEND_URL, authHeaders, defaultBackendUrl } from '../Services/config';
import { loadSettings, normalizeServerUrl, saveSettings } from '../Services/settings';

// ========================================
// ASSISTANT SETTINGS (home screen)
// ========================================
//
// Two settings a blind user (or a helper) sets once:
//   * start the hands-free assistant automatically when the app opens;
//   * the laptop's address, for when the network changes (home Wi-Fi,
//     phone hotspot): no rebuild, no Metro restart.

export default function AssistantSettings() {
  const initial = loadSettings();
  const [autoStart, setAutoStart] = useState(initial.autoStart);
  const [address, setAddress] = useState(initial.serverUrl);
  const [token, setToken] = useState(initial.token);
  const [check, setCheck] = useState('');

  const toggleAutoStart = (value: boolean) => {
    setAutoStart(value);
    saveSettings({ autoStart: value });
    AccessibilityInfo.announceForAccessibility(
      value
        ? 'The assistant will start by itself, hands-free, when the app opens.'
        : 'Automatic start is off.'
    );
  };

  const testServer = async () => {
    const url = normalizeServerUrl(address);
    saveSettings({ serverUrl: url, token: token.trim() });
    setAddress(url);
    setCheck('Checking...');
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 4000);
      // /stream/status needs the access token when the laptop requires one.
      const response = await fetch(`${BACKEND_URL}/stream/status`, {
        signal: controller.signal, headers: authHeaders(),
      });
      clearTimeout(timer);
      const message = response.ok
        ? `Connected to ${BACKEND_URL}`
        : response.status === 401
          ? 'The laptop needs the access code: check the access code.'
          : `The server answered with an error (${response.status}).`;
      setCheck(message);
      AccessibilityInfo.announceForAccessibility(message);
    } catch {
      const message = `Cannot reach ${BACKEND_URL}. Check that the laptop is on the same network.`;
      setCheck(message);
      AccessibilityInfo.announceForAccessibility(message);
    }
  };

  return (
    <View style={styles.box}>
      <View style={styles.row}>
        <Text style={styles.label} nativeID="autoStartLabel">
          Start the assistant hands-free when the app opens
        </Text>
        <Switch
          value={autoStart}
          onValueChange={toggleAutoStart}
          accessibilityLabel="Start the assistant hands-free when the app opens"
          accessibilityLabelledBy="autoStartLabel"
          trackColor={{ true: '#0F766E', false: '#475569' }}
          thumbColor="#FFFFFF"
        />
      </View>

      <Text style={styles.label}>Server address (laptop)</Text>
      <View style={styles.row}>
        <TextInput
          style={styles.input}
          value={address}
          onChangeText={setAddress}
          placeholder={defaultBackendUrl()}
          placeholderTextColor="#94A3B8"
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
          accessibilityLabel="Server address. Leave empty to use the default."
        />
        <Pressable
          style={styles.button}
          onPress={() => { void testServer(); }}
          accessibilityRole="button"
          accessibilityLabel="Save and test the server address"
        >
          <Text style={styles.buttonText}>Test</Text>
        </Pressable>
      </View>
      <Text style={styles.label}>Access code (if the laptop uses one)</Text>
      <TextInput
        style={[styles.input, styles.single]}
        value={token}
        onChangeText={setToken}
        secureTextEntry={true}
        autoCapitalize="none"
        autoCorrect={false}
        accessibilityLabel="Access code. Leave empty if the laptop does not use one."
      />
      {check.length > 0 && (
        <Text style={styles.check} accessibilityLiveRegion="polite">{check}</Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  box: { backgroundColor: '#1E293B', borderRadius: 14, padding: 14, marginTop: 14, width: '100%' },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 8 },
  label: { color: '#FFFFFF', fontSize: 16, flex: 1, marginBottom: 6 },
  input: {
    flex: 1, backgroundColor: '#0F172A', color: '#FFFFFF', borderRadius: 10, padding: 12,
    fontSize: 16, borderWidth: 1, borderColor: '#475569', minHeight: 48,
  },
  button: {
    backgroundColor: '#5DD6C0', borderRadius: 10, paddingHorizontal: 18, minHeight: 48,
    justifyContent: 'center',
  },
  buttonText: { color: '#101827', fontSize: 16, fontWeight: 'bold' },
  check: { color: '#CBD5E1', fontSize: 14 },
  single: { flex: 0, width: '100%', marginBottom: 8 },
});

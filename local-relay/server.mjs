
import { createServer } from 'node:http';
import { networkInterfaces } from 'node:os';
import { WebSocketServer, WebSocket } from 'ws';

// ==========================================
// WORLD VOICE AI - LOCAL DEVELOPMENT SERVER
// ==========================================

const PORT = 3001;

let latestPhoto = null;

// ==========================================
// 1. AUTOMATIC LOCAL IP DETECTION
// ==========================================

// Detect laptop IP addresses automatically.
// No need to hardcode 192.168.0.107 here.
function getLocalIPs() {
  const addresses = new Set(['127.0.0.1']);
  const interfaces = networkInterfaces();

  for (const entries of Object.values(interfaces)) {
    if (!entries) continue;

    for (const item of entries) {
      if (
        item.family === 'IPv4' &&
        !item.internal
      ) {
        addresses.add(item.address);
      }
    }
  }

  return [...addresses];
}

// Only these local development ports are allowed.
const ALLOWED_PORTS = [
  3001, // Node.js relay
  5173, // Vite
  5174, // Vite alternate
  5175, // Vite alternate
  8081, // Expo Metro
  8082, // Expo Metro alternate
];

// Build the allowed origin list dynamically.
function getAllowedOrigins() {
  const origins = new Set();
  const hosts = ['localhost', ...getLocalIPs()];

  for (const host of hosts) {
    for (const port of ALLOWED_PORTS) {
      origins.add(`http://${host}:${port}`);
    }
  }

  return origins;
}

function isAllowedOrigin(origin) {
  return (
    typeof origin === 'string' &&
    getAllowedOrigins().has(origin)
  );
}

// ==========================================
// 2. HTTP SERVER
// ==========================================

const server = createServer(async (req, res) => {
  const pathname = new URL(
    req.url || '/',
    'http://localhost'
  ).pathname;

  const headers = {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
  };

  // Allow our local React dashboard.
  if (isAllowedOrigin(req.headers.origin)) {
    headers['Access-Control-Allow-Origin'] =
      req.headers.origin;

    headers['Access-Control-Allow-Methods'] =
      'GET, POST, OPTIONS';

    headers['Access-Control-Allow-Headers'] =
      'Content-Type';
  }

  function send(status, data) {
    res.writeHead(status, headers);
    res.end(JSON.stringify(data));
  }

  // Handle browser CORS preflight requests.
  if (req.method === 'OPTIONS') {
    res.writeHead(204, headers);
    res.end();
    return;
  }

  // ========================================
  // 3. HEALTH CHECK
  // ========================================

  if (
    req.method === 'GET' &&
    pathname === '/health'
  ) {
    send(200, {
      status: 'ok',
      message: 'World Voice AI relay is running',
      services: {
        photos: true,
        signaling: true,
      },
    });

    return;
  }

  // ========================================
  // 4. GET LATEST PHOTO
  // ========================================

  if (
    req.method === 'GET' &&
    pathname === '/api/photo'
  ) {
    send(200, {
      photo: latestPhoto,
    });

    return;
  }

  // ========================================
  // 5. RECEIVE PHOTO FROM ANDROID
  // ========================================

  if (
    req.method === 'POST' &&
    pathname === '/api/photo'
  ) {
    try {
      let body = '';
      let bodySize = 0;

      for await (const chunk of req) {
        bodySize += chunk.length;

        if (bodySize > 8_000_000) {
          send(413, {
            error: 'Photo is too large',
          });

          return;
        }

        body += chunk.toString();
      }

      const data = JSON.parse(body);
      const image = data.imageBase64;

      if (
        typeof image !== 'string' ||
        !/^[A-Za-z0-9+/]+={0,2}$/.test(image) ||
        image.length > 7_000_000
      ) {
        send(400, {
          error: 'Invalid image data',
        });

        return;
      }

      const bytes = Buffer.from(image, 'base64');

      // JPEG files begin with FF D8.
      if (
        bytes[0] !== 0xff ||
        bytes[1] !== 0xd8
      ) {
        send(400, {
          error: 'Expected a JPEG image',
        });

        return;
      }

      latestPhoto = {
        capturedAt: new Date().toISOString(),
        dataUrl: `data:image/jpeg;base64,${image}`,
      };

      console.log(
        `Photo received at ${latestPhoto.capturedAt}`
      );

      send(201, {
        success: true,
        message: 'Photo received successfully',
      });
    } catch (error) {
      console.error(
        'Photo upload error:',
        error instanceof Error
          ? error.message
          : error
      );

      send(400, {
        error: 'Could not process photo',
      });
    }

    return;
  }

  // Unknown HTTP route.
  send(404, {
    error: 'Route not found',
  });
});

// ==========================================
// 6. WEBRTC SIGNALING SERVER
// ==========================================

const signalingServer = new WebSocketServer({
  server,
  path: '/signal',
  maxPayload: 64 * 1024,
});

// Maximum two active peers:
// one Android phone and one browser receiver.
const peers = new Set();

signalingServer.on(
  'connection',
  (socket, request) => {
    const origin = request.headers.origin;

    console.log(
      'Incoming WebSocket Origin:',
      origin ?? '(no Origin header)'
    );

    // Recalculate accepted IPs for each connection.
    // This handles laptop DHCP address changes.
    if (
      origin &&
      !isAllowedOrigin(origin)
    ) {
      console.warn(
        'Rejected WebSocket Origin:',
        origin
      );

      console.warn(
        'Current laptop IPs:',
        getLocalIPs().join(', ')
      );

      socket.close(
        1008,
        'Origin not allowed'
      );

      return;
    }

    if (peers.size >= 2) {
      console.warn(
        'WebSocket rejected: room full'
      );

      socket.close(
        1008,
        'Room is full'
      );

      return;
    }

    // ======================================
    // 7. REGISTER CONNECTED PEER
    // ======================================

    peers.add(socket);

    // Used to detect abandoned connections.
    socket.isAlive = true;

    socket.on('pong', () => {
      socket.isAlive = true;
    });

    console.log(
      `WebRTC signaling peer connected (${peers.size}/2)`
    );

    socket.send(JSON.stringify({
      type: 'welcome',
      peers: peers.size,
    }));

    // Notify both devices when ready.
    if (peers.size === 2) {
      console.log(
        'WebRTC room ready: 2 peers connected'
      );

      for (const peer of peers) {
        if (peer.readyState === WebSocket.OPEN) {
          peer.send(JSON.stringify({
            type: 'peer-ready',
          }));
        }
      }
    }

    // ======================================
    // 8. FORWARD SIGNALING MESSAGES
    // ======================================

    socket.on('message', (raw, isBinary) => {
      if (isBinary) {
        console.warn(
          'Ignoring binary signaling message'
        );

        return;
      }

      let message;

      try {
        message = JSON.parse(raw.toString());
      } catch {
        console.warn('Invalid WebSocket JSON');
        return;
      }

      if (
        !message ||
        !['offer', 'answer', 'candidate'].includes(
          message.type
        )
      ) {
        return;
      }

      console.log(
        `Signaling message received: ${message.type}`
      );

      // Send the offer, answer, or ICE
      // candidate to the other peer.
      for (const peer of peers) {
        if (
          peer !== socket &&
          peer.readyState === WebSocket.OPEN
        ) {
          peer.send(JSON.stringify(message));
        }
      }
    });

    // ======================================
    // 9. HANDLE PEER DISCONNECTION
    // ======================================

    socket.on('close', (code, reason) => {
      peers.delete(socket);

      console.log(
        'WebRTC peer closed:',
        code,
        reason.toString() || '(no reason)'
      );

      console.log(
        `WebRTC peers remaining: ${peers.size}/2`
      );

      // Inform the remaining device.
      for (const peer of peers) {
        if (peer.readyState === WebSocket.OPEN) {
          peer.send(JSON.stringify({
            type: 'peer-left',
          }));
        }
      }
    });

    socket.on('error', (error) => {
      console.error(
        'WebSocket error:',
        error.message
      );
    });
  }
);

// ==========================================
// 10. DETECT STALE CONNECTIONS
// ==========================================

// Wi-Fi interruptions can leave old sockets
// in the room even when devices are gone.
//
// Ping each device every 10 seconds.
// If a device misses a heartbeat, remove
// its connection so it can rejoin.

const heartbeatInterval = setInterval(() => {
  for (const peer of peers) {
    if (peer.isAlive === false) {
      console.warn(
        'WebRTC peer timed out - removing stale connection'
      );

      peer.terminate();
      continue;
    }

    if (peer.readyState === WebSocket.OPEN) {
      peer.isAlive = false;

      try {
        peer.ping();
      } catch (error) {
        console.error(
          'WebSocket ping error:',
          error
        );

        peer.terminate();
      }
    }
  }
}, 10000);

heartbeatInterval.unref();

server.on('close', () => {
  clearInterval(heartbeatInterval);
});

// ==========================================
// 11. START SERVER
// ==========================================

server.on('error', (error) => {
  console.error(
    'Server error:',
    error.message
  );
});

server.listen(PORT, '0.0.0.0', () => {
  console.log('');
  console.log('================================');
  console.log(' WORLD VOICE AI - LOCAL SERVER');
  console.log('================================');

  console.log(`HTTP Port: ${PORT}`);
  console.log(
    `Health: http://localhost:${PORT}/health`
  );
  console.log(
    `Photos: http://localhost:${PORT}/api/photo`
  );
  console.log(
    `WebSocket: ws://localhost:${PORT}/signal`
  );

  console.log(
    'Detected local IPs:',
    getLocalIPs().join(', ')
  );

  console.log(
    'Origin allowlist: automatic'
  );

  console.log(
    'WebRTC stale-connection cleanup: enabled'
  );

  console.log('================================');
  console.log('Server ready!');
});

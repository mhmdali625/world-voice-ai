
import { createServer } from 'node:http';
import { networkInterfaces } from 'node:os';
import { WebSocketServer, WebSocket } from 'ws';

// ==========================================
// WORLD VOICE AI - LOCAL DEVELOPMENT SERVER
// ==========================================

const PORT = 3001;

// Photos are no longer stored in this server.
// FastAPI owns permanent blurred-photo storage.

// ==========================================
// 1. AUTOMATIC LOCAL IP DETECTION
// ==========================================

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

// Local development ports.
const ALLOWED_PORTS = [
  3001,
  5173,
  5174,
  5175,
  8081,
  8082,
];

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

  // Browser CORS preflight.
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
        photos: false,
        signaling: true,
      },
    });

    return;
  }

  // ========================================
  // 4. LEGACY PHOTO ROUTE
  // ========================================

  // Keep GET temporarily so the old
  // dashboard can check this endpoint.
  // No clear photo is returned or retained.
  if (
    req.method === 'GET' &&
    pathname === '/api/photo'
  ) {
    send(200, {
      photo: null,
    });

    return;
  }

  // Reject old photo uploads.
  // Only FastAPI handles image storage now.
  if (
    req.method === 'POST' &&
    pathname === '/api/photo'
  ) {
    send(410, {
      error: 'Photo uploads are disabled on the relay.',
      message:
        'Analyze and save photos through FastAPI instead.',
    });

    return;
  }

  send(404, {
    error: 'Route not found',
  });
});

// ==========================================
// 5. WEBRTC SIGNALING SERVER
// ==========================================

const signalingServer = new WebSocketServer({
  server,
  path: '/signal',
  maxPayload: 64 * 1024,
});

// One Android phone and one browser.
const peers = new Set();

signalingServer.on(
  'connection',
  (socket, request) => {
    const origin = request.headers.origin;

    console.log(
      'Incoming WebSocket Origin:',
      origin ?? '(no Origin header)'
    );

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
    // 6. REGISTER CONNECTED PEER
    // ======================================

    peers.add(socket);

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
    // 7. FORWARD SIGNALING MESSAGES
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
    // 8. HANDLE PEER DISCONNECTION
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
// 9. DETECT STALE CONNECTIONS
// ==========================================

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
// 10. START SERVER
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
    'Legacy photo uploads: DISABLED'
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

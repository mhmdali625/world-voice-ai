
import { createServer } from 'node:http';
import { WebSocketServer, WebSocket } from 'ws';

// ==========================================
// WORLD VOICE AI - LOCAL DEVELOPMENT SERVER
// ==========================================

const PORT = 3001;

// Store the most recently uploaded photo in memory
let latestPhoto = null;

// Allowed local dashboard origins
const allowedOrigins = new Set([
  'http://localhost:5173',
  'http://127.0.0.1:5173',
  'http://localhost:5174',
  'http://127.0.0.1:5174',
]);

// ==========================================
// 1. HTTP SERVER
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

  // Allow requests from our React dashboard
  if (allowedOrigins.has(req.headers.origin)) {
    headers['Access-Control-Allow-Origin'] =
      req.headers.origin;

    headers['Access-Control-Allow-Methods'] =
      'GET, POST, OPTIONS';

    headers['Access-Control-Allow-Headers'] =
      'Content-Type';
  }

  // Send a JSON response
  const send = (status, data) => {
    res.writeHead(status, headers);
    res.end(JSON.stringify(data));
  };

  // Handle CORS preflight requests
  if (req.method === 'OPTIONS') {
    res.writeHead(204, headers);
    res.end();
    return;
  }

  // ========================================
  // 2. HEALTH CHECK
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
  // 3. GET LATEST PHOTO
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
  // 4. RECEIVE PHOTO FROM MOBILE APP
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

        // Reject very large uploads
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

      // Validate Base64 format
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

      // Verify JPEG signature
      if (
        bytes[0] !== 0xff ||
        bytes[1] !== 0xd8
      ) {
        send(400, {
          error: 'Expected a JPEG image',
        });

        return;
      }

      // Save the latest photo in memory
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
        error.message
      );

      send(400, {
        error: 'Could not process photo',
      });
    }

    return;
  }

  // Unknown HTTP route
  send(404, {
    error: 'Route not found',
  });
});

// ==========================================
// 5. WEBRTC WEBSOCKET SIGNALING SERVER
// ==========================================

// WebSocket shares port 3001 with HTTP
const signalingServer = new WebSocketServer({
  server,
  path: '/signal',
  maxPayload: 64 * 1024,
});

// Initial experiment: maximum 2 peers
const peers = new Set();

signalingServer.on(
  'connection',
  (socket, request) => {

    const origin = request.headers.origin;

    // Restrict browser-based connections
    // Node test clients may omit the Origin header.
    if (origin && !allowedOrigins.has(origin)) {
      socket.close(1008, 'Origin not allowed');
      return;
    }

    // Restrict our experimental room to 2 peers
    if (peers.size >= 2) {
      socket.close(1008, 'Room is full');
      return;
    }

    peers.add(socket);

    console.log(
      `WebRTC signaling peer connected (${peers.size}/2)`
    );

    // Notify the new peer
    socket.send(JSON.stringify({
      type: 'welcome',
      peers: peers.size,
    }));

    // Notify the existing peer when a second joins
    if (peers.size === 2) {
      for (const peer of peers) {
        if (peer.readyState === WebSocket.OPEN) {
          peer.send(JSON.stringify({
            type: 'peer-ready',
          }));
        }
      }
    }

    // ======================================
    // 6. HANDLE SIGNALING MESSAGES
    // ======================================

    socket.on('message', (raw, isBinary) => {
      if (isBinary) return;

      let message;

      try {
        message = JSON.parse(raw.toString());
      } catch {
        console.log('Invalid WebSocket JSON');
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

      // Forward the message to the other peer
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
    // 7. HANDLE DISCONNECTION
    // ======================================

    socket.on('close', () => {
      peers.delete(socket);

      console.log(
        `WebRTC peer disconnected (${peers.size}/2)`
      );

      // Inform remaining peer
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
// 8. START THE SERVER
// ==========================================

server.on('error', (error) => {
  console.error('Server error:', error.message);
});

server.listen(PORT, '0.0.0.0', () => {
  console.log('');
  console.log('================================');
  console.log(' WORLD VOICE AI - LOCAL SERVER');
  console.log('================================');

  console.log(`HTTP Port: ${PORT}`);
  console.log(`Health: http://localhost:${PORT}/health`);
  console.log(`Photos: http://localhost:${PORT}/api/photo`);
  console.log(`WebSocket: ws://localhost:${PORT}/signal`);

  console.log('================================');
  console.log('Server ready!');
});

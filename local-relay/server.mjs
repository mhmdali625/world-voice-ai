
import { createServer } from 'node:http';

const PORT = 3001;

// Temporarily store the most recent photo in memory
let latestPhoto = null;

// Allow our local React dashboard to access the server
const allowedOrigins = new Set([
  'http://localhost:5173',
  'http://127.0.0.1:5173',
  'http://localhost:5174',
  'http://127.0.0.1:5174',
]);

const server = createServer(async (req, res) => {
  // Ignore query parameters when matching routes
  const pathname = new URL(
    req.url || '/',
    'http://localhost'
  ).pathname;

  const headers = {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
  };

  // CORS configuration
  if (allowedOrigins.has(req.headers.origin)) {
    headers['Access-Control-Allow-Origin'] =
      req.headers.origin;

    headers['Access-Control-Allow-Methods'] =
      'GET, POST, OPTIONS';

    headers['Access-Control-Allow-Headers'] =
      'Content-Type';
  }

  // Helper function for sending JSON responses
  const send = (status, data) => {
    res.writeHead(status, headers);
    res.end(JSON.stringify(data));
  };

  // Handle browser preflight requests
  if (req.method === 'OPTIONS') {
    res.writeHead(204, headers);
    res.end();
    return;
  }

  // 1. Health check
  if (req.method === 'GET' && pathname === '/health') {
    send(200, {
      status: 'ok',
      message: 'World Voice AI relay is running',
    });
    return;
  }

  // 2. Return the most recent photo
  if (
    req.method === 'GET' &&
    pathname === '/api/photo'
  ) {
    send(200, {
      photo: latestPhoto,
    });
    return;
  }

  // 3. Receive a photo from the mobile app
  if (
    req.method === 'POST' &&
    pathname === '/api/photo'
  ) {
    try {
      let body = '';

      for await (const chunk of req) {
        body += chunk.toString();

        // Reject excessively large uploads
        if (body.length > 8_000_000) {
          send(413, {
            error: 'Photo is too large',
          });
          return;
        }
      }

      const data = JSON.parse(body);
      const image = data.imageBase64;

      // Validate Base64 data
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

      // Check JPEG file signature
      if (
        bytes[0] !== 0xff ||
        bytes[1] !== 0xd8
      ) {
        send(400, {
          error: 'Expected a JPEG image',
        });
        return;
      }

      // Store the latest photo in memory
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
      console.error('Photo upload error:', error);

      send(400, {
        error: 'Could not process photo',
      });
    }

    return;
  }

  // Unknown route
  send(404, {
    error: 'Route not found',
  });
});

// Listen on the local network for mobile connections
server.listen(PORT, '0.0.0.0', () => {
  console.log(
    `World Voice AI relay running on port ${PORT}`
  );
});

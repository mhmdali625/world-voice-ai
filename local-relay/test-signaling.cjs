
const WebSocket = require('ws');

const URL = 'ws://127.0.0.1:3001/signal';

const peerA = new WebSocket(URL);
const peerB = new WebSocket(URL);

const timeout = setTimeout(() => {
  console.error('Test timed out.');
  peerA.close();
  peerB.close();
  process.exitCode = 1;
}, 7000);

// Peer A sends an offer and receives an answer
peerA.on('message', (raw) => {
  const message = JSON.parse(raw.toString());

  if (message.type === 'peer-ready') {
    console.log('Both peers connected.');

    peerA.send(JSON.stringify({
      type: 'offer',
      sdp: 'test-offer',
    }));

    console.log('Peer A sent offer.');
  }

  if (message.type === 'answer') {
    console.log('Peer A received answer.');
    console.log('SUCCESS: Signaling works between two peers!');

    clearTimeout(timeout);
    peerA.close();
    peerB.close();
  }
});

// Peer B receives the offer and responds
peerB.on('message', (raw) => {
  const message = JSON.parse(raw.toString());

  if (message.type === 'offer') {
    console.log('Peer B received offer.');

    peerB.send(JSON.stringify({
      type: 'answer',
      sdp: 'test-answer',
    }));

    console.log('Peer B sent answer.');
  }
});

peerA.on('error', console.error);
peerB.on('error', console.error);

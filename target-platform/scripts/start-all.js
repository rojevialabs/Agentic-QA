'use strict';
// Starts all five services in one terminal (no Docker needed). Ctrl+C stops them all.
const { spawn } = require('node:child_process');
const path = require('node:path');

const SERVICES = [
  ['auth-service', 4001],
  ['catalog-service', 4002],
  ['cart-service', 4003],
  ['order-service', 4004],
  ['payment-service', 4005],
];

let stopping = false;
const children = SERVICES.map(([name, port]) => {
  const child = spawn(process.execPath, ['server.js'], {
    cwd: path.join(__dirname, '..', name),
    env: { ...process.env, PORT: String(port) },
    stdio: 'inherit',
  });
  child.on('exit', (code) => {
    if (stopping) return;
    console.error(`${name} exited with code ${code}; stopping the rest`);
    stop(1);
  });
  return child;
});

function stop(code) {
  stopping = true;
  for (const child of children) child.kill();
  process.exit(code);
}

process.on('SIGINT', () => stop(0));
process.on('SIGTERM', () => stop(0));

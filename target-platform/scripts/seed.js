'use strict';
// Switch seeded changes on every ShopLite service (test/demo only).
//   npm run seed -- catalog.add_button_renamed      one or more change ids (see evaluation/seeded-bugs/registry.json)
//   npm run seed -- none                            back to the normal app
const PORTS = [4001, 4002, 4003, 4004, 4005];
const args = process.argv.slice(2);
const changes = args.length === 0 || args[0] === 'none' ? [] : args;

(async () => {
  for (const port of PORTS) {
    const res = await fetch(`http://localhost:${port}/api/__config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seeded_changes: changes }),
    });
    const body = await res.json();
    console.log(`${body.service.padEnd(8)} ${body.seeded_changes.join(', ') || '(normal)'}`);
  }
})().catch((err) => {
  console.error(`Could not reach ShopLite (${err.message}); is \`npm run start:all\` running?`);
  process.exit(1);
});

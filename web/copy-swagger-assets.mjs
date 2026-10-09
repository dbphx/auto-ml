import { cp, mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));
const source = resolve(root, 'node_modules/swagger-ui-dist');
const destination = resolve(root, '../auto_ml/static/swagger-ui');
await mkdir(destination, { recursive: true });
for (const file of ['swagger-ui-bundle.js', 'swagger-ui.css']) {
  await cp(resolve(source, file), resolve(destination, file));
}

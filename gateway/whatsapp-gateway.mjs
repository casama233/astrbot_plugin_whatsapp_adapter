import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { patchGatewayGroupNames } from "./group-name-compat.mjs";

const gatewayDir = path.dirname(fileURLToPath(import.meta.url));
const implementationPath = path.join(gatewayDir, "whatsapp-gateway-impl.mjs");
const generatedPath = path.join(gatewayDir, ".whatsapp-gateway.generated.mjs");
const source = await readFile(implementationPath, "utf8");
const patched = patchGatewayGroupNames(source);

let current = "";
try {
  current = await readFile(generatedPath, "utf8");
} catch {
  // Generated runtime file does not exist yet.
}
if (current !== patched.content) {
  await writeFile(generatedPath, patched.content, "utf8");
}

await import(pathToFileURL(generatedPath).href);

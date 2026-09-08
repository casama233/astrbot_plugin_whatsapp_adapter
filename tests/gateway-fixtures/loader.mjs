export async function resolve(specifier, context, nextResolve) {
  if (specifier === '@whiskeysockets/baileys') {
    return { url: new URL('./baileys.mjs', import.meta.url).href, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}

import { unlink } from "node:fs/promises";


/** Build the Baileys context required to recover expired media URLs. */
export function inboundMediaDownloadContext(mediaSocket, logger) {
  if (typeof mediaSocket?.updateMediaMessage !== "function") {
    throw new Error("WhatsApp media re-upload callback is unavailable");
  }
  return {
    logger,
    reuploadRequest: mediaSocket.updateMediaMessage.bind(mediaSocket),
  };
}


/** Remove an incomplete download without masking the original media error. */
export async function removePartialInboundMedia(
  filePath,
  logger,
  unlinkFile = unlink,
) {
  try {
    await unlinkFile(filePath);
  } catch (error) {
    if (error?.code !== "ENOENT") {
      logger?.debug?.({ cleanupError: error, filePath }, "failed to remove partial inbound media");
    }
  }
}

// Unlocks preview files made by previews.py: b"MSP1" + 12-byte nonce + AES-256-GCM ciphertext.
// Key = SHA-256("movie-splitter-preview:" + ntfy topic), so only someone with the topic can watch.

const MAGIC = [77, 83, 80, 49]; // "MSP1"

export async function previewKey(topic) {
  const raw = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(`movie-splitter-preview:${topic || ''}`));
  return crypto.subtle.importKey('raw', raw, { name: 'AES-GCM' }, false, ['decrypt']);
}

/** ArrayBuffer in, ArrayBuffer out. Throws {kind:'locked'} if the topic is wrong. */
export async function decryptPreview(buf, topic) {
  const b = new Uint8Array(buf);
  if (b.length < 32 || MAGIC.some((v, i) => b[i] !== v)) throw Object.assign(new Error('Not a preview file.'), { kind: 'bad' });
  const key = await previewKey(topic);
  try {
    return await crypto.subtle.decrypt({ name: 'AES-GCM', iv: b.slice(4, 16) }, key, b.slice(16));
  } catch {
    throw Object.assign(new Error("Couldn't unlock the preview. Check the ntfy topic in Settings → Connection (it must match the NTFY_TOPIC secret on GitHub)."), { kind: 'locked' });
  }
}

/** Shopify admin toast via App Bridge; silently no-ops outside the admin. */
export function showToast(message: string, isError = false) {
  try {
    window.shopify?.toast?.show(message, { isError, duration: 4000 });
  } catch {
    // App Bridge unavailable (tests, local preview).
  }
}

export async function copyText(text: string, label = 'Copied to clipboard') {
  try {
    await navigator.clipboard.writeText(text);
    showToast(label);
  } catch {
    showToast('Copy failed — select the text and copy it manually', true);
  }
}

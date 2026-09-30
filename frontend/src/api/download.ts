/**
 * Save a file fetched through the authenticated API (permission-checked by the
 * backend) without ever exposing a storage URL: the bytes arrive as a Blob and
 * are handed to the browser as a download.
 */
export async function download(fetchFile: () => Promise<Blob>, name: string) {
  const blob = await fetchFile();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

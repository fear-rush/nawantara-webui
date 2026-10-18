import { WEBUI_API_BASE_URL } from '$lib/constants';

export async function nextcloud(path: string, method = 'GET', body?: unknown, signal?: AbortSignal) {
	const response = await fetch(`${WEBUI_API_BASE_URL}/nextcloud${path}`, {
		method,
		headers: { Authorization: `Bearer ${localStorage.token}`, 'Content-Type': 'application/json' },
		body: body === undefined ? undefined : JSON.stringify(body),
		signal
	});
	const result = await response.json();
	if (!response.ok) throw new Error(result.detail || 'Request failed');
	return result;
}

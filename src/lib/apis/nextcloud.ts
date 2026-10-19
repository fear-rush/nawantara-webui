import { WEBUI_API_BASE_URL } from '$lib/constants';

export type CloudFile = { file_id: string; path: string; folder: boolean; url: string };
export type CloudText = {
	file_id: string;
	path: string;
	url: string;
	revision: string;
	text: string;
	truncated: boolean;
};
export type CloudConnection = {
	connected: boolean;
	username?: string;
	url: string;
};
export type Job = {
	id: string;
	user_id: string;
	action: string;
	status: string;
	stage: string;
	error: string;
	cancel: number;
	created: number;
	updated: number;
	result: {
		url?: string;
		saved?: boolean;
	};
};

export async function nextcloud<T = unknown>(
	path: string,
	method = 'GET',
	body?: unknown,
	signal?: AbortSignal
): Promise<T> {
	const response = await fetch(`${WEBUI_API_BASE_URL}/nextcloud${path}`, {
		method,
		cache: 'no-store',
		headers: { Authorization: `Bearer ${localStorage.token}`, 'Content-Type': 'application/json' },
		body: body === undefined ? undefined : JSON.stringify(body),
		signal
	});
	const result = await response.json();
	if (!response.ok)
		throw new Error(
			typeof result.detail === 'string' ? result.detail : 'Request failed. Try again.'
		);
	return result;
}

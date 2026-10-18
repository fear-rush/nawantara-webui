import { WEBUI_API_BASE_URL } from '$lib/constants';

export type CloudFile = { file_id: string; path: string; folder: boolean; url: string };
export type CloudSource = {
	file_id: string;
	path: string;
	url: string;
	text: string;
	score: number;
	search_mode: string;
	revision: string;
	passage: number;
	character_start: number;
	character_end: number;
};
export type CloudConnection = {
	connected: boolean;
	username?: string;
	scopes?: string[];
	url: string;
};
export type Job = {
	id: string;
	user_id: string;
	user_name?: string;
	action: string;
	status: string;
	stage: string;
	error: string;
	cancel: number;
	created: number;
	updated: number;
	progress?: Record<string, number>;
	result: {
		url?: string;
		index_job_id?: string;
		indexed?: number;
		total?: number;
		processed?: number;
		skipped?: number;
	};
};
export type JobEvent = {
	id: number;
	occurred: number;
	status: string;
	stage: string;
	details: Record<string, number>;
};
export type JobDetail = { job: Job; events: JobEvent[]; events_total: number; attempts: number };
export type Activity = {
	jobs: Job[];
	total: number;
	page: number;
	page_size: number;
	summary: Record<string, number>;
	actors: { id: string; name: string }[];
	heartbeat: number | null;
	observed_at: number;
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

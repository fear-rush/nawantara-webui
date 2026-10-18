export const ui = {
	input:
		'w-full rounded-xl bg-gray-50 dark:bg-gray-850 px-3 py-2.5 text-sm outline-hidden ring-1 ring-gray-200/60 dark:ring-gray-800 focus:ring-gray-400 dark:focus:ring-gray-600 disabled:opacity-50',
	button:
		'inline-flex items-center justify-center gap-2 rounded-xl px-3 py-2 text-sm font-medium bg-gray-100 hover:bg-gray-200 dark:bg-gray-850 dark:hover:bg-gray-800 transition disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2',
	primary:
		'inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium bg-gray-900 text-white hover:bg-gray-800 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white transition disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2',
	panel: 'rounded-2xl border border-gray-100 dark:border-gray-800 p-4 sm:p-5',
	muted: 'text-gray-500 dark:text-gray-400'
};
const names: Record<string, string> = {
	index: 'Index refresh',
	scheduled: 'Scheduled index refresh',
	event: 'Nextcloud change queued indexing',
	connection: 'Account connection requested indexing',
	followup: 'Saved file queued for indexing',
	save: 'Save to Nextcloud',
	queued: 'Queued',
	processing: 'Processing',
	completed: 'Completed',
	failed: 'Failed',
	cancelled: 'Cancelled',
	interrupted: 'Interrupted',
	starting: 'Starting',
	discovering: 'Discovering files',
	extracting: 'Extracting text / OCR',
	embedding: 'Creating embeddings',
	saving: 'Saving file',
	saved: 'File saved',
	retry_queued: 'Retry queued',
	cancellation_requested: 'Cancellation requested',
	write_rejected: 'Write rejected'
};
export const label = (value: string) => names[value] ?? value.replaceAll('_', ' ');
export const badge = (status: string) =>
	status === 'completed'
		? 'success'
		: ['failed', 'interrupted'].includes(status)
			? 'error'
			: status === 'processing'
				? 'info'
				: status === 'queued'
					? 'warning'
					: 'muted';
export const date = (seconds: number) => new Date(seconds * 1000).toLocaleString();
export function elapsed(created: number, updated: number) {
	const seconds = Math.max(0, Math.round(updated - created));
	return seconds < 60
		? `${seconds}s`
		: seconds < 3600
			? `${Math.floor(seconds / 60)}m ${seconds % 60}s`
			: `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

// Match Workspace/Knowledge controls and row density, rather than dashboard cards.
export const workspaceUi = {
	input:
		'w-full rounded-lg bg-transparent px-2 py-1.5 text-sm outline-hidden focus-visible:ring-1 focus-visible:ring-gray-400 disabled:opacity-50',
	button:
		'inline-flex items-center justify-center gap-1 rounded-lg px-2 py-1 text-xs font-normal hover:bg-gray-100 dark:hover:bg-gray-850 transition disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline focus-visible:outline-2',
	primary:
		'inline-flex items-center justify-center gap-1 rounded-full px-3 py-1.5 text-xs font-medium bg-black text-white dark:bg-white dark:text-black transition disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2',
	panel: 'rounded-xl bg-gray-50/60 dark:bg-white/[0.03] p-3',
	muted: ui.muted
};

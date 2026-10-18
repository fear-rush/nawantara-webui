<script lang="ts">
	import { onMount } from 'svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import Badge from '$lib/components/common/Badge.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import { nextcloud, type JobDetail } from '$lib/apis/nextcloud';
	import { ui, label, badge, date, elapsed } from './presentation';
	export let show = false;
	export let jobId = '';
	export let admin = false;
	let detail: JobDetail | null = null;
	let error = '';
	let loading = false;
	let controller: AbortController | null = null;
	let mounted = false;
	$: if (mounted && show && jobId) load(jobId);
	async function load(id: string) {
		controller?.abort();
		const request = new AbortController();
		controller = request;
		loading = true;
		error = '';
		if (detail?.job.id !== id) detail = null;
		try {
			const result = await nextcloud<JobDetail>(
				`${admin ? '/admin' : ''}/jobs/${encodeURIComponent(id)}`,
				'GET',
				undefined,
				request.signal
			);
			if (!request.signal.aborted) detail = result;
		} catch (e) {
			if (!request.signal.aborted) error = e instanceof Error ? e.message : 'Unable to load job';
		} finally {
			if (!request.signal.aborted) loading = false;
		}
	}
	function download() {
		if (!detail) return;
		const url = URL.createObjectURL(
			new Blob([JSON.stringify(detail, null, 2)], { type: 'application/json' })
		);
		const link = document.createElement('a');
		link.href = url;
		link.download = `processing-${jobId}.json`;
		link.click();
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	}
	onMount(() => {
		mounted = true;
		const timer = setInterval(() => {
			if (show && !loading) load(jobId);
		}, 10000);
		return () => {
			clearInterval(timer);
			controller?.abort();
		};
	});
</script>

<Modal bind:show size="md" ariaLabel="Processing job details">
	<div class="px-5 pt-5 pb-3 flex items-start justify-between gap-3">
		<div>
			<h2 class="text-lg font-semibold">
				{detail ? label(detail.job.action) : 'Processing details'}
			</h2>
			<p class="mt-1 text-xs text-gray-500 break-all">{jobId}</p>
		</div>
		<button
			class="rounded-lg p-1.5 hover:bg-gray-100 dark:hover:bg-gray-800"
			aria-label="Close job details"
			on:click={() => (show = false)}><XMark className="size-5" /></button
		>
	</div>
	<div class="max-h-[65dvh] overflow-y-auto px-5 pb-5 space-y-5">
		{#if error}<p class="text-sm text-red-600 dark:text-red-400" role="alert">{error}</p>{/if}
		{#if !detail && loading}<div class="py-10" role="status" aria-label="Loading job">
				<Spinner className="size-5" />
			</div>{/if}
		{#if detail}
			<div class="flex items-center gap-2">
				<Badge type={badge(detail.job.status)} content={label(detail.job.status)} /><span
					class="text-sm text-gray-500">{label(detail.job.stage)}</span
				>
			</div>
			<dl class="grid grid-cols-2 gap-4 text-sm">
				<div>
					<dt class={ui.muted}>Recorded attempts</dt>
					<dd class="mt-1">{detail.events_total ? detail.attempts : 'Not recorded'}</dd>
				</div>
				{#if admin}<div class="col-span-2">
						<dt class={ui.muted}>Account</dt>
						<dd class="mt-1">
							{detail.job.user_name}<span class="block text-xs text-gray-500 break-all"
								>{detail.job.user_id}</span
							>
						</dd>
					</div>{/if}
				<div>
					<dt class={ui.muted}>Created</dt>
					<dd class="mt-1">{date(detail.job.created)}</dd>
				</div>
				<div>
					<dt class={ui.muted}>Last updated</dt>
					<dd class="mt-1">{date(detail.job.updated)}</dd>
				</div>
				<div>
					<dt class={ui.muted}>Time to last update</dt>
					<dd class="mt-1">{elapsed(detail.job.created, detail.job.updated)}</dd>
				</div>
				{#each Object.entries(detail.job.progress ?? {}) as [key, value]}<div>
						<dt class="capitalize text-gray-500 dark:text-gray-400">{key}</dt>
						<dd class="mt-1 tabular-nums">{value}</dd>
					</div>{/each}
			</dl>
			{#if detail.job.error}<div
					class="rounded-xl bg-red-50 dark:bg-red-950/30 p-3 text-sm text-red-700 dark:text-red-300"
					role="alert"
				>
					{detail.job.error}
				</div>{/if}
			{#if detail.job.result.url}<a
					class="text-sm underline"
					href={detail.job.result.url}
					target="_blank"
					rel="noopener noreferrer">Open saved file</a
				>{/if}
			<div>
				<h3 class="text-sm font-semibold mb-3">Processing log</h3>
				{#if detail.events.length === 0}<p class="text-sm text-gray-500">
						No event history was recorded for this job. Its latest status is shown above.
					</p>{:else}
					<ol class="divide-y divide-gray-100 dark:divide-gray-800">
						{#each detail.events as event (event.id)}<li class="py-3 flex gap-3">
								<span class="mt-1.5 size-1.5 rounded-full bg-gray-400 shrink-0" aria-hidden="true"
								></span>
								<div class="min-w-0 flex-1">
									<div class="flex flex-wrap gap-2 justify-between">
										<p class="text-sm font-medium">{label(event.stage)}</p>
										<time
											class="text-xs text-gray-500"
											datetime={new Date(event.occurred * 1000).toISOString()}
											>{date(event.occurred)}</time
										>
									</div>
									<p class="text-xs text-gray-500 mt-1">
										{label(event.status)}{#each Object.entries(event.details) as [key, value]}<span
												class="ml-3">{label(key)}: {value}</span
											>{/each}
									</p>
								</div>
							</li>{/each}
					</ol>
					{#if detail.events_total > detail.events.length}<p class="text-xs text-gray-500">
							Showing the latest {detail.events.length} of {detail.events_total} events.
						</p>{/if}
				{/if}
			</div>
		{/if}
	</div>
	<div class="px-5 py-4 border-t border-gray-100 dark:border-gray-800 flex justify-end gap-2">
		<button class={ui.button} disabled={loading} on:click={() => load(jobId)}>Refresh</button
		><button class={ui.button} disabled={!detail} on:click={download}>Download log</button>
	</div>
</Modal>

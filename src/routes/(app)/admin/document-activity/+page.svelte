<script lang="ts">
	import { onMount } from 'svelte';
	import { nextcloud, type Activity } from '$lib/apis/nextcloud';
	import Badge from '$lib/components/common/Badge.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Pagination from '$lib/components/common/Pagination.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import JobDetail from '$lib/components/nextcloud/JobDetail.svelte';
	import { ui, label, badge, date } from '$lib/components/nextcloud/presentation';
	let data: Activity | null = null;
	let page = 1;
	let status = '';
	let action = '';
	let actor = '';
	let period = '24';
	let query = '';
	let error = '';
	let loading = false;
	let autoRefresh = true;
	let mounted = false;
	let jobId = '';
	let showDetail = false;
	let controller: AbortController | null = null;
	$: if (mounted) {
		page;
		load();
	}
	$: summary = data?.summary ?? {};
	$: cards = [
		{ title: 'All jobs', value: Object.values(summary).reduce((a, b) => a + b, 0), filter: '' },
		{
			title: 'In progress',
			value: (summary.queued ?? 0) + (summary.processing ?? 0),
			filter: 'active'
		},
		{ title: 'Completed', value: summary.completed ?? 0, filter: 'completed' },
		{
			title: 'Needs attention',
			value: (summary.failed ?? 0) + (summary.interrupted ?? 0),
			filter: 'attention'
		}
	];
	$: workerOnline = data?.heartbeat != null && data.observed_at - data.heartbeat < 60;
	async function load() {
		controller?.abort();
		const request = new AbortController();
		controller = request;
		loading = true;
		error = '';
		const params = new URLSearchParams({
			page: String(page),
			status,
			action,
			actor,
			query: query.trim(),
			since: period ? String(Date.now() / 1000 - Number(period) * 3600) : '0'
		});
		try {
			const result = await nextcloud<Activity>(
				`/admin/jobs?${params}`,
				'GET',
				undefined,
				request.signal
			);
			if (!request.signal.aborted) {
				data = result;
				page = result.page;
			}
		} catch (e) {
			if (!request.signal.aborted)
				error = e instanceof Error ? e.message : 'Unable to load activity';
		} finally {
			if (!request.signal.aborted) loading = false;
		}
	}
	function filter() {
		if (page !== 1) page = 1;
		else load();
	}
	function reset() {
		status = '';
		action = '';
		actor = '';
		period = '24';
		query = '';
		filter();
	}
	onMount(() => {
		mounted = true;
		const timer = setInterval(() => {
			if (autoRefresh && !loading && document.visibilityState === 'visible') load();
		}, 10000);
		return () => {
			controller?.abort();
			clearInterval(timer);
		};
	});
</script>

<JobDetail bind:show={showDetail} {jobId} admin />
<div class="flex-1 min-h-0 overflow-y-auto px-4 sm:px-6 pb-8 text-gray-900 dark:text-gray-100">
	<div class="max-w-7xl mx-auto space-y-5 pt-3">
		<header class="flex flex-wrap items-start justify-between gap-3">
			<div>
				<h1 class="text-xl font-semibold tracking-tight">Document Activity</h1>
				<p class="mt-1 text-sm text-gray-500 dark:text-gray-400">
					Monitor Nextcloud saves and document indexing.
				</p>
			</div>
			<div class="flex items-center gap-3">
				<label class="flex items-center gap-2 text-xs text-gray-500"
					><input
						type="checkbox"
						bind:checked={autoRefresh}
						class="accent-gray-900 dark:accent-gray-100"
					/>Auto refresh</label
				><button class={ui.button} disabled={loading} on:click={load}
					>{loading ? 'Refreshing…' : 'Refresh'}</button
				>
			</div>
		</header>
		<div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
			{#each cards as card}<button
					on:click={() => {
						status = card.filter;
						filter();
					}}
					aria-pressed={status === card.filter}
					class="rounded-2xl p-4 text-left transition ring-1 {status === card.filter
						? 'bg-gray-100 dark:bg-gray-850 ring-gray-300 dark:ring-gray-600'
						: 'bg-gray-50 dark:bg-gray-900 ring-gray-100 dark:ring-gray-800 hover:bg-gray-100 dark:hover:bg-gray-850'}"
					><span class="text-xs font-medium text-gray-500 dark:text-gray-400">{card.title}</span
					><span class="block mt-2 text-2xl font-semibold tabular-nums"
						>{data ? card.value : '…'}</span
					></button
				>{/each}
		</div>
		<div
			class="flex flex-wrap justify-between items-center gap-2 text-xs text-gray-500 dark:text-gray-400"
		>
			<div class="flex items-center gap-2">
				<Badge
					type={workerOnline ? 'success' : 'warning'}
					content={workerOnline ? 'Worker connected' : 'Heartbeat unavailable'}
				/>{#if data?.heartbeat}<span>Last heartbeat {date(data.heartbeat)}</span>{/if}
			</div>
			{#if data}<span>Updated {date(data.observed_at)}</span>{/if}
		</div>
		<form class="flex flex-wrap items-end gap-2" on:submit|preventDefault={filter}>
			<div class="grow min-w-48">
				<label for="job-query" class="sr-only">Search job ID</label>
				<div class="relative">
					<span class="absolute left-3 top-3 text-gray-400"><Search className="size-4" /></span
					><input
						id="job-query"
						class="{ui.input} pl-9"
						placeholder="Search job ID"
						bind:value={query}
						maxlength="64"
					/>
				</div>
			</div>
			<label class="text-xs text-gray-500"
				>Status<select class="{ui.input} mt-1" bind:value={status} on:change={filter}
					><option value="">All statuses</option><option value="active">In progress</option><option
						value="attention">Needs attention</option
					>{#each ['queued', 'processing', 'completed', 'failed', 'interrupted', 'cancelled'] as value}<option
							{value}>{label(value)}</option
						>{/each}</select
				></label
			>
			<label class="text-xs text-gray-500"
				>Action<select class="{ui.input} mt-1" bind:value={action} on:change={filter}
					><option value="">All actions</option><option value="index">Index refresh</option><option
						value="save">Save to Nextcloud</option
					></select
				></label
			>
			<label class="text-xs text-gray-500"
				>Account<select class="{ui.input} mt-1 max-w-52" bind:value={actor} on:change={filter}
					><option value="">All accounts</option>{#each data?.actors ?? [] as account}<option
							value={account.id}>{account.name}</option
						>{/each}</select
				></label
			>
			<label class="text-xs text-gray-500"
				>Period<select class="{ui.input} mt-1" bind:value={period} on:change={filter}
					><option value="24">Last 24 hours</option><option value="168">Last 7 days</option><option
						value="720">Last 30 days</option
					><option value="">All time</option></select
				></label
			>
			<button class={ui.button} type="submit" disabled={loading}>Apply</button><button
				class={ui.button}
				type="button"
				on:click={reset}>Reset</button
			>
		</form>
		{#if error}<div
				role="alert"
				class="rounded-xl bg-red-50 dark:bg-red-950/30 text-red-700 dark:text-red-300 p-3 text-sm"
			>
				{error} <button class="underline ml-2" on:click={load}>Try again</button>
			</div>{/if}
		<div
			class="overflow-x-auto rounded-2xl border border-gray-100 dark:border-gray-800"
			aria-busy={loading}
		>
			<table class="w-full text-left text-sm">
				<thead class="bg-gray-50 dark:bg-gray-850 text-gray-500 dark:text-gray-400"
					><tr
						>{#each ['Activity', 'Account', 'Status', 'Stage', 'Last updated', ''] as heading}<th
								scope="col"
								class="px-4 py-3 font-medium whitespace-nowrap">{heading || 'Details'}</th
							>{/each}</tr
					></thead
				>
				<tbody class="divide-y divide-gray-100 dark:divide-gray-800">
					{#if !data && loading}<tr
							><td colspan="6" class="py-14"
								><div role="status" aria-label="Loading activity">
									<Spinner className="size-5" />
								</div></td
							></tr
						>
					{:else if data?.jobs.length}{#each data.jobs as job (job.id)}<tr
								class="hover:bg-gray-50/70 dark:hover:bg-gray-850/50 transition"
								><td class="px-4 py-3"
									><span class="font-medium whitespace-nowrap">{label(job.action)}</span><span
										class="block font-mono text-xs text-gray-400 mt-1"
										title={job.id}>{job.id.slice(0, 10)}</span
									></td
								><td class="px-4 py-3"
									><span class="block max-w-40 truncate" title={job.user_name}>{job.user_name}</span
									></td
								><td class="px-4 py-3"
									><Badge type={badge(job.status)} content={label(job.status)} /></td
								><td class="px-4 py-3 text-gray-500 dark:text-gray-400"
									><span class="whitespace-nowrap">{label(job.stage)}</span
									>{#if job.progress?.total !== undefined}<span class="block text-xs mt-1"
											>{job.progress.processed ?? 0} / {job.progress.total} files checked</span
										>{/if}</td
								><td class="px-4 py-3 text-xs text-gray-500 whitespace-nowrap"
									>{date(job.updated)}</td
								><td class="px-4 py-3"
									><button
										class={ui.button}
										aria-label={`View ${label(job.action).toLowerCase()} ${job.id}`}
										on:click={() => {
											jobId = job.id;
											showDetail = true;
										}}>View log</button
									></td
								></tr
							>{/each}
					{:else}<tr
							><td colspan="6" class="px-4 py-14 text-center"
								><p class="font-medium">No activity matches these filters</p>
								<p class="text-sm text-gray-500 mt-1">
									Change the period or reset the filters.
								</p></td
							></tr
						>{/if}
				</tbody>
			</table>
		</div>
		{#if data}<div class="flex flex-wrap justify-between items-center gap-3">
				<p class="text-xs text-gray-500">{data.total} jobs matching filters</p>
				<Pagination bind:page count={data.total} perPage={data.page_size} />
			</div>{/if}
		<details class="text-xs text-gray-500 dark:text-gray-400">
			<summary class="cursor-pointer w-fit">What is included in this log?</summary>
			<p class="mt-2 max-w-3xl leading-relaxed">
				WebUI save and indexing jobs, processing stages, counts and safe errors. File edits, shares
				and deletions performed in Nextcloud are recorded there. Private filenames, document text
				and credentials are excluded from this panel. Only job owners can retry or cancel their
				jobs.
			</p>
		</details>
	</div>
</div>
